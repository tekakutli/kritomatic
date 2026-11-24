# kritomatic_nodes/PixArt/nodes.py
#
# ComfyUI node wrappers for `fumeisama/drawatoon-v1`.
#
# Only drawatoon-specific nodes are registered here — the stock
# ExtraModels PixArt nodes are intentionally not duplicated in this
# pack, so this file and `ComfyUI_ExtraModels` can be installed
# side-by-side without clobbering each other's node names.
#
# Nodes:
#   * DrawatoonCheckpointLoader — loads drawatoon as a DrawatoonMS MODEL
#   * DrawatoonSetIP            — attaches ip_adapter conditioning to a
#                                 CONDITIONING, so cond and uncond can
#                                 carry *different* ip tokens (needed
#                                 for CFG to amplify layout rather than
#                                 cancel it).  Fetches bboxes from the
#                                 layout playground at
#                                 http://127.0.0.1:8777/state, or falls
#                                 back to void_ip_embed when the
#                                 playground is unreachable.

import json
import urllib.error
import urllib.request

import torch
import folder_paths

from .conf import pixart_conf
from .loader import load_pixart
from pgruntime.ports import url_for


_LAYOUT_STATE_URL   = url_for("layout", "/state")
_LAYOUT_VERSION_URL = url_for("layout", "/version")
_LAYOUT_FETCH_TIMEOUT_S = 2.0


# ---------------------------------------------------------------------------
# DrawatoonCheckpointLoader
# ---------------------------------------------------------------------------
class DrawatoonCheckpointLoader:
    """
    Loader for `fumeisama/drawatoon-v1` (and future drawatoon fine-tunes).

    Uses the stock `PixArtMS_Sigma_XL_2` config but instantiates
    `DrawatoonMS` (PixArt-Sigma + IPAdapter + per-block parallel ip
    cross-attention) so the `ip_adapter.*` and
    `transformer_blocks.N.ip_attn.*` weights load into the right
    modules.

    Text-only generation works with no extra nodes thanks to the
    void-embed fallback inside `DrawatoonMS.forward_raw`.

    IMPORTANT: drawatoon is a **512px model**. Panels were trained at a
    fixed 262,144-pixel budget.  Use `PixArtResolutionSelect` with
    `model = "PixArtMS_Sigma_XL_2_512"`.  1024x1024 will produce blocky
    output because it is 4x the pixel budget the model ever saw.
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
# Helpers
# ---------------------------------------------------------------------------
def _get_ip_adapter(model):
    """Extract the IPAdapter from a MODEL produced by DrawatoonCheckpointLoader."""
    try:
        inner = model.model
        dm = inner.diffusion_model
        return dm.ip_adapter
    except AttributeError:
        return None


def _post_json(url, body, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _probe_version():
    """Best-effort GET of the layout playground's state hash.

    Returns "" when the playground is unreachable, or its string
    version when reachable.  Used by IS_CHANGED.
    """
    try:
        r = _post_json(_LAYOUT_VERSION_URL, {}, timeout=1.0)
    except Exception:
        return ""
    v = r.get("version") if isinstance(r, dict) else None
    return str(v) if v is not None else ""


def _fetch_layout():
    """Fetch bboxes from the layout playground.

    Returns (text_bboxes, character_bboxes, refs_raw) on success, or
    ([], [], []) on any failure (timeout, connection refused, non-JSON
    body).  The empty triple is exactly the void_ip_embed fallback path
    in the caller.  No warning is printed: playground-down is a
    supported state (generation proceeds as if no layout existed).
    """
    try:
        state = _post_json(
            _LAYOUT_STATE_URL, {}, timeout=_LAYOUT_FETCH_TIMEOUT_S,
        )
    except Exception:
        return [], [], []

    if not isinstance(state, dict):
        return [], [], []

    def _bboxes(key):
        raw = state.get(key) or []
        out = []
        for b in raw:
            if not isinstance(b, (list, tuple)) or len(b) != 4:
                continue
            try:
                out.append([float(v) for v in b])
            except (TypeError, ValueError):
                continue
        return out

    text_bboxes = _bboxes("text_bboxes")
    character_bboxes = _bboxes("character_bboxes")
    refs_raw = state.get("reference_embeddings") or []
    return text_bboxes, character_bboxes, refs_raw


def _build_references(refs_raw, n_characters, ip_adapter):
    """Coerce references to the IPAdapter's dtype/device, one per character."""
    target_dtype = ip_adapter.ref_embedding_proj.weight.dtype
    target_device = ip_adapter.ref_embedding_proj.weight.device
    refs = []
    for r in refs_raw:
        if isinstance(r, list) and len(r) == 768:
            refs.append(torch.tensor(
                r, dtype=target_dtype, device=target_device,
            ))
        else:
            refs.append(None)
    while len(refs) < n_characters:
        refs.append(None)
    if len(refs) > n_characters:
        refs = refs[:n_characters]
    return refs


