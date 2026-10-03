"""
floor_plan.py — render the 2D floor-plan visualization of a room.

    python room.py        → writes room_walls.json (and room_dimensions.json,
                            room.step, room.stl)
    python floor_plan.py  → writes floor_plan.svg (and .png if cairosvg
                            is installed); also writes
                            floor_plan_layout.json when DUMP_LAYOUT_META
                            is set to True

Reads both room_walls.json and room_dimensions.json — the first for
geometry, the second for the declared dimension annotations.  Does not
import build123d.  The heavy lifting lives in:

    floor_plan_geometry   reads the two sidecars into a geometry dict
    floor_plan_layout     label placement, dimension-line geometry,
                          notes-block placement
    floor_plan_draw       SVG string generation
    floor_plan_png        SVG → PNG via cairosvg (optional)
    floor_plan_i18n       the translation table and the T() accessor

Language
--------
Set LANG in floor_plan_i18n.py.  That is the only switch.  Every
message the project writes to stdout — the progress lines below,
the notes-placement summary, the SVG / PNG status lines, the error
messages — routes through T(), as does the notes-block heading
drawn into the plan itself.  See floor_plan_i18n.py for the full
list of keys and for how to add another language.
"""

import json

import floor_plan_geometry
import floor_plan_layout
import floor_plan_draw
import floor_plan_png
from floor_plan_i18n import T


SVG_FILE          = "floor_plan.svg"
PNG_FILE          = "floor_plan.png"
LAYOUT_META_FILE  = "floor_plan_layout.json"

# When True, a structured JSON dump of the final label layout is
# written alongside the SVG.  It carries, per label: the preferred
# position, the current position, the displacement, the pairwise
# overlap area with every other label it collides with, and the
# total obstacle penetration.  A summary block reports the total
# overlap area and the indices of every label still overlapping.
# Useful for diagnosing tight or crowded placements without having
# to eyeball the SVG.
DUMP_LAYOUT_META = False


def main():
    geom = floor_plan_geometry.extract_from_json()
    print(T("readInputs")(
        floor_plan_geometry.DEFAULT_WALLS_FILE,
        floor_plan_geometry.DEFAULT_DIMENSIONS_FILE))
    print(T("countPlanFaces")(len(geom["planFaces"])))
    print(T("countColumns")(len(geom["columns"])))
    print(T("countSteps")(len(geom["steps"])))
    print(T("countOpenings")(len(geom["openings"])))
    print(T("countDimensions")(len(geom["dimensions"])))
    print(T("countHoles")(len(geom["holes"])))

    layout = floor_plan_layout.build_layout(geom)
    print(T("countLabels")(len(layout["labels"])))
    print(T("notesPlacement")(layout["notes_placement"]))

    if layout.get("meta"):
        summary = layout["meta"]["summary"]
        n_ov = summary["n_overlap_pairs"]
        pen = summary["total_obstacle_penetration"]
        if n_ov == 0:
            print(T("layoutClean")(pen))
        else:
            n_ov_labels = len(summary["labels_with_overlap"])
            print(T("layoutOverlaps")(
                n_ov, n_ov_labels, summary["total_pair_overlap"]))

    svg = floor_plan_draw.render(geom, layout)
    with open(SVG_FILE, "w", encoding="utf-8") as f:
        f.write(svg)
    print(T("wroteSvg")(SVG_FILE))

    if DUMP_LAYOUT_META and layout.get("meta"):
        with open(LAYOUT_META_FILE, "w", encoding="utf-8") as f:
            json.dump(layout["meta"], f, indent=2)
        print(T("wroteLayoutMeta")(LAYOUT_META_FILE))

    if floor_plan_png.try_export(SVG_FILE, PNG_FILE):
        print(T("wrotePng")(PNG_FILE))


if __name__ == "__main__":
    main()
