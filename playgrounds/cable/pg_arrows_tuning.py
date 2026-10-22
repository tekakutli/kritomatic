"""
pg_arrows_tuning.py — every tuning constant the arrow solver reads,
plus the shared mutable flags that survive across recursive solve
passes.

ES module.  Two groups of exports:

    • the tuning constants — frozen base values in `_RA_BASE`, plus
      the `export let` bindings that `_raSetZoom(k)` rescales.  In
      this build `_raSetZoom` is always called with k=1 (see the
      entry module's note about reverting the zoom coupling), so the
      bindings hold their base values.  The `let` bindings are still
      exported as `let` — not `const` — because `_raSetZoom` writes
      them and an ES module `let` is a live binding importers can
      read from anywhere.

    • `arrowsState` — a `const` object wrapping the two flags the
      solver mutates and the scoring module reads:

          frozenPolylines   the evacuee-side polylines during an
                            escape-fan re-solve.  Set by
                            pg_arrows_solver's `_raEscapeFans`; read
                            by pg_arrows_scoring's `_raScoreSide` and
                            by `_raScoreAgainst` in pg_arrows_shapes.
                            Null outside an escape pass.
          escapeInProgress  true while an escape-fan sub-solve is
                            running.  Prevents the reroute and escape
                            passes from recursing.

In the concatenation model these were top-level `let`s; in ES modules
a `let` export is read-only from the importer's side, so they had to
move onto a const object.
"""

