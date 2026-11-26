"""
text_wall_kra.py — turn the text_wall playground into a .kra.

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

The scaffolding shared with `cone` — constants, the batch bootstrap,
`normalize_band`, `common_header`, `coerce_transform`,
`prepare_output_path`, and the batch-execute-and-summarize pattern —
lives in `pgruntime.kra_export`.  What stays here is the command
emission itself: `_build_bundle` and the text_wall-specific option
`draw_rectangles`.  See `pgruntime/kra_export.py`'s module
docstring for the layering.

The affine is computed in the client, on the JS side, from the
local Jacobian of the segment's parametric map.  This module simply
forwards the resulting numbers to the daemon.  See pg_kra.py for
the derivation.
"""

import json
from typing import Any, Dict

from pgruntime import kra_export


def _normalize_options(raw):
    """text_wall-specific option normalization.  Only `draw_rectangles`
    is user-settable; cone's `text_warp_mode` doesn't apply here."""
    if not isinstance(raw, dict):
        raw = {}
    draw = raw.get("draw_rectangles", True)
    if not isinstance(draw, bool):
        draw = bool(draw)
    return {"draw_rectangles": draw}


def _build_bundle(texts, rects, opts, band, doc_name):
    commands = kra_export.common_header(band, doc_name)

    # ---- rectangles first (bottom of the group's stack) -----------
    for r in (rects or []):
        layer_name = r.get("name", "rect")
        commands.append({
            "type": "create_layer",
            "name": layer_name,
            "layer_type": "vectorlayer",
            "parent": kra_export.EXPORT_GROUP_NAME,
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
            "parent": kra_export.EXPORT_GROUP_NAME,
        })

        entry = {
            "type": "add_vector_text",
            "layer_name": layer_name,
            "text": str(t.get("text", t.get("label", ""))),
            "font_family": kra_export.DEFAULT_FONT_FAMILY,
            "font_size": max(6, int(round(float(t["font_px"])))),
            "x": float(t["x"]),
            "y": float(t["y"]),
            "color": t.get("color", "#ffffff"),
            "alignment": t.get("alignment", "center"),
            "rotation": 0.0,
        }
        tf = kra_export.coerce_transform(t.get("transform"))
        if tf is not None:
            entry["transform"] = json.dumps(tf)

        inline = t.get("inline_size")
        if inline is not None:
            try:
                entry["inline_size"] = float(inline)
            except (TypeError, ValueError):
                pass

        commands.append(entry)

    return {"id": "text_wall_export", "commands": commands}


def generate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Entry point called by text_wall_playground's POST handler."""
    texts   = payload.get("texts", [])
    rects   = payload.get("rects", [])
    options = payload.get("options", {})
    band    = payload.get("cone_band", {})
    doc_name = payload.get("doc_name", "Text Wall Scene")
    output_path = payload.get("output_path")

    if not texts and not rects:
        return {"success": False, "message": "No segments to export"}
    if not output_path:
        return {"success": False, "message": "No output path provided"}

    out = kra_export.prepare_output_path(output_path)

    opts  = _normalize_options(options)
    nband = kra_export.normalize_band(band)

    bundle = _build_bundle(texts, rects, opts, nband, doc_name)

    return kra_export.execute_bundle(
        bundle, out, count=len(texts), kind="text(s)",
    )