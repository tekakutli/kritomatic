"""Drawatoon-specific nodes.

`DrawatoonCheckpointLoader` loads the drawatoon transformer as a
ComfyUI MODEL.  `DrawatoonSetIP` attaches IP-adapter conditioning to a
CONDITIONING by reading bboxes from the layout playground's `/state`.

Both nodes are V3 `io.ComfyNode` subclasses, discovered by
`KritomaticExtension` in `kritomatic_nodes/__init__.py`.
"""

from __future__ import annotations

import torch
import folder_paths

from comfy_api.latest import io

from .layout_client import fetch_state_dict, probe_version


def _get_ip_adapter(model):
    try:
        return model.model.diffusion_model.ip_adapter
    except AttributeError:
        return None


def _fetch_layout():
    """Parse `text_bboxes` / `character_bboxes` / `reference_embeddings`
    from the playground state.  Returns `([], [], [])` when unreachable
    or when any bbox is malformed."""
    state = fetch_state_dict()
    if not state:
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


class DrawatoonCheckpointLoader(io.ComfyNode):
    """
    Loads `fumeisama/drawatoon-v1` (or any fine-tune with the same
    architecture) as a ComfyUI MODEL.

    drawatoon is a 512px model.  Use `PlaygroundResolutionSelect` (or
    `PixArtResolutionSelect` with `PixArtMS_Sigma_XL_2_512`) — the
    1024 pixel budget will produce blocky output because the model
    never saw that many latent tokens.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DrawatoonCheckpointLoader",
            display_name="Drawatoon Checkpoint Loader",
            category="ExtraModels/PixArt",
            inputs=[
                io.Combo.Input(
                    "ckpt_name",
                    options=folder_paths.get_filename_list("checkpoints"),
                    tooltip="Select a drawatoon checkpoint from models/checkpoints.",
                ),
            ],
            outputs=[
                io.Model.Output("model"),
            ],
        )

    @classmethod
    def execute(cls, ckpt_name: str) -> io.NodeOutput:
        from .PixArt.models.drawatoon import DrawatoonMS
        from .PixArt.conf import pixart_conf
        from .PixArt.loader import load_pixart

        ckpt_path = folder_paths.get_full_path("checkpoints", ckpt_name)
        model = load_pixart(
            model_path=ckpt_path,
            model_conf=pixart_conf["PixArtMS_Sigma_XL_2"],
            model_class=DrawatoonMS,
        )
        return io.NodeOutput(model)


class DrawatoonSetIP(io.ComfyNode):
    """
    Attach IP-adapter conditioning to a CONDITIONING.

    Wire one of these on the positive branch with `ip_mode="layout"` and
    one on the negative branch with `ip_mode="negative"`.  That gives
    the CFG delta real signal on both sides — the trained layout
    bboxes on cond, the trained CFG drop-out token on uncond.

    `ip_mode="layout"` fetches bboxes from the layout playground's
    `/state`.  If the playground is unreachable, or returns empty
    bboxes, the node falls back to `void_ip_embed` (the trained
    no-layout token) — which is exactly what CFG subtraction needs on
    an unwired branch.  Silent: playground-down is a supported state.
    """

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="DrawatoonSetIP",
            display_name="Drawatoon Set IP Conditioning",
            category="ExtraModels/PixArt",
            inputs=[
                io.Model.Input("model"),
                io.Conditioning.Input("conditioning"),
                io.Combo.Input(
                    "ip_mode",
                    options=["layout", "negative", "void"],
                    default="layout",
                    tooltip="layout = fetch bboxes from the playground; "
                            "negative = trained CFG drop-out token; "
                            "void = trained no-layout token.",
                ),
            ],
            outputs=[
                io.Conditioning.Output("conditioning"),
            ],
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return probe_version()

    @classmethod
    def execute(cls, model, conditioning, ip_mode: str) -> io.NodeOutput:
        ip_adapter = _get_ip_adapter(model)
        if ip_adapter is None:
            raise RuntimeError(
                "DrawatoonSetIP: MODEL does not contain a DrawatoonMS with "
                "ip_adapter. Load the checkpoint with Drawatoon Checkpoint Loader."
            )

        if ip_mode == "layout":
            text_bboxes, character_bboxes, refs_raw = _fetch_layout()
            if not text_bboxes and not character_bboxes:
                ip_hidden_states = ip_adapter.void_ip_embed.weight.unsqueeze(0)
                ip_attention_mask = torch.ones(
                    1, 1, dtype=torch.bool, device=ip_hidden_states.device,
                )
            else:
                target_dtype = ip_adapter.ref_embedding_proj.weight.dtype
                target_device = ip_adapter.ref_embedding_proj.weight.device
                references = []
                for r in refs_raw:
                    if isinstance(r, list) and len(r) == 768:
                        references.append(torch.tensor(
                            r, dtype=target_dtype, device=target_device,
                        ))
                    else:
                        references.append(None)
                while len(references) < len(character_bboxes):
                    references.append(None)
                if len(references) > len(character_bboxes):
                    references = references[:len(character_bboxes)]

                with torch.no_grad():
                    ip_hidden_states, ip_attention_mask = ip_adapter(
                        [text_bboxes], [character_bboxes], [references], False,
                    )

        elif ip_mode == "negative":
            ip_hidden_states = ip_adapter.negative_ip_embed.weight.unsqueeze(0)
            ip_attention_mask = torch.ones(
                1, 1, dtype=torch.bool, device=ip_hidden_states.device,
            )

        elif ip_mode == "void":
            ip_hidden_states = ip_adapter.void_ip_embed.weight.unsqueeze(0)
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

        return io.NodeOutput(out)