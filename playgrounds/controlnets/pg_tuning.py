"""Numeric constants for the controlnets playground."""

TUNING_JS = r"""
export const VIEW_ZOOM_MIN   = 0.10;
export const VIEW_ZOOM_MAX   = 32.0;
export const VIEW_PAN_MARGIN = 40;

/* Debounce for state sync — same window as multimask/magi. */
export const SYNC_DEBOUNCE_MS = 250;
"""