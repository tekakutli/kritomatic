"""
cone_kra.py — turn the cone playground into a .kra.

Five export modes, selected by options.text_warp_mode:

    "square" (default)   One group + one transform mask per square.
    "patch"              One group + one mask per PATCH.  All texts
                         on the patch share the patch's mask.
    "text"               One group + one mask per TEXT.  Each text's
                         mask is a copy of the SQUARE'S own mask.
    "text-shear"         No masks.  One direct vector layer per
                         text, carrying a full affine transform.
    "patch-shear"        No masks.  One group per patch; each text
                         on the patch is a direct vector layer inside
                         the group, with its own affine.

No background layer is created in any mode.

INLINE-WRAPPED TEXT AND HANDLE ALIGNMENT
========================================
Every text the export creates is written in Krita's SVG 2 "inline
wrapped" mode.  In that mode a text shape carries:

    style="inline-size: <W>;font-size: <F>;white-space: pre-wrap;"

and NO x/y attributes on the <text> element.  Krita then draws two
vertical "inline-size handles" at the left and right edges of the
wrapping area, and the text wraps within that area.

To put the handles on the polygon's edges, the shear modes follow
each add_vector_text with two move_text_handle commands — one for
the left handle, one for the right.  The targets are the midpoints
of the polygon's two edges farthest apart along the text baseline
direction.

STAGE 1 CLEANUP
===============
`_emit_handle_moves` used to `print(..., file=sys.stderr)` six
diagnostic lines per text (layer name, x/y, transform, polygon,
targets).  On a 20-square export that's ~120 lines of debug noise
in the server terminal.  The prints are now gated behind the
environment variable `CONE_KRA_DEBUG`.  Set `CONE_KRA_DEBUG=1` to
re-enable them for a specific run.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List


def _find_repo_src() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(10):
        cand = p / "src"
        if (cand / "kritomatic").is_dir():
            return cand
        if p == p.parent:
            break
        p = p.parent
    raise RuntimeError(
        "Could not locate src/kritomatic from " + str(Path(__file__).resolve())
    )


_SRC = _find_repo_src()
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic.batch import BatchExecutor  # noqa: E402


DEFAULT_CANVAS_W = 1920
DEFAULT_CANVAS_H = 1080

DEFAULT_RESOLUTION = 72
DEFAULT_FONT_FAMILY = "sans-serif"
FONT_FRACTION = 0.40

EXPORT_GROUP_NAME = "Export"

_DEBUG = bool(os.environ.get("CONE_KRA_DEBUG"))


def _debug(*args, **kwargs):
    if _DEBUG:
        kwargs.setdefault("file", sys.stderr)
        print(*args, **kwargs)


def _fallback_anchor(W, H):
    return {"x": W / 2.0, "y": H / 2.0, "alignment": "center"}


def _normalize_options(raw):
    if not isinstance(raw, dict):
        raw = {}
    draw = raw.get("draw_rectangles", True)
    if not isinstance(draw, bool):
        draw = bool(draw)
    mode = raw.get("text_warp_mode", "square")
    if mode not in ("square", "patch", "text", "text-shear", "patch-shear"):
        mode = "square"
    return {"draw_rectangles": draw, "text_warp_mode": mode}


def _normalize_cone_band(raw):
    if not isinstance(raw, dict):
        return {"width": DEFAULT_CANVAS_W, "height": DEFAULT_CANVAS_H}
    w = raw.get("width", DEFAULT_CANVAS_W)
    h = raw.get("height", DEFAULT_CANVAS_H)
    try:
        w = int(round(float(w)))
    except (TypeError, ValueError):
        w = DEFAULT_CANVAS_W
    try:
        h = int(round(float(h)))
    except (TypeError, ValueError):
        h = DEFAULT_CANVAS_H
    if w < 1:
        w = DEFAULT_CANVAS_W
    if h < 1:
        h = DEFAULT_CANVAS_H
    return {"width": w, "height": h}


def _common_header(band, doc_name):
    return [
        {
            "type": "create_new_with_dimensions",
            "name": doc_name,
            "width": band["width"],
            "height": band["height"],
            "resolution": DEFAULT_RESOLUTION,
        },
        {
            "type": "create_layer",
            "name": EXPORT_GROUP_NAME,
            "layer_type": "grouplayer",
            "position": "top",
        },
    ]


def _inline_size_for(font_px, width=None):
    """Return the wrap width for one exported text.  Uses the source
    width directly when available; otherwise recovers it from the
    font size (font_px = min(W, H) * FONT_FRACTION, and every square
    in this project is square)."""
    if width is not None:
        try:
            w = float(width)
            if w > 0:
                return w
        except (TypeError, ValueError):
            pass
    try:
        fs = float(font_px)
    except (TypeError, ValueError):
        return None
    if fs <= 0:
        return None
    return fs / FONT_FRACTION


def _coerce_transform(raw):
    """Accept a list or a JSON string; return a 6-float list or None."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) != 6:
        return None
    try:
        return [float(v) for v in raw]
    except (TypeError, ValueError):
        return None


