"""
cone_kra.py — turn the cone playground's floating squares into a .kra
full of perspective-projected rectangles and/or labels.

The .kra reproduces the cone playground's NORMAL view: the same canvas
dimensions as the visible cone band, the same shape positions, the
same apparent sizes.  The shape corners in the payload are already in
the cone band's pixel coordinates — the JS side computes them through
projectShapePoint, which uses the current view transform — so the
daemon sizes the canvas to match the cone band and uses those
coordinates verbatim.  No fit transform, and no extra outer transform:
both the cone band and the Krita canvas share the same origin at the
top-left and the same y-down axis, so the mapping is identity.

If the user changes the cone depth, the half-angle, the shape depth
slider, or pans, all of those effects are already baked into the
coordinates the JS side sends.  The .kra always reflects the current
point of view.

Structure per export
====================
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

No background layer is created.  The output contains only the shapes
the user authored; opening the .kra over an existing document does not
silently paint over it.

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


# Fallback canvas dimensions, used only if the payload arrives without
# a `cone_band` field (an older browser tab, or a hand-crafted payload).
DEFAULT_CANVAS_W = 1920
DEFAULT_CANVAS_H = 1080

DEFAULT_RESOLUTION = 72
DEFAULT_FONT_FAMILY = "sans-serif"
FONT_FRACTION = 0.40

# Name of the outer group that holds the whole export.  The daemon's
# batch executor prefixes it with the batch id, so in the document it
# appears as something like "0kgc_Export".
EXPORT_GROUP_NAME = "Export"


def _fallback_anchor(W, H):
    """Used only if a shape arrives without a `text_anchor` field."""
    return {"x": W / 2.0, "y": H / 2.0, "alignment": "center"}


def _normalize_options(raw):
    if not isinstance(raw, dict):
        raw = {}
    draw = raw.get("draw_rectangles", True)
    if not isinstance(draw, bool):
        draw = bool(draw)
    return {"draw_rectangles": draw}


def _normalize_cone_band(raw):
    """Pull canvas dimensions out of the payload's `cone_band` field,
    with safe fallbacks for anything missing or malformed."""
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


def build_bundle(shapes: List[Dict[str, Any]],
                 options: Dict[str, Any] = None,
                 cone_band: Dict[str, Any] = None,
                 doc_name: str = "Cone Scene") -> Dict[str, Any]:
    opts = _normalize_options(options)
    band = _normalize_cone_band(cone_band)

    commands: List[Dict[str, Any]] = [
        {
            "type": "create_new_with_dimensions",
            "name": doc_name,
            "width": band["width"],
            "height": band["height"],
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

        # Shape corners, in cone-band screen pixels.  Used verbatim:
        # the canvas is the same size as the cone band, so these are
        # also the shape corners on the canvas.
        dst_pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
                   for p in sh["points"]]

        W = max(1.0, float(sh["natural_w"]))
        H = max(1.0, float(sh["natural_h"]))
        font_px = max(6, int(round(min(W, H) * FONT_FRACTION)))

        anchor = sh.get("text_anchor") or _fallback_anchor(W, H)
        text_x = float(anchor.get("x", W / 2.0))
        text_y = float(anchor.get("y", H / 2.0))
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
    cone_band = payload.get("cone_band", {})
    doc_name = payload.get("doc_name", "Cone Scene")
    output_path = payload.get("output_path")

    if not shapes:
        return {"success": False, "message": "No shapes to export"}
    if not output_path:
        return {"success": False, "message": "No output path provided"}

    out = Path(str(output_path)).expanduser()
    if out.suffix.lower() != ".kra":
        out = out.with_suffix(".kra")

    bundle = build_bundle(shapes, options, cone_band, doc_name)
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
