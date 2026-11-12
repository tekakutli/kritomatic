# kritomatic_nodes/__init__.py
"""
Kritomatic Nodes — Multi‑Area Conditioning (playground-driven)

The area list — masks, prompts, and per-area flag overrides — is
edited in the `multimask` playground at http://127.0.0.1:8776 (or
http://127.0.0.1:8780/multimask under the pgkit hub).  The node
fetches that state on every execution, stages each area's mask PNG
into ComfyUI's input directory, and expands them into
ConditioningSetMask branches.

CONTEXT MASK AND CROP ALIGNMENT
===============================
When `context_mask` (and optionally `selection_mask`) is wired and
an area's `bbox` flag is true (or inherits the global default), the
node crops the area's mask to the bounding box of
`context_mask ∪ selection_mask`, expanded by `crop_padding` pixels
and clamped to the mask canvas.  This makes the area mask land on
the same pixels `InpaintCropImproved` (or any other bbox-crop)
cropped the image to — which is what the sampler actually sees.

For alignment to be exact:

  · wire `context_mask` to the same mask the workflow feeds into
    `InpaintCropImproved`'s `optional_context_mask` (e.g. Krita's
    `@thecontext`);
  · wire `selection_mask` to the same mask the workflow feeds into
    `InpaintCropImproved`'s `mask` (e.g. Krita's `@theselection`);
  · set `crop_padding` to match `InpaintCropImproved`'s
    `output_padding` (default 32).

If `selection_mask` is not wired, the node falls back to the bbox
of `context_mask` alone.  That is correct only when the selection
is inside the context region — which is usually the case, but not
always.

Requires: ComfyUI >= 0.4
          comfyui-wd14-tagger  (only when at least one area uses use_tagger)
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from comfy_api.latest import ComfyExtension, io
import folder_paths
from comfy_execution.graph_utils import GraphBuilder


_DEFAULT_PLAYGROUND_URL = "http://127.0.0.1:8776"
_DEFAULT_CROP_PADDING   = 32


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def _bool(v, default=False):
    if isinstance(v, bool): return v
    if isinstance(v, str):  return v.lower() in ("true", "yes", "on", "1")
    if isinstance(v, (int, float)): return bool(v)
    return default

def _float(v, default):
    try: return float(v)
    except (TypeError, ValueError): return default

def _int(v, default):
    try: return int(v)
    except (TypeError, ValueError): return default

def _str(v, default=""):
    return default if v is None else str(v)


# ---------------------------------------------------------------------------
# Playground HTTP client (stdlib only)
# ---------------------------------------------------------------------------

def _post_json(url, body, timeout=5.0):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _fetch_state(playground_url, timeout=5.0):
    return _post_json(playground_url.rstrip("/") + "/state", {}, timeout)


def _fetch_version(playground_url, timeout=2.0):
    return _post_json(playground_url.rstrip("/") + "/version", {}, timeout)


def _fetch_mask_b64(playground_url, hash_hex, timeout=10.0):
    r = _post_json(
        playground_url.rstrip("/") + "/mask",
        {"hash": hash_hex},
        timeout,
    )
    if not r.get("success"):
        raise RuntimeError(r.get("message", "unknown"))
    return r.get("b64", "")


# ---------------------------------------------------------------------------
# Mask bbox helpers
# ---------------------------------------------------------------------------

def _mask_shape(mask):
    """Return (H, W) of a mask tensor, or None."""
    if mask is None:
        return None
    try:
        shape = tuple(mask.shape)
    except AttributeError:
        return None
    if len(shape) == 2:
        return shape[0], shape[1]
    if len(shape) >= 3:
        return shape[-2], shape[-1]
    return None


def _mask_bbox(mask):
    """Return (x, y, w, h) bbox of the non-zero region, or None."""
    if mask is None:
        return None
    try:
        if mask.dim() == 3:
            mask = mask[0]
        elif mask.dim() > 3:
            return None
        nz = mask.nonzero()
    except Exception:
        return None
    if nz.numel() == 0:
        return None
    ys = nz[:, 0]
    xs = nz[:, 1]
    x0 = int(xs.min())
    y0 = int(ys.min())
    x1 = int(xs.max()) + 1
    y1 = int(ys.max()) + 1
    return (x0, y0, x1 - x0, y1 - y0)


def _union_bbox(a, b):
    if a is None: return b
    if b is None: return a
    x0 = min(a[0], b[0])
    y0 = min(a[1], b[1])
    x1 = max(a[0] + a[2], b[0] + b[2])
    y1 = max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def _pad_clamp_bbox(bbox, padding, canvas_w, canvas_h):
    x, y, w, h = bbox
    x0 = max(0, x - padding)
    y0 = max(0, y - padding)
    x1 = min(canvas_w, x + w + padding)
    y1 = min(canvas_h, y + h + padding)
    return (x0, y0, x1 - x0, y1 - y0)


# ---------------------------------------------------------------------------
# MultiAreaConditioning
# ---------------------------------------------------------------------------

class MultiAreaConditioning(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MultiAreaConditioning",
            display_name="Multi‑Area Conditioning (Playground Expand)",
            category="conditioning",
            description=(
                "Fetches mask+prompt pairs from a running `multimask` "
                "playground and expands them into per-area "
                "ConditioningSetMask branches. Each area produces its "
                "own LoadImageMask from a PNG staged under "
                "ComfyUI/input/. The playground must be reachable at "
                "`playground_url` when this node executes. "
                "Guiless — sockets only."
            ),
            enable_expand=True,
            inputs=[
                io.Clip.Input("clip"),
                io.Conditioning.Input("base_positive"),
                io.Mask.Input(
                    "context_mask",
                    optional=True,
                    tooltip=(
                        "The context / crop-hint mask (e.g. Krita's "
                        "@thecontext).  When wired, the node uses its "
                        "bbox (unioned with selection_mask, padded by "
                        "crop_padding) to crop each area's mask so it "
                        "aligns with the region InpaintCropImproved "
                        "(or any other bbox-crop) cropped the image to."
                    ),
                ),
                io.Mask.Input(
                    "selection_mask",
                    optional=True,
                    tooltip=(
                        "The selection mask (e.g. Krita's @theselection). "
                        "Wire to the same mask that InpaintCropImproved "
                        "uses as its `mask` input.  Used only to expand "
                        "the crop bbox; not otherwise applied."
                    ),
                ),
                io.Image.Input(
                    "image",
                    optional=True,
                    tooltip="Source image for per-area taggers.",
                ),
                io.Int.Input(
                    "crop_padding",
                    default=_DEFAULT_CROP_PADDING,
                    min=0, max=512,
                    optional=True,
                    tooltip=(
                        "Padding (in pixels) around the context/selection "
                        "bbox.  Should match InpaintCropImproved's "
                        "`output_padding` (default 32)."
                    ),
                ),
                io.String.Input(
                    "playground_url",
                    default=_DEFAULT_PLAYGROUND_URL,
                    optional=True,
                    tooltip=(
                        "Base URL of the multimask playground. "
                        "Standalone: http://127.0.0.1:8776 . "
                        "Under the pgkit hub: http://127.0.0.1:8780/multimask . "
                        "Leave blank to use the default."
                    ),
                ),
                io.String.Input(
                    "area_filter",
                    default="",
                    optional=True,
                    tooltip=(
                        "Optional comma-separated area names. Empty = "
                        "all areas in the playground state."
                    ),
                ),
            ],
            outputs=[
                io.Conditioning.Output("CONDITIONING"),
            ],
        )

    @classmethod
    def IS_CHANGED(cls, playground_url=_DEFAULT_PLAYGROUND_URL, **kwargs):
        playground_url = (playground_url or _DEFAULT_PLAYGROUND_URL).strip()
        try:
            r = _fetch_version(playground_url)
            return r.get("version") or "empty"
        except Exception:
            return "unreachable"

    @classmethod
    def execute(cls, clip, base_positive, context_mask=None, image=None,
                playground_url=_DEFAULT_PLAYGROUND_URL, area_filter="",
                selection_mask=None, crop_padding=_DEFAULT_CROP_PADDING):
        playground_url = (playground_url or _DEFAULT_PLAYGROUND_URL).strip()
        area_filter = area_filter or ""
        crop_padding = max(0, _int(crop_padding, _DEFAULT_CROP_PADDING))

        try:
            state = _fetch_state(playground_url)
        except Exception as e:
            raise RuntimeError(
                f"Kritomatic multimask playground unreachable at "
                f"{playground_url!r}: {e}\n"
                f"Start it with: "
                f"python playgrounds/multimask/multimask_playground.py"
            )

        state_version = state.get("version") or ""
        globs = dict(state.get("globals") or {})
        areas = list(state.get("areas") or [])

        want = {n.strip() for n in area_filter.split(",") if n.strip()}
        if want:
            areas = [a for a in areas if a.get("name") in want]

        if not areas:
            print(f"[MultiAreaConditioning] no areas in state "
                  f"(v{state_version[:8]}); passing base_positive through")
            return io.NodeOutput(base_positive, expand={})

        # ---- Stage each area's mask into ComfyUI's input dir ----
        input_dir = folder_paths.get_input_directory()
        staged = {}
        for area in areas:
            mask_meta = area.get("mask") or {}
            hash_hex = mask_meta.get("hash")
            area_id  = area.get("id")
            if not hash_hex or not area_id:
                print(f"[Kritomatic] skipping area "
                      f"{area.get('name', '?')!r}: no mask hash")
                continue
            fn = f"_kritomatic_{area_id}.png"
            dst = os.path.join(input_dir, fn)
            try:
                b64 = _fetch_mask_b64(playground_url, hash_hex)
                with open(dst, "wb") as fh:
                    fh.write(base64.b64decode(b64))
            except Exception as e:
                print(f"[Kritomatic] skipping area "
                      f"{area.get('name', '?')!r}: mask fetch failed: {e}")
                continue
            staged[area_id] = fn

        if not staged:
            print(f"[MultiAreaConditioning] staged 0 of {len(areas)} "
                  f"area(s); passing base_positive through")
            return io.NodeOutput(base_positive, expand={})

        # ---- Compute the crop rect from context ∪ selection ----
        canvas_shape = _mask_shape(context_mask) or _mask_shape(selection_mask)
        crop_rect    = None
        crop_source  = ""
        if canvas_shape is not None:
            canvas_h, canvas_w = canvas_shape
            bbox_ctx = _mask_bbox(context_mask) if context_mask is not None else None
            bbox_sel = _mask_bbox(selection_mask) if selection_mask is not None else None
            combined = _union_bbox(bbox_ctx, bbox_sel)
            if combined is not None:
                crop_rect = _pad_clamp_bbox(
                    combined, crop_padding, canvas_w, canvas_h,
                )
                if context_mask is not None and selection_mask is not None:
                    crop_source = "context+selection"
                elif context_mask is not None:
                    crop_source = "context"
                else:
                    crop_source = "selection"

        if crop_rect is None:
            if canvas_shape is None:
                reason = "no context_mask or selection_mask wired"
            else:
                reason = "context/selection masks are all-zero"
            print(f"[MultiAreaConditioning] no crop rect ({reason}); "
                  f"area masks will be resized without cropping")
        else:
            print(f"[MultiAreaConditioning] crop rect from "
                  f"{crop_source}+pad({crop_padding}) = {crop_rect} "
                  f"(canvas {canvas_w}x{canvas_h})")

        print(f"[MultiAreaConditioning] staged {len(staged)} of "
              f"{len(areas)} area(s) from {playground_url} "
              f"(v{state_version[:8]})")

        # ---- Expansion ----
        graph = GraphBuilder()

        combine_mode          = _str(globs.get("combine_mode"), "combine")
        use_base_setarea      = _bool(globs.get("use_base_setarea"), False)
        base_w                = _int(globs.get("base_area_width"), 1024)
        base_h                = _int(globs.get("base_area_height"), 1024)
        default_strength      = _float(globs.get("default_strength"), 0.9)
        default_set_cond_area = _str(globs.get("default_set_cond_area"),
                                     "default")

        tagger_mode        = _str(globs.get("tagger_mode"), "replace")
        if tagger_mode not in ("replace", "concat"):
            tagger_mode = "replace"
        tagger_model       = _str(globs.get("tagger_model"),
                                  "wd-v1-4-moat-tagger-v2")
        tagger_threshold   = _float(globs.get("tagger_threshold"), 0.35)
        tagger_char_thresh = _float(globs.get("tagger_char_threshold"), 0.85)

        g_bbox       = _bool(globs.get("bbox"), False)
        g_resize     = _bool(globs.get("resize"), True)
        mask_w       = _int(globs.get("mask_w"), 1024)
        mask_h       = _int(globs.get("mask_h"), 1024)
        mask_channel = _str(globs.get("mask_channel"), "red")

        current = base_positive
        if use_base_setarea:
            current = graph.node(
                "ConditioningSetArea",
                conditioning=base_positive,
                width=base_w, height=base_h, x=0, y=0, strength=1.0,
            ).out(0)

        for area in areas:
            area_id = area.get("id")
            if area_id not in staged:
                continue
            mask_file = staged[area_id]

            prompt = _str(area.get("prompt"))

            strength = _float(
                area.get("strength") if area.get("strength") is not None
                                      else default_strength,
                default_strength,
            )
            set_cond_area = _str(
                area.get("set_cond_area") if area.get("set_cond_area") is not None
                                          else default_set_cond_area,
                default_set_cond_area,
            )
            area_channel = _str(
                area.get("mask_channel") if area.get("mask_channel") is not None
                                         else mask_channel,
                mask_channel,
            )
            area_bbox = _bool(
                area.get("bbox") if area.get("bbox") is not None else g_bbox,
                g_bbox,
            )
            area_resize = _bool(
                area.get("resize") if area.get("resize") is not None else g_resize,
                g_resize,
            )

            area_use_tagger = _bool(area.get("use_tagger"), False)
            area_tagger_mode = _str(
                area.get("tagger_mode") if area.get("tagger_mode") is not None
                                         else tagger_mode,
                tagger_mode,
            )
            if area_tagger_mode not in ("replace", "concat"):
                area_tagger_mode = "replace"
            area_tagger_model = _str(
                area.get("tagger_model") if area.get("tagger_model") is not None
                                          else tagger_model,
                tagger_model,
            )
            area_tagger_thresh = _float(
                area.get("tagger_threshold") if area.get("tagger_threshold") is not None
                                              else tagger_threshold,
                tagger_threshold,
            )
            area_tagger_char_th = _float(
                area.get("tagger_char_threshold")
                    if area.get("tagger_char_threshold") is not None
                    else tagger_char_thresh,
                tagger_char_thresh,
            )

            if area_use_tagger and image is None:
                raise RuntimeError(
                    f"Area {area.get('name', '?')!r} has use_tagger=true "
                    f"but the node's `image` input is not connected."
                )

            raw_mask = graph.node(
                "LoadImageMask",
                image=mask_file,
                channel=area_channel,
            ).out(0)
            cond_mask = raw_mask

            # Crop to the same rect the upstream crop used.  This is
            # what makes the area mask land on the right pixels in the
            # sampler's latent.
            if area_bbox and crop_rect is not None:
                cx, cy, cw, ch = crop_rect
                cond_mask = graph.node(
                    "CropMask",
                    mask=cond_mask,
                    x=cx, y=cy, width=cw, height=ch,
                ).out(0)

            if area_resize:
                cond_mask = graph.node(
                    "ResizeImageMaskNode",
                    input=cond_mask,
                    **{
                        "resize_type":        "scale dimensions",
                        "resize_type.width":  mask_w,
                        "resize_type.height": mask_h,
                        "resize_type.crop":   "center",
                        "scale_method":       "area",
                    },
                ).out(0)

            text_input = prompt
            if area_use_tagger and image is not None:
                img_for_tagger = graph.node(
                    "ImageCropToMask",
                    images=image, masks=raw_mask,
                    width=mask_w, height=mask_h, pad_factor=1,
                    grow_mask=0, background="#000000",
                ).out(0)

                tagger_out = graph.node(
                    "WD14Tagger|pysssss",
                    image=img_for_tagger,
                    model=area_tagger_model,
                    threshold=area_tagger_thresh,
                    character_threshold=area_tagger_char_th,
                    replace_underscore=False,
                    trailing_comma=False,
                    exclude_tags="",
                ).out(0)

                if area_tagger_mode == "concat" and prompt:
                    text_input = graph.node(
                        "StringFormat",
                        **{
                            "values.a": tagger_out,
                            "values.b": prompt,
                            "f_string": "{a} {b}",
                        },
                    ).out(0)
                else:
                    text_input = tagger_out

            cond = graph.node(
                "CLIPTextEncode", clip=clip, text=text_input,
            ).out(0)

            cond = graph.node(
                "ConditioningSetMask",
                conditioning=cond, mask=cond_mask,
                strength=strength, set_cond_area=set_cond_area,
            ).out(0)

            if combine_mode == "concat":
                current = graph.node(
                    "ConditioningConcat",
                    conditioning_to=current, conditioning_from=cond,
                ).out(0)
            else:
                current = graph.node(
                    "ConditioningCombine",
                    conditioning_1=current, conditioning_2=cond,
                ).out(0)

        finalized = graph.finalize()

        print(f"[MultiAreaConditioning] expanded -> "
              f"{len(finalized)} node(s)")

        return io.NodeOutput(current, expand=finalized)


# ---------------------------------------------------------------------------
# V3 entrypoint
# ---------------------------------------------------------------------------

class KritomaticExtension(ComfyExtension):
    async def get_node_list(self):
        return [MultiAreaConditioning]


async def comfy_entrypoint():
    return KritomaticExtension()