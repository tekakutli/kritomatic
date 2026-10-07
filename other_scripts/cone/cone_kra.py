"""
cone_kra.py — turn the cone playground into a .kra.

Five export modes, selected by options.text_warp_mode:

    "square" (default)
        One group + one transform mask per square.

    "patch"
        One group + one transform mask per patch.  All texts on the
        patch share the patch's mask.

    "text"
        One group + one transform mask per text.  Each text's mask
        is a copy of the SQUARE'S own mask.

    "text-shear"
        No masks.  One direct vector layer per text, carrying a full
        affine transform (rotation + shear + non-uniform scale)
        computed on the JS side to reproduce the mask-mode result
        while keeping the text's on-screen bounding box inside a
        fontNat × fontNat square.

    "patch-shear"
        No masks.  One group per patch (no mask on the group); each
        text on the patch is a direct vector layer inside the group,
        each with its own affine transform.  The scale normalization
        matches text-shear's, so both modes render each text at the
        same size.

No background layer is created in any mode.
"""

import json
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
            "text": layer_name,
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": font_px,
            "x": text_x,
            "y": text_y,
            "color": sh.get("text_color", "#ffffff"),
            "alignment": text_align,
            "rotation": float(sh.get("flat_rotation", 0.0)),
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
    """One group + one mask per patch.

    Rectangles, when enabled, are emitted first as direct polygons in
    the export group (outside any mask), using each square's projected
    corners verbatim.

    Each text carries its own flat position, size, rotation, and
    alignment, all pre-computed on the JS side so that after the
    patch's mask the text lands at the same screen position, size,
    and angle it would have had in square mode.  The mask's source
    rectangle is extended to cover every text, so Krita's transform
    actually reaches every glyph.
    """
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
            commands.append({
                "type": "add_vector_text",
                "layer_name": content_name,
                "text": str(txt["text"]),
                "font_family": DEFAULT_FONT_FAMILY,
                "font_size": max(6, int(round(float(txt["font_px"])))),
                "x": float(txt["x"]),
                "y": float(txt["y"]),
                "color": txt.get("color", "#ffffff"),
                "alignment": txt.get("alignment", "center"),
                "rotation": float(txt.get("flat_rotation", 0.0)),
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
    """One group + one mask per text.  Each text's mask is a copy of
    the square's own mask: source = the square's full flat rectangle,
    destination = the square's projected corners.  Rectangles are
    direct polygons in the export group, emitted first so they render
    below."""
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
            "text": str(t["name"]),
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": max(6, int(round(float(t["font_px"])))),
            "x": float(t["text_x"]),
            "y": float(t["text_y"]),
            "color": t.get("color", "#ffffff"),
            "alignment": t.get("alignment", "center"),
            "rotation": float(t.get("rotation", 0.0)),
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
    """No masks.  One vector layer per text.  Position (x, y), size
    (font_size), and a full affine transform (rotation + shear +
    non-uniform scale, scale-normalized so the larger of the two
    reaches 1) are pre-computed on the JS side to reproduce the mask-
    based output without using a mask."""
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
            "text": str(t["text"]),
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": max(6, int(round(float(t["font_px"])))),
            "x": float(t["x"]),
            "y": float(t["y"]),
            "color": t.get("color", "#ffffff"),
            "alignment": t.get("alignment", "center"),
            "rotation": 0.0,
            "transform": json.dumps(t["transform"]),
        })

    return {"id": "cone_export", "commands": commands}


def _build_patch_shear_mode(patches, opts, band, doc_name):
    """No masks.  One group per patch; each text inside its patch's
    group as a direct vector layer with its own affine transform.

    Rectangles, when enabled, are direct polygons in the export group,
    emitted first so they render below the texts.

    Group nesting:
        group <batchid>_Export
            vector layer <sq>_rect ...
            group <patch>
                vector layer <sq>_text ...
    """
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
                "text": str(t["text"]),
                "font_family": DEFAULT_FONT_FAMILY,
                "font_size": max(6, int(round(float(t["font_px"])))),
                "x": float(t["x"]),
                "y": float(t["y"]),
                "color": t.get("color", "#ffffff"),
                "alignment": t.get("alignment", "center"),
                "rotation": 0.0,
                "transform": json.dumps(t["transform"]),
            })

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
