"""Numeric constants.

Every JS-side constant that is *not* user-facing state.  This is the
single most stable file in the project: it holds numbers, and only
numbers.  Feature-local constants stay with their feature; only
magnitudes that more than one feature reads belong here.

The op-log cap and the view-zoom bounds are the two the entire
codebase already agreed on; the bbox paddings are read by
`pg_pixels.py` and by `pg_comfy_client.py` through `computeEffectiveBBox`.
SYNC_DEBOUNCE_MS is the debounce window for pushes of playground state
into Krita scratch layers — small enough to feel immediate when the
user edits, large enough to collapse a burst of edits into one push."""

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
   ============================================================ */

export const BBOX_PAD       = 4;
export const BBOX_MIN_SIZE  = 8;
export const POINT_BBOX_PAD = 50;

/* ============================================================
   Sync
   ============================================================
   Debounce for state pushes.  Rotation changes bypass this — the
   rotation slider calls syncNow() directly so a rotate-then-Run
   sequence has no measurable stale window. */

export const SYNC_DEBOUNCE_MS = 250;
"""
