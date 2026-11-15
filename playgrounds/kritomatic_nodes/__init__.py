# kritomatic_nodes/__init__.py
"""
Kritomatic Nodes — all nodes registered through the V3 ComfyExtension API.

There is no NODE_CLASS_MAPPINGS in this module. The loader sees
comfy_entrypoint, calls it, and gets every node from the returned
extension's get_node_list(). That is the only registration route used.

Nodes exposed:
  * DrawatoonCheckpointLoader   (MODEL)
  * DrawatoonSetIP              (CONDITIONING)
  * MultiAreaConditioning       (CONDITIONING)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import torch
import folder_paths

from comfy_api.latest import ComfyExtension, io

# ---------------------------------------------------------------------------
# Re-exported helpers from multimask/ (MultiAreaConditioning lives there).
# ---------------------------------------------------------------------------
from .multimask import MultiAreaConditioning


# ---------------------------------------------------------------------------
# DrawatoonCheckpointLoader — V3
# ---------------------------------------------------------------------------
class DrawatoonCheckpointLoader(io.ComfyNode):
    """
    Loader for `fumeisama/drawatoon-v1` (and future drawatoon fine-tunes).

    Uses the stock PixArtMS_Sigma_XL_2 config but instantiates
    DrawatoonMS (PixArt-Sigma + IPAdapter + per-block parallel ip
    cross-attention) so the ip_adapter.* and
    transformer_blocks.N.ip_attn.* weights load into the right modules.

    IMPORTANT: drawatoon is a 512px model. Panels were trained at a
    fixed 262,144-pixel budget. Use PixArtResolutionSelect with
    model = "PixArtMS_Sigma_XL_2_512". 1024x1024 will produce blocky
    output because it is 4x the pixel budget the model ever saw.
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


# ---------------------------------------------------------------------------
# DrawatoonSetIP — V3
# ---------------------------------------------------------------------------
_LAYOUT_STATE_URL   = "http://127.0.0.1:8777/state"
_LAYOUT_VERSION_URL = "http://127.0.0.1:8777/version"
_LAYOUT_FETCH_TIMEOUT_S = 2.0


def _post_json(url, body, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _probe_version():
    try:
        r = _post_json(_LAYOUT_VERSION_URL, {}, timeout=1.0)
    except Exception:
        return ""
    v = r.get("version") if isinstance(r, dict) else None
    return str(v) if v is not None else ""


def _fetch_layout():
    try:
        state = _post_json(_LAYOUT_STATE_URL, {}, timeout=_LAYOUT_FETCH_TIMEOUT_S)
    except Exception as e:
        print(f"[DrawatoonSetIP] fetch failed from {_LAYOUT_STATE_URL}: "
              f"{type(e).__name__}: {e}", flush=True)
        return [], [], []

    if not isinstance(state, dict):
        print(f"[DrawatoonSetIP] fetch returned non-dict: {type(state).__name__}",
              flush=True)
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


def _get_ip_adapter(model):
    try:
        return model.model.diffusion_model.ip_adapter
    except AttributeError:
        return None


class DrawatoonSetIP(io.ComfyNode):
    """
    Attach IP-adapter conditioning to a CONDITIONING.

    Use this on both positive and negative branches feeding KSampler so
    that cond and uncond each carry different ip_hidden_states. Without
    it, both passes see the same tokens and the ip branch cancels out
    in the CFG delta — layout then barely affects output.

    ip_mode:
      - "layout"   : probe http://127.0.0.1:8777/state for bboxes.
                     Falls back to void_ip_embed when the playground is
                     unreachable or returns empty bboxes.
      - "negative" : use the trained negative_ip_embed (CFG drop-out token).
      - "void"     : use the trained void_ip_embed (no-layout token).
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
        # V3 equivalent of V1 IS_CHANGED: return the layout version so
        # the node re-executes when the playground state changes.
        return _probe_version()

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
            print(
                f"[DrawatoonSetIP] layout: "
                f"text={len(text_bboxes)} char={len(character_bboxes)} "
                f"refs={len([r for r in refs_raw if isinstance(r, list)])}",
                flush=True,
            )
            if not text_bboxes and not character_bboxes:
                print("[DrawatoonSetIP] empty layout \u2014 using void_ip_embed",
                      flush=True)
                ip_hidden_states = ip_adapter.void_ip_embed.weight.unsqueeze(0)
                ip_attention_mask = torch.ones(
                    1, 1, dtype=torch.bool, device=ip_hidden_states.device,
                )
            else:
                # Coerce references to the adapter's dtype/device.
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
            print("[DrawatoonSetIP] using negative_ip_embed (CFG drop-out)",
                  flush=True)
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


# ---------------------------------------------------------------------------
# V3 entrypoint — every node is returned from one extension.
# ---------------------------------------------------------------------------
class KritomaticExtension(ComfyExtension):
    async def get_node_list(self):
        return [
            DrawatoonCheckpointLoader,
            DrawatoonSetIP,
            MultiAreaConditioning,
        ]


async def comfy_entrypoint():
    return KritomaticExtension()
