PAN_ZOOM_JS = r"""
/* ==========================================================================
   installPanZoom — standard pan (middle / space+left) and zoom (wheel).

   Opts:
     view, clampPan, zoomAt, resetView   — from the state bundle
     _scheduleDrawView                   — canvas-only scheduler
     onBeforeMouseDown(e)                — return true to claim the
                                           event before pan logic runs
     onPanStart, onPanEnd, onZoom        — optional notifications
     panButton = "middle-or-space"
     resetKey  = "Home"
     resetKeys = ["Home", "0"]
   ========================================================================== */

export function installPanZoom(canvasEl, opts) {
  const {
    view, clampPan, zoomAt, resetView,
    _scheduleDrawView,
    onBeforeMouseDown = null,
    onPanStart = null,
    onPanEnd = null,
    onZoom = null,
    resetKeys = ["Home", "0"],
  } = opts;

  let _panActive = false;
  let _panStartSx = 0, _panStartSy = 0;
  let _panStartX = 0, _panStartY = 0;
  let _spaceDown = false;

  canvasEl.addEventListener("mousedown", (e) => {
    if (onBeforeMouseDown && onBeforeMouseDown(e)) return;

    if (e.button === 1 || (e.button === 0 && _spaceDown)) {
      e.preventDefault();
      _panActive = true;
      _panStartSx = e.clientX;
      _panStartSy = e.clientY;
      _panStartX = view.panX;
      _panStartY = view.panY;
      canvasEl.style.cursor = "grabbing";
      if (onPanStart) onPanStart();
      return;
    }
  });

  canvasEl.addEventListener("mousemove", (e) => {
    if (!_panActive) return;
    view.panX = _panStartX + (e.clientX - _panStartSx);
    view.panY = _panStartY + (e.clientY - _panStartSy);
    clampPan();
    _scheduleDrawView();
  });

  window.addEventListener("mouseup", () => {
    if (_panActive) {
      _panActive = false;
      canvasEl.style.cursor = _spaceDown ? "grab" : "crosshair";
      if (onPanEnd) onPanEnd();
    }
  });

  canvasEl.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = canvasEl.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1);
    const factor = Math.exp(-dy * 0.0015);
    zoomAt(mx, my, view.zoom * factor);
    if (onZoom) onZoom(view.zoom);
    _scheduleDrawView();
  }, { passive: false });

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
              || t.tagName === "SELECT")) return;
    if (e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      if (!_spaceDown) {
        _spaceDown = true;
        if (!_panActive) canvasEl.style.cursor = "grab";
      }
      return;
    }
    if (resetKeys.indexOf(e.key) >= 0) {
      e.preventDefault();
      resetView();
      _scheduleDrawView();
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.key === " " || e.key === "Spacebar") {
      _spaceDown = false;
      if (!_panActive) canvasEl.style.cursor = "crosshair";
    }
  });

  canvasEl.style.cursor = "crosshair";
}
"""