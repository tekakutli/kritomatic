"""
cone_kra.py — turn the cone playground's floating squares into a .kra
full of perspective-projected rectangles with rotated labels.

The playground runs in the browser; the only way it can reach Krita
is the local HTTP server in cone_server.py, which forwards to this
module.  This module then talks to the Kritomatic daemon via the same
client the CLI uses, exactly as captionize_krita.py does.

Structure per shape
===================
Each floating square becomes one group in the .kra:

    group <name>
        vector layer <name>_content
            rectangle  (W × H) at the origin
            horizontal-in-the-flat-frame text at the rectangle's
            centre, rotated by `flat_rotation`
        transform mask <name>_persp
            a 4-point perspective homography mapping the rectangle's
            four corners onto the shape's four projected corners

The rectangle and the text are laid out on a clean, axis-aligned
plane.  The text's rotation in that plane is not zero: it is chosen
by the caller (see pg_kra.py's _shapeLabelFlatRotationDeg) so that,
after the perspective transform, the text runs along the shape's
longer projected side and reads floor-is-down.

Correspondence
==============
The flat rectangle's corners are laid out as

    (0, 0), (W, 0), (W, H), (0, H)

in the order TL, TR, BR, BL — matching the JS side's corner order
for the destination points.
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
BACKGROUND_COLOR = "#0a0e14"
FIT_MARGIN = 60
DEFAULT_FONT_FAMILY = "sans-serif"
FONT_FRACTION = 0.40


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


def build_bundle(shapes: List[Dict[str, Any]],
                 doc_name: str = "Cone Scene") -> Dict[str, Any]:
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
        {
            "type": "create_layer",
            "name": "background",
            "layer_type": "paintlayer",
            "position": "bottom",
        },
        {
            "type": "fill_layer",
            "layer_name": "background",
            "color": BACKGROUND_COLOR,
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

        # 1. Group
        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "grouplayer",
            "position": "top",
        })

        # 2. Vector layer inside the group
        commands.append({
            "type": "create_layer",
            "name": content_name,
            "layer_type": "vectorlayer",
            "position": "inside_active",
        })

        # 3. Flat rectangle at the origin
        rect_pts = [[0.0, 0.0], [W, 0.0], [W, H], [0.0, H]]
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

        # 4. Text at the rectangle's centre, pre-rotated by
        #    `flat_rotation` so that after the perspective transform
        #    its baseline lies along the shape's longer projected
        #    side and its reading sense is floor-is-down.
        commands.append({
            "type": "add_vector_text",
            "layer_name": content_name,
            "text": layer_name,
            "font_family": DEFAULT_FONT_FAMILY,
            "font_size": font_px,
            "x": W / 2.0,
            "y": H / 2.0,
            "color": sh.get("text_color", "#ffffff"),
            "alignment": "center",
            "rotation": float(sh.get("flat_rotation", 0.0)),
        })

        # 5. Transform mask on the group
        commands.append({
            "type": "create_transform_mask",
            "layer_name": layer_name,
            "mask_name": mask_name,
        })

        # 6. Perspective homography from the flat rectangle's corners
        #    to the projected corners on the canvas.
        src_pts = rect_pts
        dst_pts = [[round(x, 3), round(y, 3)] for x, y in fitted]
        commands.append({
            "type": "set_perspective_transform_mask",
            "mask_name": mask_name,
            "src_points": json.dumps(src_pts),
            "dst_points": json.dumps(dst_pts),
        })

    return {"id": "cone_export", "commands": commands}


def generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point called by cone_server's POST handler."""
    shapes = payload.get("shapes", [])
    doc_name = payload.get("doc_name", "Cone Scene")
    output_path = payload.get("output_path")

    if not shapes:
        return {"success": False, "message": "No shapes to export"}
    if not output_path:
        return {"success": False, "message": "No output path provided"}

    out = Path(str(output_path)).expanduser()
    if out.suffix.lower() != ".kra":
        out = out.with_suffix(".kra")

    bundle = build_bundle(shapes, doc_name)
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
