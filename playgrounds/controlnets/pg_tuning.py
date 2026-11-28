"""Numeric constants for the controlnets playground.

Re-exports the universal constants from pgruntime/js/constants.js.
Any controlnets-specific constants stay below the re-export; the
playground currently has none.
"""

TUNING_JS = r"""
export {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
  SYNC_DEBOUNCE_MS,
} from "./pgruntime/js/constants.js";
"""