def _polygon_target_edges(polygon_pts, baseline_dir):
    """Return (target_left, target_right), the two polygon edge
    midpoints farthest apart along the baseline direction."""
    n = len(polygon_pts)
    if n < 3:
        return None
    bx, by = baseline_dir
    bL2 = bx * bx + by * by
    if bL2 < 1e-12:
        return None
    bL = bL2 ** 0.5
    bux, buy = bx / bL, by / bL

    edges = []
    for i in range(n):
        ax = float(polygon_pts[i][0])
        ay = float(polygon_pts[i][1])
        cx = float(polygon_pts[(i + 1) % n][0])
        cy = float(polygon_pts[(i + 1) % n][1])
        mx = (ax + cx) / 2.0
        my = (ay + cy) / 2.0
        proj = mx * bux + my * buy
        edges.append((proj, mx, my))
    edges.sort(key=lambda e: e[0])

    return ((edges[0][1], edges[0][2]),
            (edges[-1][1], edges[-1][2]))


def _emit_handle_moves(commands, layer_name, t, polygon_pts):
    affine = _coerce_transform(t.get("transform"))

    _debug("=" * 60)
    _debug("text layer_name = %r" % layer_name)
    _debug("  t['name']     = %r" % t.get("name"))
    _debug("  t['x']  t['y'] = %r  %r" % (t.get("x"), t.get("y")))
    _debug("  t['transform']= %r" % t.get("transform"))
    _debug("  polygon_pts   = %r" % (polygon_pts,))

    if not polygon_pts or len(polygon_pts) < 4:
        _debug("  -> SKIP (no polygon pts)")
        return
    if affine is None:
        _debug("  -> SKIP (bad transform)")
        return
    targets = _polygon_target_edges(polygon_pts, (affine[0], affine[1]))
    if targets is None:
        _debug("  -> SKIP (targets None)")
        return
    tl, tr = targets
    _debug("  target_left  = %r" % (tl,))
    _debug("  target_right = %r" % (tr,))

    commands.append({
        "type": "move_text_handle",
        "layer_name": layer_name,
        "text_index": 0,
        "side": "left",
        "target_x": round(float(tl[0]), 4),
        "target_y": round(float(tl[1]), 4),
        "pivot": "other",
    })
    commands.append({
        "type": "move_text_handle",
        "layer_name": layer_name,
        "text_index": 0,
        "side": "right",
        "target_x": round(float(tr[0]), 4),
        "target_y": round(float(tr[1]), 4),
        "pivot": "other",
    })


def _build_square_mode(shapes, opts, band, doc_name):
    commands = _common_header(band, doc_name)

    for sh in shapes:
        layer_name = sh["name"]
        content_name = layer_name + "_content"
        mask_name = layer_name + "_persp"

        dst_pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in sh["points"]]

        W = max(1.0, float(sh["natural_w"]))
        H = max(1.0, float(sh["natural_h"]))
        font_px = max(6, int(round(min(W, H) * FONT_FRACTION)))

        anchor = sh.get("text_anchor") or _fallback_anchor(W, H)
        text_x = float(anchor.get("x", W / 2.0))
        text_y = float(anchor.get("y", H / 2.0))
        text_align = anchor.get("alignment", "center")

        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "grouplayer",
            "parent": EXPORT_GROUP_NAME,
        })
        commands.append({
            "type": "create_layer",
            "name": content_name,
            "layer_type": "vectorlayer",
            "parent": layer_name,
        })

        rect_pts = [[0.0, 0.0], [W, 0.0], [W, H], [0.0, H]]
        if opts["draw_rectangles"]:
            commands.append({
                "type": "add_vector_polygon",
                "layer_name": content_name,
                "points": json.dumps(rect_pts),
                "fill": sh.get("fill", "#ffffff"),
                "fill_opacity": sh.get("fill_opacity", 0.40),
                "stroke": sh.get("stroke", "#ffffff"),
                "stroke_width": sh.get("stroke_width", 2.0),
                "stroke_opacity": sh.get("stroke_opacity", 1.0),
            })

        commands.append({
            "type": "add_vector_text",
            "layer_name": content_name,
            "text": sh.get("label", layer_name),
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": font_px,
            "x": text_x,
            "y": text_y,
            "color": sh.get("text_color", "#ffffff"),
            "alignment": text_align,
            "rotation": float(sh.get("flat_rotation", 0.0)),
            "inline_size": _inline_size_for(font_px, width=W),
        })

        commands.append({
            "type": "create_transform_mask",
            "layer_name": layer_name,
            "mask_name": mask_name,
        })
        commands.append({
            "type": "set_perspective_transform_mask",
            "mask_name": mask_name,
            "src_points": json.dumps(rect_pts),
            "dst_points": json.dumps(dst_pts),
        })

    return {"id": "cone_export", "commands": commands}


