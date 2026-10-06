"""
cone_kra.py — turn the cone playground's floating squares into a .kra
full of perspective-projected rectangles and/or labels.

The frame of reference for label placement is VISUAL.  The JS side
determines, from the shape's projected corners, which flat corner
corresponds to the requested visual position and sends the resulting
anchor in flat rectangle coordinates as `text_anchor` on each shape.
This module consumes it and scales it by the fit factor.

Structure per export
====================
Everything the export produces ends up inside one outer group at the
top of the document:

    group <EXPORT_GROUP_NAME>
        group <name>                       (one per shape)
            vector layer <name>_content
                [optional] rectangle (W × H) at the origin
                text at sh["text_anchor"], rotated by sh["flat_rotation"]
            transform mask <name>_persp
                a 4-point perspective homography mapping the rectangle's
                corners onto the shape's projected corners
        group <name> ...
        ...

No background layer is created.  The output contains only the
shapes the user authored; opening the .kra over an existing document
does not silently paint over it, and the transparent canvas shows
whatever the user places underneath.

LAYER PLACEMENT
===============
Every `create_layer` in this bundle uses the `parent` argument of the
daemon's create_layer command, naming the group the new layer should
be added to.  That path looks the parent up by name and never reads
the document's active node, so it is not affected by however Krita
settled the active-node state after the previous command.

Options
=======
An `options` object travels in the payload:

    text_position    interpreted by the JS side; not read here
    text_padding     interpreted by the JS side; not read here
    draw_rectangles  if False, skip the add_vector_polygon command
                     for every shape.  Default True.
    text_color_mode  interpreted by the JS side; not read here
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


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


CANVAS_W = 2048
CANVAS_H = 1024
DEFAULT_RESOLUTION = 72
FIT_MARGIN = 60
DEFAULT_FONT_FAMILY = "sans-serif"
FONT_FRACTION = 0.40

# Name of the outer group that holds the whole export.  The daemon's
# batch executor prefixes it with the batch id, so in the document it
# appears as something like "0kgc_Export".
EXPORT_GROUP_NAME = "Export"


def _bbox_of(shapes: List[Dict[str, Any]]) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []
    for s in shapes:
        for p in s["points"]:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    return (min(xs), min(ys), max(xs), max(ys))


def _fit_transform(bbox, canvas_w, canvas_h, margin):
    x0, y0, x1, y1 = bbox
    bw = x1 - x0
    bh = y1 - y0
    if bw <= 1e-9 or bh <= 1e-9:
        return (1.0, canvas_w / 2.0, canvas_h / 2.0)
    avail_w = canvas_w - 2 * margin
    avail_h = canvas_h - 2 * margin
    scale = min(avail_w / bw, avail_h / bh)
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    tx = canvas_w / 2.0 - cx * scale
    ty = canvas_h / 2.0 - cy * scale
    return (scale, tx, ty)


def _apply(p, scale, tx, ty):
    return (p[0] * scale + tx, p[1] * scale + ty)


def _fallback_anchor(W, H):
    """Used only if a shape arrives without a `text_anchor` field —
    e.g. a payload from an older version of the playground."""
    return {"x": W / 2.0, "y": H / 2.0, "alignment": "center"}


def _normalize_options(raw):
    if not isinstance(raw, dict):
        raw = {}
    draw = raw.get("draw_rectangles", True)
    if not isinstance(draw, bool):
        draw = bool(draw)
    return {"draw_rectangles": draw}


def build_bundle(shapes: List[Dict[str, Any]],
                 options: Dict[str, Any] = None,
                 doc_name: str = "Cone Scene") -> Dict[str, Any]:
    opts = _normalize_options(options)

    bbox = _bbox_of(shapes)
    scale, tx, ty = _fit_transform(bbox, CANVAS_W, CANVAS_H, FIT_MARGIN)

    commands: List[Dict[str, Any]] = [
        {
            "type": "create_new_with_dimensions",
            "name": doc_name,
            "width": CANVAS_W,
            "height": CANVAS_H,
            "resolution": DEFAULT_RESOLUTION,
        },
        # Outer group: every shape goes inside this.
        {
            "type": "create_layer",
            "name": EXPORT_GROUP_NAME,
            "layer_type": "grouplayer",
            "position": "top",
        },
    ]

    for sh in shapes:
        layer_name = sh["name"]
        content_name = layer_name + "_content"
        mask_name = layer_name + "_persp"

        fitted = [_apply(p, scale, tx, ty) for p in sh["points"]]
        W = max(1.0, sh["natural_w"] * scale)
        H = max(1.0, sh["natural_h"] * scale)
        font_px = max(6, int(round(min(W, H) * FONT_FRACTION)))

        anchor = sh.get("text_anchor") or _fallback_anchor(W, H)
        text_x = anchor.get("x", W / 2.0) * scale
        text_y = anchor.get("y", H / 2.0) * scale
        text_align = anchor.get("alignment", "center")

        # 1. Shape group, inside the outer group.
        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "grouplayer",
            "parent": EXPORT_GROUP_NAME,
        })

        # 2. Vector layer inside the shape group.
        commands.append({
            "type": "create_layer",
            "name": content_name,
            "layer_type": "vectorlayer",
            "parent": layer_name,
        })

        # 3. Flat rectangle at the origin, if requested.
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

        # 4. Text at the JS-computed anchor.
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

        # 5. Transform mask on the shape group.
        commands.append({
            "type": "create_transform_mask",
            "layer_name": layer_name,
            "mask_name": mask_name,
        })

        # 6. Perspective homography from the flat rectangle's corners
        #    to the projected corners on the canvas.
        dst_pts = [[round(x, 3), round(y, 3)] for x, y in fitted]
        commands.append({
            "type": "set_perspective_transform_mask",
            "mask_name": mask_name,
            "src_points": json.dumps(rect_pts),
            "dst_points": json.dumps(dst_pts),
        })

    return {"id": "cone_export", "commands": commands}


def generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point called by cone_server's POST handler."""
    shapes = payload.get("shapes", [])
    options = payload.get("options", {})
    doc_name = payload.get("doc_name", "Cone Scene")
    output_path = payload.get("output_path")

    if not shapes:
        return {"success": False, "message": "No shapes to export"}
    if not output_path:
        return {"success": False, "message": "No output path provided"}

    out = Path(str(output_path)).expanduser()
    if out.suffix.lower() != ".kra":
        out = out.with_suffix(".kra")

    bundle = build_bundle(shapes, options, doc_name)
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

    return {
        "success": True,
        "message": f"Wrote {out} ({len(shapes)} shape(s))",
        "output_path": str(out),
        "results": results,
    }
