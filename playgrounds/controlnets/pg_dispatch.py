"""
Controlnets dispatch — draw() and canvas event listeners.

Delegates pan/zoom to `installPanZoom` from pgruntime.  The draw
entry point is unchanged: it calls `drawView` then `syncPanelFields`.
"""

DISPATCH_JS = r"""
import { canvas, view, clampPan, zoomAt, resetView, _scheduleDrawView }
  from "./pg_state.js";
import { installPanZoom } from "./pgruntime/js/pan_zoom.js";
import { drawView } from "./pg_view.js";
import { syncPanelFields } from "./pg_panel.js";

export function draw() {
  drawView();
  syncPanelFields();
}

installPanZoom(canvas, {
  view, clampPan, zoomAt, resetView,
  _scheduleDrawView: _scheduleDrawView,
});
"""
