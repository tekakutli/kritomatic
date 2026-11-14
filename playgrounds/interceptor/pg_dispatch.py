"""The draw orchestrator and canvas input wiring.

    · drag empty canvas         → pan
    · Space + drag              → pan
    · middle drag               → pan
    · Wheel at cursor           → zoom
    · Home / 0                  → fit view

PAN / ZOOM USE `_scheduleDrawView`, NOT `_scheduleDraw`
=======================================================
Panning and zooming change only `view.zoom / offsetX / offsetY` —
pure canvas state.  The panel fields and the mask/area cards are
entirely unaffected.  Routing these through `_scheduleDraw` (which
runs `draw()` = `drawView()` + `syncPanelFields()`) would schedule a
panel sync that is guaranteed to be a no-op, on every animation
frame of a pan.  `_scheduleDrawView` calls `drawView()` only — the
sig-based layer dispatch inside it already skips the raw image blit
when nothing about the image changed, and the panel sync is not
called at all.

The panel sync happens through `_scheduleDraw` (called from
`pg_panel_bindings.js`) when an actual panel-state change occurs —
task selection, task list refresh, task status updates, result
arrival.  Those are rare, discrete events; the correctness of the
split is not sensitive to timing.

The old edge-drag / expansion-feature input paths are gone.  There
is no longer an "expand the crop" interaction — that whole concept
lived in the pre-refactor interceptor and depended on re-preparing
the workflow, which is not part of the new architecture.  If the
user wants more context than the daemon's crop provided, they
re-run with different crop padding in the docker.
"""

DISPATCH_JS = r"""
import {
  canvas, board, view, _scheduleDrawView,
} from "./pg_state.js";
import { drawView, zoomAt, fitView } from "./pg_view.js";
import { syncPanelFields }      from "./pg_panel_fields.js";

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
  e.preventDefault();
  _startPan(e);
});

window.addEventListener("mousemove", (e) => {
  if (_panDrag) {
    view.offsetX = _panDrag.startOffsetX + (e.clientX - _panDrag.startClientX);
    view.offsetY = _panDrag.startOffsetY + (e.clientY - _panDrag.startClientY);
    _scheduleDrawView();
    return;
  }
  canvas.style.cursor = _spaceDown ? "grab" : "grab";
});

window.addEventListener("mouseup", () => {
  if (_panDrag) {
    _panDrag = null;
    canvas.style.cursor = "grab";
  }
});

canvas.addEventListener("wheel", (e) => {
  if (_panDrag) return;
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const dy = e.deltaY * (e.deltaMode === 1 ? 16
                       : e.deltaMode === 2 ? 100
                       : 1);
  const factor = Math.exp(-dy * 0.0015);
  zoomAt(mx, my, factor);
  _scheduleDrawView();
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
    _scheduleDrawView();
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