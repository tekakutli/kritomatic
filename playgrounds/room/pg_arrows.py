"""
pg_arrows.py — aggregator for the wall→plan escape-arrow field.

This file is the aggregator.  The arrow field was originally a single
~3400-line module; it has been split into twelve submodules by
concern, mirroring the split cable_live.py uses for the interactive
half of the playground:

    pg_arrows_tuning    every tuning constant the solver reads, the
                        base-value frozen table, and the two module-
                        scoped flags that survive recursive solves
    pg_arrows_geometry  segment geometry primitives — distances,
                        projections, path segmentation, pair scoring
                        atoms
    pg_arrows_state     the left/right side structures, initial bundle
                        proposals, the route-ref rebuild, and plan-
                        space cluster discovery and Y assignment
    pg_arrows_passes    the state-mutating post-solve passes: cluster
                        attack forcing, cluster unification and
                        dedupe, long-route bundling, force-parallel
                        bundling, long-attack splitting, and crossing
                        reduction
    pg_arrows_scoring   the per-side score: parallel, crossing, tip,
                        attack-parallel, bundle, group, and cluster
                        penalties; the cluster penalty breakdown; and
                        the attack-fan detector
    pg_arrows_layout    the multi-pass layout step — bundle rows,
                        descent columns, peel ranks, bundle fan-out,
                        and the local baseBreak search
    pg_arrows_shapes    candidate generation for three route kinds
                        (orthogonal, bundled attack, cluster attack),
                        the against-others score, and the placement
                        iteration
    pg_arrows_reroute   the high-conflict reroute pass and the
                        post-reroute rebundle
    pg_arrows_solver    the top-level solve, side clone/restore, the
                        refinement chooser, the escape-fan eviction
                        step, and every find-the-next-target helper
    pg_arrows_render    arrowhead drawing, the trunk and destination
                        path builders, the per-route path renderer,
                        route preparation from the wall-segment table,
                        the batch draw, and route hit testing
    pg_arrows_entry     the cache, the topology signature, the two
                        affine transforms that keep the cache valid
                        across viewport changes, and
                        drawWallToPlanArrows — the single entry point
    pg_arrows_diag      the structured diagnostic dump, its per-side
                        breakdown tables, the diag button, and the
                        arrow-visibility toggle

Each module is a Python string.  The aggregator concatenates them
into one <script> tag, so the JS lives in a single flat scope — same
functions, same names, same call sites as before the split.  Only the
Python packaging changed; there is no semantic difference.

The twelve-module split exists so a change to the layout, the scoring,
the reroute pass, or the diagnostic dump only reprints the relevant
module — the aggregator almost never changes.

Viewport-change handling
------------------------
The solver reads only floor-space fields (planX, planY, descentX,
bundleY, ownPts) and the tuning constants.  The four strip-space
fields a route carries — stripX, stripBaseY, stripRouteY, corridorX —
are read only at draw time and hit-test time.

That means a change to either view's transform is answerable from the
cached solution without re-solving, as long as the change is affine on
the strip-space fields (a wall pan or zoom) or affine on the floor-
space fields (a floor pan or zoom).  Both are handled by
_raApplyFloorAffine and _raApplyWallAffine in pg_arrows_entry.  The
solve path is only re-entered when the topology signature changes —
a wall-segment table change, a viewport resize, or a forced resolve —
or when the debounce guard has elapsed and the signature has moved.

The tuning constants are held at their base values via _raSetZoom(1)
at every solve.  They are compared against floor-screen px inside
_raScoreSide and _raSegmentPairScore, where floor zoom is already
accounted for by the geometry itself; coupling them to the wall zoom
was an earlier design choice and is what forced a full re-solve on
every wheel tick of the wall view.
"""

from pg_arrows_tuning   import TUNING_JS
from pg_arrows_geometry import GEO_JS
from pg_arrows_state    import STATE_JS
from pg_arrows_passes   import PASSES_JS
from pg_arrows_scoring  import SCORING_JS
from pg_arrows_layout   import LAYOUT_JS
from pg_arrows_shapes   import SHAPES_JS
from pg_arrows_reroute  import REROUTE_JS
from pg_arrows_solver   import SOLVER_JS
from pg_arrows_render   import RENDER_JS
from pg_arrows_entry    import ENTRY_JS
from pg_arrows_diag     import DIAG_JS


ARROWS_JS = (
      TUNING_JS
    + GEO_JS
    + STATE_JS
    + PASSES_JS
    + SCORING_JS
    + LAYOUT_JS
    + SHAPES_JS
    + REROUTE_JS
    + SOLVER_JS
    + RENDER_JS
    + ENTRY_JS
    + DIAG_JS
)
