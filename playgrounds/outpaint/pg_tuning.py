"""Numeric constants for the outpaint playground."""

TUNING_JS = r"""
export const VIEW_ZOOM_MIN   = 0.10;
export const VIEW_ZOOM_MAX   = 32.0;
export const VIEW_PAN_MARGIN = 40;

/* Minimum viewer size in area pixels.  The viewer can never be
 * smaller than this in either dimension. */
export const VIEWER_MIN_SIZE_PX = 8;

/* Debounce for state sync. */
export const SYNC_DEBOUNCE_MS = 250;
"""