def _build_patch_mode(patches, opts, band, doc_name):
    commands = _common_header(band, doc_name)

    for patch in patches:
        for rect in patch.get("rects", []):
            rect_layer = rect["name"] + "_rect"
            commands.append({
                "type": "create_layer",
                "name": rect_layer,
                "layer_type": "vectorlayer",
                "parent": EXPORT_GROUP_NAME,
            })
            pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in rect["points"]]
            commands.append({
                "type": "add_vector_polygon",
                "layer_name": rect_layer,
                "points": json.dumps(pts),
                "fill": rect.get("fill", "#ffffff"),
                "fill_opacity": rect.get("fill_opacity", 0.40),
                "stroke": rect.get("stroke", "#ffffff"),
                "stroke_width": rect.get("stroke_width", 2.0),
                "stroke_opacity": rect.get("stroke_opacity", 1.0),
            })

    for patch in patches:
        layer_name = patch["name"]
        content_name = layer_name + "_content"
        mask_name = layer_name + "_persp"

        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "grouplayer",
            "parent": EXPORT_GROUP_NAME,
        })
        commands.append({
            "type": "create_layer",
            "name": content_name,
            "layer_type": "vectorlayer",
            "parent": layer_name,
        })

        for txt in patch.get("texts", []):
            font_px = max(6, int(round(float(txt["font_px"]))))
            commands.append({
                "type": "add_vector_text",
                "layer_name": content_name,
                "text": str(txt.get("text", txt.get("label", ""))),
                "font_family": DEFAULT_FONT_FAMILY,
                "font_size": font_px,
                "x": float(txt["x"]),
                "y": float(txt["y"]),
                "color": txt.get("color", "#ffffff"),
                "alignment": txt.get("alignment", "center"),
                "rotation": float(txt.get("flat_rotation", 0.0)),
                "inline_size": _inline_size_for(font_px, width=txt.get("src_w")),
            })

        commands.append({
            "type": "create_transform_mask",
            "layer_name": layer_name,
            "mask_name": mask_name,
        })

        src_pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in patch["src_pts"]]
        dst_pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in patch["dst_pts"]]
        commands.append({
            "type": "set_perspective_transform_mask",
            "mask_name": mask_name,
            "src_points": json.dumps(src_pts),
            "dst_points": json.dumps(dst_pts),
        })

    return {"id": "cone_export", "commands": commands}


