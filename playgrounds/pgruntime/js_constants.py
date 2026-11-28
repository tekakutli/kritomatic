"""
Shared JS numeric constants.  Only the ones that are byte-identical
across multiple playgrounds live here; per-playground constants stay
in that playground's pg_tuning.py.

Why some obvious candidates are absent:

  · BBOX_MIN_SIZE — magi and layout use 0.02 in normalized units;
    segment and inpaint use 8 in pixels.  Same name, different unit.
    Do not share until renamed.

  · HANDLE_TOL_PX — magi and layout use 10; segment and inpaint
    don't have the concept.  Two consumer files; kept local.
"""

CONSTANTS_JS = r"""
/* ==========================================================================
   Every playground's view bounds.
   ========================================================================== */

export const VIEW_ZOOM_MIN   = 0.10;
export const VIEW_ZOOM_MAX   = 32.0;
export const VIEW_PAN_MARGIN = 40;

/* ==========================================================================
   Debounce for state sync (magi, layout, controlnets, inpaint).
   ========================================================================== */

export const SYNC_DEBOUNCE_MS = 250;

/* ==========================================================================
   Op log cap (segment, inpaint).
   ========================================================================== */

export const OP_LOG_MAX = 20;

/* ==========================================================================
   Pixel-space bbox constants (segment, inpaint).
   ========================================================================== */

export const BBOX_PAD         = 4;
export const BBOX_MIN_SIZE_PX = 8;
export const POINT_BBOX_PAD   = 50;
"""