# kritomatic_nodes/PixArt/nodes.py
#
# ComfyUI node wrappers for `fumeisama/drawatoon-v1`.
#
# Only drawatoon-specific nodes are registered here — the stock ExtraModels
# PixArt nodes (`PixArtCheckpointLoader`, `PixArtResolutionSelect`,
# `PixArtLoraLoader`, ...) are intentionally not duplicated in this pack, so
# this file and `ComfyUI_ExtraModels` can be installed side-by-side without
# clobbering each other's node names.
#
# Nodes:
#   * DrawatoonCheckpointLoader — loads drawatoon as a DrawatoonMS MODEL
#   * DrawatoonSetIP            — attaches ip_adapter conditioning to a
#                                 CONDITIONING, so cond and uncond can carry
#                                 *different* ip tokens (needed for CFG to
#                                 amplify layout rather than cancel it).

import json

import torch
import folder_paths

from .conf import pixart_conf
from .loader import load_pixart


# ---------------------------------------------------------------------------
# DrawatoonCheckpointLoader
# ---------------------------------------------------------------------------
class DrawatoonCheckpointLoader:
    """
    Loader for `fumeisama/drawatoon-v1` (and future drawatoon fine-tunes).

    Uses the stock `PixArtMS_Sigma_XL_2` config but instantiates `DrawatoonMS`
    (PixArt-Sigma + IPAdapter + per-block parallel ip cross-attention) so the
    `ip_adapter.*` and `transformer_blocks.N.ip_attn.*` weights load into the
    right modules.

    Text-only generation works with no extra nodes thanks to the void-embed
    fallback inside `DrawatoonMS.forward_raw`. Layout conditioning is wired
    through `DrawatoonSetIP` (below) — one node per CONDITIONING branch.

    IMPORTANT: drawatoon is a **512px model**. Panels were trained at a fixed
    262,144-pixel budget. Use `PixArtResolutionSelect` with
    `model = "PixArtMS_Sigma_XL_2_512"` — its preset table keeps the area
    near 262,144 for any aspect ratio. 1024x1024 will produce blocky output
    because it is 4x the pixel budget the model ever saw.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "ckpt_name": (folder_paths.get_filename_list("checkpoints"),),
            }
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "load_checkpoint"
    CATEGORY = "ExtraModels/PixArt"
    TITLE = "Drawatoon Checkpoint Loader"

    def load_checkpoint(self, ckpt_name):
        from .models.drawatoon import DrawatoonMS

        ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
        model = load_pixart(
            model_path=ckpt_path,
            model_conf=pixart_conf["PixArtMS_Sigma_XL_2"],
            model_class=DrawatoonMS,
        )
        return (model,)


# ---------------------------------------------------------------------------
# Helpers for DrawatoonSetIP
# ---------------------------------------------------------------------------
def _get_ip_adapter(model):
    """Extract the IPAdapter from a MODEL produced by DrawatoonCheckpointLoader."""
    try:
        # model is a ModelPatcher wrapping EXM_PixArt_Model -> DrawatoonMS
        inner = model.model
        dm = inner.diffusion_model
        return dm.ip_adapter
    except AttributeError:
        return None


def _parse_bboxes(s):
    """Parse a JSON string of bboxes: `[[x1,y1,x2,y2], ...]` (normalized 0..1)."""
    if not s or not s.strip():
        return []
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid bbox JSON: {e}")
    if not isinstance(data, list):
        raise ValueError("bboxes JSON must be a list of [x1,y1,x2,y2]")
    out = []
    for item in data:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            raise ValueError(f"each bbox must be [x1,y1,x2,y2], got {item!r}")
        out.append([float(v) for v in item])
    return out


def _parse_reference_embeddings(s):
    """Parse a JSON string of references: `[null | [768 floats], ...]`."""
    if not s or not s.strip():
        return []
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid reference embeddings JSON: {e}")
    if not isinstance(data, list):
        raise ValueError("reference_embeddings JSON must be a list")
    out = []
    for item in data:
        if item is None:
            out.append(None)
        elif isinstance(item, list):
            out.append(torch.tensor(item, dtype=torch.float32))
        else:
            raise ValueError(
                f"each ref must be null or a list of floats, got {type(item)}"
            )
    return out


# ---------------------------------------------------------------------------
# DrawatoonSetIP
# ---------------------------------------------------------------------------
class DrawatoonSetIP:
    """
    Attach IP-adapter conditioning to a CONDITIONING.

    Use this on **both** positive and negative branches feeding KSampler so
    that cond and uncond pass carry different `ip_hidden_states`. Without it,
    both passes see the same tokens and the ip branch is identical in both,
    cancelling out in the CFG delta — layout then barely affects output.

    Wiring (typical):
        CLIPTextEncode (positive) -> DrawatoonSetIP(ip_mode="layout")    -> KSampler.positive
        CLIPTextEncode (negative) -> DrawatoonSetIP(ip_mode="negative")  -> KSampler.negative
        DrawatoonCheckpointLoader -> MODEL -> both DrawatoonSetIP nodes

    ip_mode:
      - "layout"    : build ip tokens from text_bboxes_json /
                      character_bboxes_json / reference_embeddings_json
                      via the model's IPAdapter. If the JSON arrays are
                      empty, falls back to the same default layout as
                      DrawatoonMS._default_layout (1 caption box at top,
                      1 character box in centre).
      - "negative"  : use the trained `negative_ip_embed` (the author's
                      CFG drop-out token). This is the correct value for
                      the negative branch.
      - "void"      : use the trained `void_ip_embed` (no-layout token).
                      Useful for A/B testing against "layout".

    BBOX FORMAT (JSON string, normalized 0..1, `[x1,y1,x2,y2]` per box):
        text_bboxes_json       = '[[0.10, 0.02, 0.90, 0.12]]'
        character_bboxes_json  = '[[0.30, 0.15, 0.70, 0.90]]'
        reference_embeddings_json = '[null]'  (null = let model invent character)

    LIMITATIONS:
      * Each layout token contributes 2 (text) or 5 (character) tokens.
        Match the number of character boxes to the length of
        `reference_embeddings_json` — extra references are ignored, missing
        ones are padded with `null` (model invents).
      * Batch size > 1 is only partially supported (the model broadcasts the
        single-layout ip tokens across the batch, but ExtraModels'
        block-diagonal mask has a known B>1 quirk that this pack does not
        work around). Use batch_size = 1.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "conditioning": ("CONDITIONING",),
                "ip_mode": (["layout", "negative", "void"], {"default": "layout"}),
            },
            "optional": {
                "text_bboxes_json": (
                    "STRING",
                    {"multiline": True, "default": "[]",
                     "tooltip": "JSON list of [x1,y1,x2,y2] normalized (0..1)."},
                ),
                "character_bboxes_json": (
                    "STRING",
                    {"multiline": True, "default": "[]",
                     "tooltip": "JSON list of [x1,y1,x2,y2] normalized (0..1)."},
                ),
                "reference_embeddings_json": (
                    "STRING",
                    {"multiline": True, "default": "[]",
                     "tooltip": "JSON list of null or 768-float vectors, one per character."},
                ),
            },
        }

    RETURN_TYPES = ("CONDITIONING",)
    RETURN_NAMES = ("conditioning",)
    FUNCTION = "apply"
    CATEGORY = "ExtraModels/PixArt"
    TITLE = "Drawatoon Set IP Conditioning"

    def apply(
        self,
        model,
        conditioning,
        ip_mode,
        text_bboxes_json="[]",
        character_bboxes_json="[]",
        reference_embeddings_json="[]",
    ):
        ip_adapter = _get_ip_adapter(model)
        if ip_adapter is None:
            raise RuntimeError(
                "DrawatoonSetIP: MODEL does not contain a DrawatoonMS with "
                "ip_adapter. Load the checkpoint with Drawatoon Checkpoint "
                "Loader."
            )

        if ip_mode == "layout":
            text_bboxes = _parse_bboxes(text_bboxes_json)
            character_bboxes = _parse_bboxes(character_bboxes_json)
            reference_embeddings = _parse_reference_embeddings(
                reference_embeddings_json
            )

            # Empty = same default as DrawatoonMS._default_layout
            if not text_bboxes and not character_bboxes:
                text_bboxes = [[0.10, 0.02, 0.90, 0.12]]
                character_bboxes = [[0.30, 0.15, 0.70, 0.90]]
                reference_embeddings = [None]

            # Pad references to match character count
            while len(reference_embeddings) < len(character_bboxes):
                reference_embeddings.append(None)

            # Cast references to ref_proj's dtype/device (may be CPU if model
            # is currently offloaded; forward_raw will move them to x.device).
            target_dtype = ip_adapter.ref_embedding_proj.weight.dtype
            target_device = ip_adapter.ref_embedding_proj.weight.device
            reference_embeddings = [
                r.to(dtype=target_dtype, device=target_device)
                if r is not None else None
                for r in reference_embeddings
            ]

            with torch.no_grad():
                ip_hidden_states, ip_attention_mask = ip_adapter(
                    [text_bboxes],
                    [character_bboxes],
                    [reference_embeddings],
                    False,
                )

        elif ip_mode == "negative":
            ip_hidden_states = ip_adapter.negative_ip_embed.weight.unsqueeze(0)
            ip_attention_mask = torch.ones(
                1, 1, dtype=torch.bool, device=ip_hidden_states.device
            )

        elif ip_mode == "void":
            ip_hidden_states = ip_adapter.void_ip_embed.weight.unsqueeze(0)
            ip_attention_mask = torch.ones(
                1, 1, dtype=torch.bool, device=ip_hidden_states.device
            )

        else:
            raise ValueError(f"Unknown ip_mode: {ip_mode!r}")

        # Attach to every cond entry's metadata (a single CONDITIONING can
        # contain multiple entries after ConditioningCombine, etc.).
        out = []
        for cond_tensor, cond_meta in conditioning:
            new_meta = dict(cond_meta)
            new_meta["ip_hidden_states"] = ip_hidden_states
            new_meta["ip_attention_mask"] = ip_attention_mask
            out.append([cond_tensor, new_meta])
        return (out,)


NODE_CLASS_MAPPINGS = {
    "DrawatoonCheckpointLoader": DrawatoonCheckpointLoader,
    "DrawatoonSetIP": DrawatoonSetIP,
}