def _build_text_mode(texts, rects, opts, band, doc_name):
    commands = _common_header(band, doc_name)

    for rect in (rects or []):
        rect_layer = rect["name"] + "_rect"
        commands.append({
            "type": "create_layer",
            "name": rect_layer,
            "layer_type": "vectorlayer",
            "parent": EXPORT_GROUP_NAME,
        })
        pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
               for p in rect["points"]]
        commands.append({
            "type": "add_vector_polygon",
            "layer_name": rect_layer,
            "points": json.dumps(pts),
            "fill": rect.get("fill", "#ffffff"),
            "fill_opacity": rect.get("fill_opacity", 0.40),
            "stroke": rect.get("stroke", "#ffffff"),
            "stroke_width": rect.get("stroke_width", 2.0),
            "stroke_opacity": rect.get("stroke_opacity", 1.0),
        })

    for t in texts:
        group_name   = t["name"] + "_text_grp"
        content_name = t["name"] + "_text_content"
        mask_name    = t["name"] + "_text_mask"

        commands.append({
            "type": "create_layer",
            "name": group_name,
            "layer_type": "grouplayer",
            "parent": EXPORT_GROUP_NAME,
        })
        commands.append({
            "type": "create_layer",
            "name": content_name,
            "layer_type": "vectorlayer",
            "parent": group_name,
        })
        commands.append({
            "type": "add_vector_text",
            "layer_name": content_name,
            "text": str(t.get("label", t["name"])),
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": max(6, int(round(float(t["font_px"])))),
            "x": float(t["text_x"]),
            "y": float(t["text_y"]),
            "color": t.get("color", "#ffffff"),
            "alignment": t.get("alignment", "center"),
            "rotation": float(t.get("rotation", 0.0)),
            "inline_size": _inline_size_for(t["font_px"], width=t.get("src_w")),
        })
        commands.append({
            "type": "create_transform_mask",
            "layer_name": group_name,
            "mask_name": mask_name,
        })
        src_pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in t["src_pts"]]
        dst_pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in t["dst_pts"]]
        commands.append({
            "type": "set_perspective_transform_mask",
            "mask_name": mask_name,
            "src_points": json.dumps(src_pts),
            "dst_points": json.dumps(dst_pts),
        })

    return {"id": "cone_export", "commands": commands}


def _build_shear_mode(texts, rects, opts, band, doc_name):
    commands = _common_header(band, doc_name)

    for rect in (rects or []):
        rect_layer = rect["name"] + "_rect"
        commands.append({
            "type": "create_layer",
            "name": rect_layer,
            "layer_type": "vectorlayer",
            "parent": EXPORT_GROUP_NAME,
        })
        pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
               for p in rect["points"]]
        commands.append({
            "type": "add_vector_polygon",
            "layer_name": rect_layer,
            "points": json.dumps(pts),
            "fill": rect.get("fill", "#ffffff"),
            "fill_opacity": rect.get("fill_opacity", 0.40),
            "stroke": rect.get("stroke", "#ffffff"),
            "stroke_width": rect.get("stroke_width", 2.0),
            "stroke_opacity": rect.get("stroke_opacity", 1.0),
        })

    rect_by_name = {}
    for rect in (rects or []):
        name = rect.get("name")
        pts = rect.get("points")
        if name and pts and len(pts) >= 3:
            rect_by_name[name] = pts

    for t in texts:
        layer_name = t["name"] + "_text"

        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "vectorlayer",
            "parent": EXPORT_GROUP_NAME,
        })
        commands.append({
            "type": "add_vector_text",
            "layer_name": layer_name,
            "text": str(t.get("text", t.get("label", ""))),
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": max(6, int(round(float(t["font_px"])))),
            "x": float(t["x"]),
            "y": float(t["y"]),
            "color": t.get("color", "#ffffff"),
            "alignment": t.get("alignment", "center"),
            "rotation": 0.0,
            "transform": json.dumps(t["transform"]),
            "inline_size": _inline_size_for(t["font_px"], width=t.get("src_w")),
        })
        _emit_handle_moves(commands, layer_name, t,
                           rect_by_name.get(t.get("name")))

    return {"id": "cone_export", "commands": commands}


def _build_patch_shear_mode(patches, opts, band, doc_name):
    commands = _common_header(band, doc_name)

    for patch in patches:
        for rect in patch.get("rects", []):
            rect_layer = rect["name"] + "_rect"
            commands.append({
                "type": "create_layer",
                "name": rect_layer,
                "layer_type": "vectorlayer",
                "parent": EXPORT_GROUP_NAME,
            })
            pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in rect["points"]]
            commands.append({
                "type": "add_vector_polygon",
                "layer_name": rect_layer,
                "points": json.dumps(pts),
                "fill": rect.get("fill", "#ffffff"),
                "fill_opacity": rect.get("fill_opacity", 0.40),
                "stroke": rect.get("stroke", "#ffffff"),
                "stroke_width": rect.get("stroke_width", 2.0),
                "stroke_opacity": rect.get("stroke_opacity", 1.0),
            })

    rect_by_name = {}
    for patch in patches:
        for rect in patch.get("rects", []):
            name = rect.get("name")
            pts = rect.get("points")
            if name and pts and len(pts) >= 3:
                rect_by_name[name] = pts

    for patch in patches:
        group_name = patch["name"]

        commands.append({
            "type": "create_layer",
            "name": group_name,
            "layer_type": "grouplayer",
            "parent": EXPORT_GROUP_NAME,
        })

        for t in patch.get("texts", []):
            layer_name = t["name"] + "_text"

            commands.append({
                "type": "create_layer",
                "name": layer_name,
                "layer_type": "vectorlayer",
                "parent": group_name,
            })
            commands.append({
                "type": "add_vector_text",
                "layer_name": layer_name,
                "text": str(t.get("text", t.get("label", ""))),
                "font_family": DEFAULT_FONT_FAMILY,
                "font_size": max(6, int(round(float(t["font_px"])))),
                "x": float(t["x"]),
                "y": float(t["y"]),
                "color": t.get("color", "#ffffff"),
                "alignment": t.get("alignment", "center"),
                "rotation": 0.0,
                "transform": json.dumps(t["transform"]),
                "inline_size": _inline_size_for(t["font_px"], width=t.get("src_w")),
            })
            _emit_handle_moves(commands, layer_name, t,
                               rect_by_name.get(t.get("name")))

    return {"id": "cone_export", "commands": commands}


