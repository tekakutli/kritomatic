"""Numeric constants for the magi playground."""

TUNING_JS = r"""
export const VIEW_ZOOM_MIN   = 0.10;
export const VIEW_ZOOM_MAX   = 32.0;
export const VIEW_PAN_MARGIN = 40;

/* Minimum bbox size in normalized units (2% of image dimension). */
export const BBOX_MIN_SIZE = 0.02;

/* Handle hit tolerance in screen pixels. */
export const HANDLE_TOL_PX = 10;

/* Debounce for state sync.  Same window as layout — small enough
   to feel immediate when the user edits, large enough to collapse
   a drag into one POST. */
export const SYNC_DEBOUNCE_MS = 250;
"""