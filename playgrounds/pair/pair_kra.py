"""
pair_kra.py — turn the pair playground into a .kra.

One export mode.  For each pair of lines, for each segment on the
pair, the client sends

    a TEXT item — the segment's label, carrying a full affine
                  transform that reproduces the trapezoid's local
                  frame at the text's anchor point.  The text is
                  drawn as a plain vector layer with that transform;
                  no transform mask is used.

    a RECT item — the trapezoid itself, drawn as a filled polygon.

Both are drawn as their own vector layers under a single Export
group.  Rectangles are emitted first (so they sit at the bottom of
the group's stack); texts second (so they draw on top).  No
transform masks exist in this export at all — the warp is encoded
entirely in the affine the text carries.

The affine is computed in the client, on the JS side, from the
local Jacobian of the segment's parametric map.  This module simply
forwards the resulting numbers to the daemon.  See pg_kra.py for
the derivation.
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

EXPORT_GROUP_NAME = "Export"


def _normalize_band(raw):
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


def _normalize_options(raw):
    if not isinstance(raw, dict):
        raw = {}
    draw = raw.get("draw_rectangles", True)
    if not isinstance(draw, bool):
        draw = bool(draw)
    return {"draw_rectangles": draw}


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


def _build_bundle(texts, rects, opts, band, doc_name):
    commands = _common_header(band, doc_name)

    # ---- rectangles first (bottom of the group's stack) -----------
    for r in (rects or []):
        layer_name = r.get("name", "rect")
        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "vectorlayer",
            "parent": EXPORT_GROUP_NAME,
        })
        pts = [[round(float(p[0]), 3), round(float(p[1]), 3)]
               for p in r.get("points", [])]
        commands.append({
            "type": "add_vector_polygon",
            "layer_name": layer_name,
            "points": json.dumps(pts),
            "fill": r.get("fill", "#ffffff"),
            "fill_opacity": r.get("fill_opacity", 0.40),
            "stroke": r.get("stroke", "#ffffff"),
            "stroke_width": r.get("stroke_width", 2.0),
            "stroke_opacity": r.get("stroke_opacity", 1.0),
        })

    # ---- texts on top --------------------------------------------
    for t in (texts or []):
        layer_name = t.get("name", "text")
        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "vectorlayer",
            "parent": EXPORT_GROUP_NAME,
        })

        entry = {
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
        }
        tf = _coerce_transform(t.get("transform"))
        if tf is not None:
            entry["transform"] = json.dumps(tf)

        inline = t.get("inline_size")
        if inline is not None:
            try:
                entry["inline_size"] = float(inline)
            except (TypeError, ValueError):
                pass

        commands.append(entry)

    return {"id": "pair_export", "commands": commands}


def generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point called by pair_server's POST handler."""
    texts   = payload.get("texts", [])
    rects   = payload.get("rects", [])
    options = payload.get("options", {})
    band    = payload.get("cone_band", {})
    doc_name = payload.get("doc_name", "Pairs Scene")
    output_path = payload.get("output_path")

    if not texts and not rects:
        return {"success": False, "message": "No segments to export"}
    if not output_path:
        return {"success": False, "message": "No output path provided"}

    out = Path(str(output_path)).expanduser()
    if out.suffix.lower() != ".kra":
        out = out.with_suffix(".kra")

    opts  = _normalize_options(options)
    nband = _normalize_band(band)

    bundle = _build_bundle(texts, rects, opts, nband, doc_name)
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
        "message": f"Wrote {out} ({len(texts)} text(s))",
        "output_path": str(out),
        "results": results,
    }
