"""@thecontrolnet auto-sync."""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_client.js";
import { makeAutosync } from "./pgruntime/js/autosync.js";
import { flashStatus } from "./pgkit/panel_core.js";

const _auto = makeAutosync({
  playgroundId:       "controlnets",
  slotId:             "@thecontrolnet",
  fetchActiveState,
  fetchResourceImage,
  applyCanvasImage,
  onWarn: (msg) => flashStatus(msg, "warn"),
  onDaemonDown: () => flashStatus(
    "daemon unreachable — playground will exit", "bad",
  ),
});

export const fetchSelectedLayer = _auto.fetchSelectedLayer;
export const startAutosync = _auto.start;
"""
