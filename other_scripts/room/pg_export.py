"""
pg_export.py — aggregator for the print-friendly cable-run exporter.

This file is the aggregator.  The exporter was originally a single
~2000-line module; it has been split into fourteen submodules by
concern, mirroring the split pg_arrows.py uses for the wall→plan
escape-arrow field:

    pg_export_textures    the four print-friendly hatch patterns
                          (void, step, room-interior, and the two
                          drawing-side textures)
    pg_export_linearize   anchor graph → renderable data.  The
                          collinear-anchor filter, the true-cable
                          linearizer, the run grouper, the wall-run
                          layout, the segment directory, and the
                          per-pill JSON metadata builder
    pg_export_primitives  the drawing atoms every renderer shares:
                          the badge, the arrow style table, the path
                          trims, the arrow sampler, the shared label
                          geometry constants, and refineLeaderOffsets
    pg_export_geometry    the pure distance and segment-pair atoms,
                          plus the segment-conflict sweep
    pg_export_snapshot    _buildLayout and its consumers
    pg_export_penalties   the tier-list penalty functions
    pg_export_style       the style-conflict graph
    pg_export_optimizer   the leader-geometry optimiser (JS fallback)
    pg_export_pillpush    the escape valve and the polish loop
                          (JS fallback)
    pg_export_labels      the vertex label placement and draw pass
    pg_export_render      the crop, the rescale, and the main
                          renderCableRunToCanvas
    pg_export_preview     the preview page
    pg_export_rust        the bridge to the Rust leader optimiser
    pg_export_entry       the export button and the entry point

Rust leader optimiser
---------------------
The three passes that used to be the slowest part of an export —
refineLeaderOffsets, polishLeaderLayout, hugOverhangingLeaders —
now run in a compiled Rust binary spawned by the Python server as
POST /optimize-leaders.  pg_export_rust.py carries the JS bridge
that hands `placed` + the obstacle context to that endpoint and
splices the result back into the JS state.  The JS-side versions of
those three functions are still present (in pg_export_primitives,
pg_export_pillpush, pg_export_labels) so the export still works
when the Rust binary is unavailable — the bridge falls back to the
in-page optimiser in that case.  See pg_export_rust.py for the wire
format and pg_export_labels.computeVertexLabelPlacement for the
call site.

Deliberately NOT translated (in any module of this split):

    • wall-segment names — W1, W3, C1.W, SR.edge[1] — these are IDs.
    • pill contents — "V<n>", "h <cm>", "W <cm> . E <cm>".
    • the "..." run-gap separator.

Every user-facing string routes through T() in pg_i18n.py.
"""

from pg_export_textures  import TEXTURES_JS
from pg_export_linearize import LINEARIZE_JS
from pg_export_primitives import PRIMITIVES_JS
from pg_export_geometry  import GEO_JS
from pg_export_snapshot  import SNAPSHOT_JS
from pg_export_penalties import PENALTIES_JS
from pg_export_style     import STYLE_JS
from pg_export_optimizer import OPTIMIZER_JS
from pg_export_pillpush  import PILLPUSH_JS
from pg_export_labels    import LABELS_JS
from pg_export_render    import RENDER_JS
from pg_export_preview   import PREVIEW_JS
from pg_export_rust      import RUST_JS
from pg_export_entry     import ENTRY_JS


EXPORT_JS = (
      TEXTURES_JS
    + LINEARIZE_JS
    + PRIMITIVES_JS
    + GEO_JS
    + SNAPSHOT_JS
    + PENALTIES_JS
    + STYLE_JS
    + OPTIMIZER_JS
    + PILLPUSH_JS
    + LABELS_JS
    + RENDER_JS
    + PREVIEW_JS
    + RUST_JS
    + ENTRY_JS
)
