"""Numeric constants.

Single most stable file.  Magnitudes read by more than one feature.
No rotation constants — rmbg strips rotation entirely."""

TUNING_JS = r"""
/* ============================================================
   View zoom / pan
   ============================================================ */

export const VIEW_ZOOM_MIN   = 0.10;
export const VIEW_ZOOM_MAX   = 32.0;
export const VIEW_PAN_MARGIN = 40;

/* ============================================================
   Bbox / mask geometry
   ============================================================ */

export const BBOX_PAD       = 4;
export const BBOX_MIN_SIZE  = 8;
export const POINT_BBOX_PAD = 50;

/* ============================================================
   Sync
   ============================================================
   Debounce for pushes into the rmbg server's cache.  No rotation
   scrub in this playground, so nothing bypasses the debounce. */

export const SYNC_DEBOUNCE_MS = 250;
"""