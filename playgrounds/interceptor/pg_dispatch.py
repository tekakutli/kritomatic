"""The draw orchestrator and canvas input wiring.

    · Drag empty canvas           → pan (offsetX/Y)
    · Space + drag, middle drag   → pan
    · Wheel at cursor             → zoom (0.05× .. 32×)
    · Drag an edge handle         → expand (delegated to
                                    pg_panel_bindings.js)
    · Home / 0                    → fitView (re-fit and center)
"""

DISPATCH_JS = r"""
import { canvas, board, view, _scheduleDraw } from "./pg_state.js";
import { drawView, zoomAt, edgeHitAt, fitView } from "./pg_view.js";
import { syncPanelFields }                       from "./pg_panel_fields.js";
import {
  handleEdgeMouseDown, handleEdgeMouseMove, handleEdgeMouseUp,
  isEdgeDragging,
} from "./pg_panel_bindings.js";

export function draw() {
  drawView();
  syncPanelFields();
}

let _panDrag   = null;
let _spaceDown = false;

function _startPan(e) {
  _panDrag = {
    startClientX:  e.clientX,
    startClientY:  e.clientY,
    startOffsetX:  view.offsetX,
    startOffsetY:  view.offsetY,
  };
  canvas.style.cursor = "grabbing";
}

function _canExpandNow() {
  const item = board.selectedItem;
  if (!item || !item.payload) return false;
  if (board.view !== "input") return false;
  return (item.payload.output_rotation || 0) === 0;
}

function _hoverCursor(sx, sy) {
  if (_canExpandNow()) {
    const side = edgeHitAt(sx, sy);
    if (side === "left" || side === "right")  return "ew-resize";
    if (side === "top"  || side === "bottom") return "ns-resize";
  }
  return "grab";
}

canvas.addEventListener("mousedown", (e) => {
  const active = document.activeElement;
  if (active && active !== canvas &&
      (active.tagName === "INPUT" || active.tagName === "TEXTAREA" ||
       active.tagName === "SELECT")) {
    active.blur();
  }

  if (e.button === 1 || (e.button === 0 && _spaceDown)) {
    e.preventDefault();
    _startPan(e);
    return;
  }

  if (e.button !== 0) return;

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;

  if (_canExpandNow() && handleEdgeMouseDown(sx, sy)) {
    e.preventDefault();
    return;
  }

  e.preventDefault();
  _startPan(e);
});

window.addEventListener("mousemove", (e) => {
  if (_panDrag) {
    view.offsetX = _panDrag.startOffsetX + (e.clientX - _panDrag.startClientX);
    view.offsetY = _panDrag.startOffsetY + (e.clientY - _panDrag.startClientY);
    _scheduleDraw();
    return;
  }
  if (isEdgeDragging()) {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    if (handleEdgeMouseMove(sx, sy)) e.preventDefault();
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  if (sx >= 0 && sy >= 0
      && sx < canvas.clientWidth && sy < canvas.clientHeight) {
    canvas.style.cursor = _spaceDown ? "grab" : _hoverCursor(sx, sy);
  } else {
    canvas.style.cursor = "default";
  }
});

window.addEventListener("mouseup", (e) => {
  if (_panDrag) {
    _panDrag = null;
    canvas.style.cursor = _spaceDown ? "grab" : "grab";
    return;
  }
  if (isEdgeDragging()) {
    handleEdgeMouseUp();
    return;
  }
});

canvas.addEventListener("wheel", (e) => {
  if (_panDrag || isEdgeDragging()) return;
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const dy = e.deltaY * (e.deltaMode === 1 ? 16
                       : e.deltaMode === 2 ? 100
                       : 1);
  const factor = Math.exp(-dy * 0.0015);
  zoomAt(mx, my, factor);
  _scheduleDraw();
}, { passive: false });

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
            t.tagName === "SELECT")) return;

  if (e.key === " " || e.key === "Spacebar") {
    if (!_spaceDown) {
      _spaceDown = true;
      if (!_panDrag) canvas.style.cursor = "grab";
    }
    e.preventDefault();
    return;
  }
  if (e.key === "Home" || e.key === "0") {
    e.preventDefault();
    fitView();
    _scheduleDraw();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = false;
    if (!_panDrag) canvas.style.cursor = "grab";
  }
});

canvas.style.cursor = "grab";
"""