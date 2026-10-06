"""
cone_kra.py — turn the cone playground's floating squares into a .kra
full of polygon shapes, each carrying its own label.

The playground runs in the browser; the only way it can reach Krita
is the local HTTP server in cone_server.py, which forwards to this
module.  This module then talks to the Kritomatic daemon via the same
client the CLI uses, exactly as captionize_krita.py does.

Mapping
=======
Each shape arrives with two payloads:

    points   the shape's cone-view quadrilateral, in the cone band's
             own canvas pixels (perspective-tapered, horizon-clipped,
             slope-offset — the same polygon the cone band draws).

    label    the shape's display name, the polygon's centroid, a
             font size in screen pixels, and a rotation in degrees.

The union of every shape's corner points is used as a bounding box,
and that box is mapped onto a fixed canvas with a uniform scale and
a small margin.  The polygon points, the label position, and the
label font size are all run through the same transform, so the
labels scale with the shapes and stay inside them.

Each shape becomes ONE vector layer holding two SVG shapes: the
polygon (fill + stroke), then the text (centred on the polygon's
centroid, rotated to the shape's own axis).
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _find_repo_src() -> Path:
    """Walk up from this file to the directory containing src/kritomatic."""
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
        pts = [_apply(p, scale, tx, ty) for p in sh["points"]]
        points_json = json.dumps(
            [[round(x, 3), round(y, 3)] for x, y in pts]
        )

        layer_name = sh["name"]

        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "vectorlayer",
            "position": "top",
        })
        commands.append({
            "type": "add_vector_polygon",
            "layer_name": layer_name,
            "points": points_json,
            "fill": sh.get("fill", "#ffffff"),
            "fill_opacity": sh.get("fill_opacity", 0.40),
            "stroke": sh.get("stroke", "#ffffff"),
            "stroke_width": sh.get("stroke_width", 2.0),
            "stroke_opacity": sh.get("stroke_opacity", 1.0),
        })

        label = sh.get("label")
        if label and label.get("text"):
            lx, ly = _apply((label["x"], label["y"]), scale, tx, ty)
            font_px = max(6, int(round(label["font_px"] * scale)))

            commands.append({
                "type": "add_vector_text",
                "layer_name": layer_name,
                "text": str(label["text"]),
                "font_family": label.get("font_family", DEFAULT_FONT_FAMILY),
                "font_size": font_px,
                "x": lx,
                "y": ly,
                "color": label.get("color", "#ffffff"),
                "alignment": "center",
                "rotation": float(label.get("rotation", 0.0)),
            })

    return {"id": "cone_export", "commands": commands}


def generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point called by cone_server's POST handler.

    Expected payload:

        {
          "doc_name":    "Cone Scene",
          "output_path": "/abs/path/out.kra",
          "shapes": [
            {
              "name":           "S1",
              "points":         [[x0,y0],[x1,y1],[x2,y2],[x3,y3]],
              "fill":           "#ffc85a",
              "fill_opacity":   0.40,
              "stroke":         "#ffc85a",
              "stroke_width":   2.0,
              "stroke_opacity": 1.0,
              "label": {
                "text":     "S1",
                "x":        512.0,
                "y":        300.0,
                "font_px":  24.0,
                "rotation": -30.0,
                "color":    "#ffd9a0"
              }
            },
            ...
          ]
        }
    """
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
