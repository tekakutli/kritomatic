"""Numeric constants.

Every JS-side constant that is *not* user-facing state.  This is the
single most stable file in the project: it holds numbers, and only
numbers.  Feature-local constants stay with their feature; only
magnitudes that more than one feature reads belong here.

The op-log cap and the view-zoom bounds are the two the entire
codebase already agreed on; the bbox paddings are read by
`pg_pixels.py` and by `pg_comfy_client.py` through `computeEffectiveBBox`."""

TUNING_JS = r"""
/* ============================================================
   Operation log
   ============================================================ */

export const OP_LOG_MAX = 20;

/* ============================================================
   View zoom / pan
   ============================================================ */

export const VIEW_ZOOM_MIN   = 0.10;
export const VIEW_ZOOM_MAX   = 32.0;
export const VIEW_PAN_MARGIN = 40;

/* ============================================================
   Bbox
   ============================================================
   BBOX_PAD / BBOX_MIN_SIZE are applied to a mask's actual pixel
   bounds when `pg_pixels.computeMaskBBox` snaps them to the image
   coordinate space.  POINT_BBOX_PAD is the extra halo added around
   each positive point when `computeEffectiveBBox` widens a bbox to
   include a fresh click that has no mask yet. */

export const BBOX_PAD       = 4;
export const BBOX_MIN_SIZE  = 8;
export const POINT_BBOX_PAD = 50;
"""