def build_bundle(shapes: List[Dict[str, Any]],
                 options: Dict[str, Any] = None,
                 cone_band: Dict[str, Any] = None,
                 doc_name: str = "Cone Scene",
                 patches: List[Dict[str, Any]] = None,
                 texts: List[Dict[str, Any]] = None,
                 rects: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    opts = _normalize_options(options)
    band = _normalize_cone_band(cone_band)

    if opts["text_warp_mode"] == "patch-shear":
        return _build_patch_shear_mode(patches or [], opts, band, doc_name)
    if opts["text_warp_mode"] == "text-shear":
        return _build_shear_mode(texts or [], rects or [],
                                 opts, band, doc_name)
    if opts["text_warp_mode"] == "text":
        return _build_text_mode(texts or [], rects or [],
                                opts, band, doc_name)
    if opts["text_warp_mode"] == "patch":
        return _build_patch_mode(patches or [], opts, band, doc_name)
    return _build_square_mode(shapes or [], opts, band, doc_name)


def generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point called by cone_server's POST handler."""
    shapes  = payload.get("shapes", [])
    patches = payload.get("patches", [])
    texts   = payload.get("texts", [])
    rects   = payload.get("rects", [])
    options = payload.get("options", {})
    cone_band = payload.get("cone_band", {})
    doc_name = payload.get("doc_name", "Cone Scene")
    output_path = payload.get("output_path")

    opts = _normalize_options(options)
    if opts["text_warp_mode"] == "patch-shear":
        if not patches:
            return {"success": False, "message": "No patches to export"}
    elif opts["text_warp_mode"] == "text-shear":
        if not texts:
            return {"success": False, "message": "No texts to export"}
    elif opts["text_warp_mode"] == "text":
        if not texts:
            return {"success": False, "message": "No texts to export"}
    elif opts["text_warp_mode"] == "patch":
        if not patches:
            return {"success": False, "message": "No patches to export"}
    else:
        if not shapes:
            return {"success": False, "message": "No shapes to export"}
    if not output_path:
        return {"success": False, "message": "No output path provided"}

    out = Path(str(output_path)).expanduser()
    if out.suffix.lower() != ".kra":
        out = out.with_suffix(".kra")

    bundle = build_bundle(
        shapes=shapes, options=options, cone_band=cone_band,
        doc_name=doc_name, patches=patches, texts=texts, rects=rects,
    )
    bundle["commands"].append({
        "type": "save_document",
        "file_path": str(out),
    })

    executor = BatchExecutor()
    try:
        results = executor.execute(bundle)
    finally:
        executor.close()

    failed = [r for r in results if r["status"] == "error"]
    if failed:
        summary = "; ".join(
            f"{r['command']} -> {r['message']}" for r in failed
        )
        return {
            "success": False,
            "message": f"{len(failed)} command(s) failed: {summary}",
            "results": results,
        }

    if opts["text_warp_mode"] in ("text", "text-shear"):
        count, kind = len(texts), "text(s)"
    elif opts["text_warp_mode"] in ("patch", "patch-shear"):
        count, kind = len(patches), "patch(es)"
    else:
        count, kind = len(shapes), "shape(s)"
    return {
        "success": True,
        "message": f"Wrote {out} ({count} {kind})",
        "output_path": str(out),
        "results": results,
    }
