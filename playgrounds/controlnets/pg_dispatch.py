"""draw() orchestrator and canvas mouse handling.

Only pan (space / middle) and zoom (wheel) — this skeleton has no
overlay interaction yet."""

DISPATCH_JS = r"""
import {
  canvas, board, view,
  clampPan, zoomAt, resetView, _scheduleDraw,
} from "./pg_state.js";
import { drawView } from "./pg_view.js";
import { syncPanelFields } from "./pg_panel.js";

export function draw() {
  drawView();
  syncPanelFields();
}

let _panActive = false;
let _panStartSx = 0, _panStartSy = 0;
let _panStartX = 0, _panStartY = 0;
let _spaceDown = false;

canvas.addEventListener("mousedown", (e) => {
  const active = document.activeElement;
  if (active && active !== canvas
      && (active.tagName === "INPUT"
          || active.tagName === "TEXTAREA"
          || active.tagName === "SELECT")) {
    active.blur();
  }

  if (e.button === 1 || (e.button === 0 && _spaceDown)) {
    e.preventDefault();
    _panActive = true;
    _panStartSx = e.clientX;
    _panStartSy = e.clientY;
    _panStartX = view.panX;
    _panStartY = view.panY;
    canvas.style.cursor = "grabbing";
  }
});

canvas.addEventListener("contextmenu", (e) => e.preventDefault());

window.addEventListener("mousemove", (e) => {
  if (_panActive) {
    view.panX = _panStartX + (e.clientX - _panStartSx);
    view.panY = _panStartY + (e.clientY - _panStartSy);
    clampPan();
    drawView();
    return;
  }
  if (_spaceDown) {
    canvas.style.cursor = "grab";
    return;
  }
  canvas.style.cursor = "default";
});

window.addEventListener("mouseup", () => {
  if (_panActive) {
    _panActive = false;
    canvas.style.cursor = _spaceDown ? "grab" : "default";
  }
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const dy = e.deltaY * (e.deltaMode === 1 ? 16
                       : e.deltaMode === 2 ? 100
                       : 1);
  const factor = Math.exp(-dy * 0.0015);
  zoomAt(mx, my, view.zoom * factor);
  drawView();
}, { passive: false });

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
            || t.tagName === "SELECT")) return;

  if (e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    if (!_spaceDown) {
      _spaceDown = true;
      if (!_panActive) canvas.style.cursor = "grab";
    }
    return;
  }
  if (e.key === "Home" || e.key === "0") {
    e.preventDefault();
    resetView();
    _scheduleDraw();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = false;
    if (!_panActive) canvas.style.cursor = "default";
  }
});
"""