# ---------------------------------------------------------------------------
# DrawatoonSetIP
# ---------------------------------------------------------------------------
class DrawatoonSetIP:
    """
    Attach IP-adapter conditioning to a CONDITIONING.

    Use this on **both** positive and negative branches feeding
    KSampler so that cond and uncond pass carry different
    `ip_hidden_states`.  Without it, both passes see the same tokens
    and the ip branch is identical in both, cancelling out in the CFG
    delta — layout then barely affects output.

    Wiring (typical):
        CLIPTextEncode (positive) -> DrawatoonSetIP(ip_mode="layout")    -> KSampler.positive
        CLIPTextEncode (negative) -> DrawatoonSetIP(ip_mode="negative")  -> KSampler.negative
        DrawatoonCheckpointLoader -> MODEL -> both DrawatoonSetIP nodes

    ip_mode:
      - "layout"    : probe http://127.0.0.1:8777/state for bboxes.
                      If the playground is unreachable, or returns empty
                      bboxes, fall back to `void_ip_embed` (identical
                      to "void").  Silent — playground-down is a
                      supported state.
      - "negative"  : use the trained `negative_ip_embed` (the author's
                      CFG drop-out token).  Never contacts playground.
      - "void"      : use the trained `void_ip_embed` (no-layout token).
                      Never contacts playground.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "conditioning": ("CONDITIONING",),
                "ip_mode": (["layout", "negative", "void"],
                            {"default": "layout"}),
            },
        }

    RETURN_TYPES = ("CONDITIONING",)
    RETURN_NAMES = ("conditioning",)
    FUNCTION = "apply"
    CATEGORY = "ExtraModels/PixArt"
    TITLE = "Drawatoon Set IP Conditioning"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # Return the layout playground's state hash; "" when down.
        # ComfyUI re-executes when the returned value differs from the
        # previous call's return.
        return _probe_version()

    def apply(self, model, conditioning, ip_mode):
        ip_adapter = _get_ip_adapter(model)
        if ip_adapter is None:
            raise RuntimeError(
                "DrawatoonSetIP: MODEL does not contain a DrawatoonMS "
                "with ip_adapter. Load the checkpoint with Drawatoon "
                "Checkpoint Loader."
            )

        if ip_mode == "layout":
            text_bboxes, character_bboxes, refs_raw = _fetch_layout()
            if not text_bboxes and not character_bboxes:
                # Playground unreachable, or has empty bboxes.  Void.
                ip_hidden_states = (
                    ip_adapter.void_ip_embed.weight.unsqueeze(0)
                )
                ip_attention_mask = torch.ones(
                    1, 1, dtype=torch.bool,
                    device=ip_hidden_states.device,
                )
            else:
                references = _build_references(
                    refs_raw, len(character_bboxes), ip_adapter,
                )
                with torch.no_grad():
                    ip_hidden_states, ip_attention_mask = ip_adapter(
                        [text_bboxes], [character_bboxes], [references],
                        False,
                    )

        elif ip_mode == "negative":
            ip_hidden_states = (
                ip_adapter.negative_ip_embed.weight.unsqueeze(0)
            )
            ip_attention_mask = torch.ones(
                1, 1, dtype=torch.bool, device=ip_hidden_states.device,
            )

        elif ip_mode == "void":
            ip_hidden_states = (
                ip_adapter.void_ip_embed.weight.unsqueeze(0)
            )
            ip_attention_mask = torch.ones(
                1, 1, dtype=torch.bool, device=ip_hidden_states.device,
            )

        else:
            raise ValueError(f"Unknown ip_mode: {ip_mode!r}")

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