TUNING_JS = r"""
/* ==========================================================================
   MODULE-SCOPED MUTABLE STATE
   ==========================================================================
   Read by pg_arrows_scoring.js and pg_arrows_shapes.js; written by
   pg_arrows_solver.js.  A const object so the bindings are writable
   from any module that imports the object. */

export const arrowsState = {
  frozenPolylines:  null,
  escapeInProgress: false,
};

/* ==========================================================================
   NON-SCALED CONSTANTS
   ==========================================================================
   These do not change with zoom and are not in `_RA_BASE`. */

export const ROUTE_PARALLEL_TOL          = Math.PI / 10;
export const ROUTE_PARALLEL_W            = 30.0;
export const ROUTE_MILD_W                = 3.0;
export const ROUTE_CROSS_W               = 12.0;
export const ROUTE_TIP_W                 = 30.0;

export const SOLVER_MAX_ITERATIONS       = 60;
export const SOLVER_CLUSTER_EARLY_ITER   = 12;
export const SOLVER_GROUP_MAX_ROUTES     = 4;
export const SOLVER_BUNDLE_MAX_ROUTES    = 8;
export const ROUTE_PLACE_PASSES          = 12;
export const ROUTE_GROUP_MAX_K           = 6;

export const ROUTE_ATTACK_BUNDLE_TOL     = Math.PI / 6;
export const ROUTE_CLUSTER_MIN_SIZE      = 3;
export const ROUTE_CLUSTER_LONG_MULT     = 2.2;

export const ROUTE_REROUTE_MAX_CONFLICTS = 3;
export const ROUTE_REROUTE_GRID_STEPS    = 8;
export const ROUTE_REROUTE_REFINE_STEPS  = 3;
export const ROUTE_REROUTE_MAX_PER_SWEEP = 4;
export const REROUTE_LENGTH_W            = 0.3;

export const ROUTE_ESCAPE_ANGLE_TOL      = Math.PI / 6;
export const ROUTE_ESCAPE_MAX_PASSES     = 2;
export const ROUTE_ESCAPE_FROZEN_TAIL    = 2;

export const LONG_ATTACK_THRESHOLD        = 200.0;
export const SAME_COL_TOL                 = 4.0;
export const LONG_ATTACK_SPLIT_THRESHOLD  = 200.0;

export const RA_SOLVE_SIGNATURE_PRECISION = 0.05;
export const RA_SOLVE_DEBOUNCE_MS         = 120;
export const RA_FLOOR_SCALE_STEPS         = 10;

/* ==========================================================================
   BASE VALUES
   ==========================================================================
   Frozen.  `_raSetZoom(k)` reads from this object to compute the
   scaled values, so it is the single source of truth for the
   default magnitudes. */

export const _RA_BASE = Object.freeze({
  ROUTE_MIN_SEP:              12.0,
  ROUTE_MIN_OVERLAP:          10.0,
  ROUTE_ACCEPT_SCORE:         10.0,
  ROUTE_TIP_CLEARANCE:        15.0,
  ROUTE_TIP_QUAD_W:           20.0,

  SOLVER_ACCEPTABLE_SCORE:       40.0,
  SOLVER_PARALLEL_BUDGET:        20.0,
  SOLVER_BUNDLE_SPACING_BUDGET:   5.0,
  SOLVER_CROSSING_BUDGET:        12.0,
  SOLVER_ATTACK_PARALLEL_BUDGET:  8.0,
  SOLVER_CLUSTER_BUDGET:         20.0,
  SOLVER_TIP_BUDGET:             20.0,
  SOLVER_MERGE_THRESHOLD:        22,

  SOLVER_MAX_BUNDLE_SPACING:     96,
  SOLVER_MAX_GROUP_SPACING:      72,
  SOLVER_BUNDLE_SPACING_STEP:     6,
  SOLVER_GROUP_SPACING_STEP:      4,
  ROUTE_BUNDLE_SPACING_MIN:      28,
  ROUTE_BUNDLE_TOP_MARGIN:       44,
  ROUTE_BUNDLE_SCREEN_MIN:       50,
  ROUTE_GROUP_SPACING_MIN:       20,
  ROUTE_GROUP_STEP:              14,
  ROUTE_BUNDLE_GAP_LIMIT:        80,

  ROUTE_LAST_SEG_MIN:            22.0,
  ROUTE_PEEL_STAGGER_MIN:         4.0,
  ROUTE_PEEL_STAGGER_MAX:        28.0,
  ROUTE_PEEL_STAGGER_STEP:        4.0,
  BUNDLE_FAN_STEP:               24.0,
  BUNDLE_PERP_FAN_STEP:          20.0,
  BUNDLED_ATTACK_MIN:            22.0,
  BUNDLE_TINY_ATTACK_MAX:        40.0,
  BUNDLE_TINY_PROX:              14.0,
  BUNDLE_JOG_STEP:               20.0,

  ROUTE_CLUSTER_RADIUS:          90.0,
  ROUTE_CLUSTER_GAP:             36.0,
  ROUTE_CLUSTER_LONG_W:          12.0,
  CLUSTER_DROP_STEP:             15.0,
  CLUSTER_CORRIDOR_STAGGER:       5.0,

  ROUTE_REROUTE_MAX_TOTAL:      180.0,
  ROUTE_REROUTE_SPAN:            80.0,
  ROUTE_REROUTE_ACCEPT:          30.0,
  REROUTE_BEND_W:               100.0,

  ROUTE_ESCAPE_PROX:             10.0,
  ROUTE_ESCAPE_MIN_OVERLAP:      30.0,
});

/* ==========================================================================
   SCALED BINDINGS
   ==========================================================================
   `export let` — a live binding importers read from anywhere; only
   `_raSetZoom` in this module writes them.  In this build
   `_raSetZoom` is always called with k=1. */

export let ROUTE_MIN_SEP              = _RA_BASE.ROUTE_MIN_SEP;
export let ROUTE_MIN_OVERLAP          = _RA_BASE.ROUTE_MIN_OVERLAP;
export let ROUTE_ACCEPT_SCORE         = _RA_BASE.ROUTE_ACCEPT_SCORE;
export let ROUTE_TIP_CLEARANCE        = _RA_BASE.ROUTE_TIP_CLEARANCE;
export let ROUTE_TIP_QUAD_W           = _RA_BASE.ROUTE_TIP_QUAD_W;
export let SOLVER_ACCEPTABLE_SCORE    = _RA_BASE.SOLVER_ACCEPTABLE_SCORE;
export let SOLVER_PARALLEL_BUDGET     = _RA_BASE.SOLVER_PARALLEL_BUDGET;
export let SOLVER_BUNDLE_SPACING_BUDGET = _RA_BASE.SOLVER_BUNDLE_SPACING_BUDGET;
export let SOLVER_CROSSING_BUDGET     = _RA_BASE.SOLVER_CROSSING_BUDGET;
export let SOLVER_ATTACK_PARALLEL_BUDGET = _RA_BASE.SOLVER_ATTACK_PARALLEL_BUDGET;
export let SOLVER_CLUSTER_BUDGET      = _RA_BASE.SOLVER_CLUSTER_BUDGET;
export let SOLVER_TIP_BUDGET          = _RA_BASE.SOLVER_TIP_BUDGET;
export let SOLVER_MERGE_THRESHOLD     = _RA_BASE.SOLVER_MERGE_THRESHOLD;
export let SOLVER_MAX_BUNDLE_SPACING  = _RA_BASE.SOLVER_MAX_BUNDLE_SPACING;
export let SOLVER_MAX_GROUP_SPACING   = _RA_BASE.SOLVER_MAX_GROUP_SPACING;
export let SOLVER_BUNDLE_SPACING_STEP = _RA_BASE.SOLVER_BUNDLE_SPACING_STEP;
export let SOLVER_GROUP_SPACING_STEP  = _RA_BASE.SOLVER_GROUP_SPACING_STEP;
export let ROUTE_BUNDLE_SPACING_MIN   = _RA_BASE.ROUTE_BUNDLE_SPACING_MIN;
export let ROUTE_BUNDLE_TOP_MARGIN    = _RA_BASE.ROUTE_BUNDLE_TOP_MARGIN;
export let ROUTE_BUNDLE_SCREEN_MIN    = _RA_BASE.ROUTE_BUNDLE_SCREEN_MIN;
export let ROUTE_GROUP_SPACING_MIN    = _RA_BASE.ROUTE_GROUP_SPACING_MIN;
export let ROUTE_GROUP_STEP           = _RA_BASE.ROUTE_GROUP_STEP;
export let ROUTE_BUNDLE_GAP_LIMIT     = _RA_BASE.ROUTE_BUNDLE_GAP_LIMIT;
export let ROUTE_LAST_SEG_MIN         = _RA_BASE.ROUTE_LAST_SEG_MIN;
export let ROUTE_PEEL_STAGGER_MIN     = _RA_BASE.ROUTE_PEEL_STAGGER_MIN;
export let ROUTE_PEEL_STAGGER_MAX     = _RA_BASE.ROUTE_PEEL_STAGGER_MAX;
export let ROUTE_PEEL_STAGGER_STEP    = _RA_BASE.ROUTE_PEEL_STAGGER_STEP;
export let BUNDLE_FAN_STEP            = _RA_BASE.BUNDLE_FAN_STEP;
export let BUNDLE_PERP_FAN_STEP       = _RA_BASE.BUNDLE_PERP_FAN_STEP;
export let BUNDLED_ATTACK_MIN         = _RA_BASE.BUNDLED_ATTACK_MIN;
export let BUNDLE_TINY_ATTACK_MAX     = _RA_BASE.BUNDLE_TINY_ATTACK_MAX;
export let BUNDLE_TINY_PROX           = _RA_BASE.BUNDLE_TINY_PROX;
export let BUNDLE_JOG_STEP            = _RA_BASE.BUNDLE_JOG_STEP;
export let ROUTE_CLUSTER_RADIUS       = _RA_BASE.ROUTE_CLUSTER_RADIUS;
export let ROUTE_CLUSTER_GAP          = _RA_BASE.ROUTE_CLUSTER_GAP;
export let ROUTE_CLUSTER_LONG_W       = _RA_BASE.ROUTE_CLUSTER_LONG_W;
export let CLUSTER_DROP_STEP          = _RA_BASE.CLUSTER_DROP_STEP;
export let CLUSTER_CORRIDOR_STAGGER   = _RA_BASE.CLUSTER_CORRIDOR_STAGGER;
export let ROUTE_REROUTE_MAX_TOTAL    = _RA_BASE.ROUTE_REROUTE_MAX_TOTAL;
export let ROUTE_REROUTE_SPAN         = _RA_BASE.ROUTE_REROUTE_SPAN;
export let ROUTE_REROUTE_ACCEPT       = _RA_BASE.ROUTE_REROUTE_ACCEPT;
export let REROUTE_BEND_W             = _RA_BASE.REROUTE_BEND_W;
export let ROUTE_ESCAPE_PROX          = _RA_BASE.ROUTE_ESCAPE_PROX;
export let ROUTE_ESCAPE_MIN_OVERLAP   = _RA_BASE.ROUTE_ESCAPE_MIN_OVERLAP;

/* ==========================================================================
   ZOOM RECALIBRATION
   ==========================================================================
   The entry module calls `_raSetZoom(1)` at the start of every solve,
   resetting every binding to its base value.  Kept as a real setter
   so a future caller that wants to scale by k has a hook, even
   though the current build never uses it. */

export function _raSetZoom(k) {
  if (!isFinite(k) || k <= 0) k = 1;

  ROUTE_MIN_SEP              = _RA_BASE.ROUTE_MIN_SEP              * k;
  ROUTE_MIN_OVERLAP          = _RA_BASE.ROUTE_MIN_OVERLAP          * k;
  ROUTE_ACCEPT_SCORE         = _RA_BASE.ROUTE_ACCEPT_SCORE         * k;
  ROUTE_TIP_CLEARANCE        = _RA_BASE.ROUTE_TIP_CLEARANCE        * k;
  ROUTE_TIP_QUAD_W           = _RA_BASE.ROUTE_TIP_QUAD_W           / k;

  SOLVER_ACCEPTABLE_SCORE    = _RA_BASE.SOLVER_ACCEPTABLE_SCORE    * k;
  SOLVER_PARALLEL_BUDGET     = _RA_BASE.SOLVER_PARALLEL_BUDGET     * k;
  SOLVER_BUNDLE_SPACING_BUDGET =
    _RA_BASE.SOLVER_BUNDLE_SPACING_BUDGET * k;
  SOLVER_CROSSING_BUDGET     = _RA_BASE.SOLVER_CROSSING_BUDGET     * k;
  SOLVER_ATTACK_PARALLEL_BUDGET =
    _RA_BASE.SOLVER_ATTACK_PARALLEL_BUDGET * k;
  SOLVER_CLUSTER_BUDGET      = _RA_BASE.SOLVER_CLUSTER_BUDGET      * k;
  SOLVER_TIP_BUDGET          = _RA_BASE.SOLVER_TIP_BUDGET          * k;
  SOLVER_MERGE_THRESHOLD     = _RA_BASE.SOLVER_MERGE_THRESHOLD     * k;

  SOLVER_MAX_BUNDLE_SPACING  = _RA_BASE.SOLVER_MAX_BUNDLE_SPACING  * k;
  SOLVER_MAX_GROUP_SPACING   = _RA_BASE.SOLVER_MAX_GROUP_SPACING   * k;
  SOLVER_BUNDLE_SPACING_STEP = _RA_BASE.SOLVER_BUNDLE_SPACING_STEP * k;
  SOLVER_GROUP_SPACING_STEP  = _RA_BASE.SOLVER_GROUP_SPACING_STEP  * k;
  ROUTE_BUNDLE_SPACING_MIN   = _RA_BASE.ROUTE_BUNDLE_SPACING_MIN   * k;
  ROUTE_BUNDLE_TOP_MARGIN    = _RA_BASE.ROUTE_BUNDLE_TOP_MARGIN    * k;
  ROUTE_BUNDLE_SCREEN_MIN    = _RA_BASE.ROUTE_BUNDLE_SCREEN_MIN    * k;
  ROUTE_GROUP_SPACING_MIN    = _RA_BASE.ROUTE_GROUP_SPACING_MIN    * k;
  ROUTE_GROUP_STEP           = _RA_BASE.ROUTE_GROUP_STEP           * k;
  ROUTE_BUNDLE_GAP_LIMIT     = _RA_BASE.ROUTE_BUNDLE_GAP_LIMIT     * k;

  ROUTE_LAST_SEG_MIN         = _RA_BASE.ROUTE_LAST_SEG_MIN         * k;
  ROUTE_PEEL_STAGGER_MIN     = _RA_BASE.ROUTE_PEEL_STAGGER_MIN     * k;
  ROUTE_PEEL_STAGGER_MAX     = _RA_BASE.ROUTE_PEEL_STAGGER_MAX     * k;
  ROUTE_PEEL_STAGGER_STEP    = _RA_BASE.ROUTE_PEEL_STAGGER_STEP    * k;
  BUNDLE_FAN_STEP            = _RA_BASE.BUNDLE_FAN_STEP            * k;
  BUNDLE_PERP_FAN_STEP       = _RA_BASE.BUNDLE_PERP_FAN_STEP       * k;
  BUNDLED_ATTACK_MIN         = _RA_BASE.BUNDLED_ATTACK_MIN         * k;
  BUNDLE_TINY_ATTACK_MAX     = _RA_BASE.BUNDLE_TINY_ATTACK_MAX     * k;
  BUNDLE_TINY_PROX           = _RA_BASE.BUNDLE_TINY_PROX           * k;
  BUNDLE_JOG_STEP            = _RA_BASE.BUNDLE_JOG_STEP            * k;

  ROUTE_CLUSTER_RADIUS       = _RA_BASE.ROUTE_CLUSTER_RADIUS       * k;
  ROUTE_CLUSTER_GAP          = _RA_BASE.ROUTE_CLUSTER_GAP          * k;
  ROUTE_CLUSTER_LONG_W       = _RA_BASE.ROUTE_CLUSTER_LONG_W       * k;
  CLUSTER_DROP_STEP          = _RA_BASE.CLUSTER_DROP_STEP          * k;
  CLUSTER_CORRIDOR_STAGGER   = _RA_BASE.CLUSTER_CORRIDOR_STAGGER   * k;

  ROUTE_REROUTE_MAX_TOTAL    = _RA_BASE.ROUTE_REROUTE_MAX_TOTAL    * k;
  ROUTE_REROUTE_SPAN         = _RA_BASE.ROUTE_REROUTE_SPAN         * k;
  ROUTE_REROUTE_ACCEPT       = _RA_BASE.ROUTE_REROUTE_ACCEPT       * k;
  REROUTE_BEND_W             = _RA_BASE.REROUTE_BEND_W             * k;

  ROUTE_ESCAPE_PROX          = _RA_BASE.ROUTE_ESCAPE_PROX          * k;
  ROUTE_ESCAPE_MIN_OVERLAP   = _RA_BASE.ROUTE_ESCAPE_MIN_OVERLAP   * k;
}
"""
