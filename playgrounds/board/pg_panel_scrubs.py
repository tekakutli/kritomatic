"""
pg_panel_scrubs.py — pgkit field registrations and slider/scrub
bindings.

Every field the remote panel sees is registered here:

    board.cardWidth     number, via the slider below
    card.scale          number, per selected card
    card.rotation       number, per selected card (degrees)
    view.zoom           number, driven by wheel
    view.panX           number, driven by canvas pan
    view.panY           number, driven by canvas pan

`syncPanelSliders` and `updatePanelLabels` are called from
`boardBootSync` and `applyBoardScene`; they read
`board.defaultWidth` into the slider position and the readout.
"""

PG_PANEL_SCRUBS_JS = r"""
/* ==========================================================================
   SLIDER SYNC
   ========================================================================== */

function syncPanelSliders() {
  const s = document.getElementById("cardWSlider");
  if (s && document.activeElement !== s) s.value = board.defaultWidth;
  updatePanelLabels();
}

function updatePanelLabels() {
  const v = document.getElementById("cardWVal");
  const target = String(board.defaultWidth);
  if (v && v.textContent !== target) v.textContent = target;
}

/* ==========================================================================
   BOARD.* FIELDS
   ========================================================================== */

pgkitRegisterField("board.cardWidth", {
  get: () => board.defaultWidth,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n) || n <= 0) return;
    const oldDefault = board.defaultWidth;
    if (oldDefault <= 0) {
      board.defaultWidth = n;
    } else {
      const factor = n / oldDefault;
      for (const f of board.files) {
        f.w *= factor;
        f.h *= factor;
      }
      board.defaultWidth = n;
    }
    const s = document.getElementById("cardWSlider");
    if (s && document.activeElement !== s) s.value = n;
    updatePanelLabels();
    draw();
  },
  meta: { kind: "number", label: "Card width", min: 120, max: 720 },
});

/* ==========================================================================
   VIEW.* FIELDS
   ========================================================================== */

pgkitRegisterField("view.zoom", {
  get: () => view.zoom,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.zoom = Math.max(0.05, Math.min(8, n));
    draw();
  },
  meta: { kind: "number", label: "Zoom", min: 0.05, max: 8 },
});

pgkitRegisterField("view.panX", {
  get: () => view.panX,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panX = n;
    draw();
  },
  meta: { kind: "number", label: "Pan X" },
});

pgkitRegisterField("view.panY", {
  get: () => view.panY,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panY = n;
    draw();
  },
  meta: { kind: "number", label: "Pan Y" },
});

/* ==========================================================================
   CARD.* FIELDS — FOLLOW SELECTION
   ========================================================================== */

function _selectedScale() {
  const f = selectedFile();
  if (!f) return 1;
  const d = board.defaultWidth > 0 ? board.defaultWidth : 1;
  return f.w / d;
}

function _selectedRotationDeg() {
  const f = selectedFile();
  if (!f) return 0;
  return f.rotation * 180 / Math.PI;
}

pgkitRegisterField("card.scale", {
  get: _selectedScale,
  set: (v) => {
    const f = selectedFile();
    if (!f) return;
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    const clamped = Math.max(0.05, Math.min(20, n));
    const d = board.defaultWidth > 0 ? board.defaultWidth : 1;
    const oldScale = f.w / d;
    if (oldScale <= 0) return;
    const factor = clamped / oldScale;
    f.w *= factor;
    f.h *= factor;
    draw();
  },
  meta: { kind: "number", label: "Card scale", min: 0.05, max: 20 },
});

pgkitRegisterField("card.rotation", {
  get: _selectedRotationDeg,
  set: (v) => {
    const f = selectedFile();
    if (!f) return;
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    f.rotation = n * Math.PI / 180;
    draw();
  },
  meta: { kind: "number", label: "Card rotation (deg)" },
});

/* ==========================================================================
   DOM BINDINGS
   ========================================================================== */

(function installSliderAndScrubBindings() {
  const slider = document.getElementById("cardWSlider");
  if (slider) slider.addEventListener("input", () => {
    const v = parseInt(slider.value, 10);
    if (!isFinite(v) || v <= 0) return;
    const oldDefault = board.defaultWidth;
    if (oldDefault <= 0) {
      board.defaultWidth = v;
    } else {
      const factor = v / oldDefault;
      for (const f of board.files) {
        f.w *= factor;
        f.h *= factor;
      }
      board.defaultWidth = v;
    }
    updatePanelLabels();
    pgkitNotifyLocal("board.cardWidth");
    draw();
  });

  const scaleInput = document.getElementById("cardScaleVal");
  if (scaleInput) _installScrubInput(scaleInput, {
    rate:     0.005,
    snapStep: 0.25,
    read: () => _selectedScale(),
    write: (raw) => {
      const f = selectedFile();
      if (!f) return;
      const clamped = Math.max(0.05, Math.min(20, raw));
      const d = board.defaultWidth > 0 ? board.defaultWidth : 1;
      const oldScale = f.w / d;
      if (oldScale <= 0) return;
      const factor = clamped / oldScale;
      f.w *= factor;
      f.h *= factor;
      pgkitNotifyLocal("card.scale");
    },
  });

  const rotInput = document.getElementById("cardRotVal");
  if (rotInput) _installScrubInput(rotInput, {
    rate:     0.5,
    snapStep: 15,
    read: () => _selectedRotationDeg(),
    write: (raw) => {
      const f = selectedFile();
      if (!f) return;
      f.rotation = raw * Math.PI / 180;
      pgkitNotifyLocal("card.rotation");
    },
  });
})();
"""
