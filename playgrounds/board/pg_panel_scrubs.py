"""
pg_panel_scrubs.py — field registrations and DOM bindings.

ES module.  Pure side effect at module load: registers the fields
the remote API exposes and installs the two range sliders
(`cardWSlider`, plus the two per-card scrub inputs
`cardScaleVal` and `cardRotVal`).  No named exports — the boot
module imports this file for its side effects only.

FIELDS REGISTERED HERE
======================
    board.cardWidth       number, driven by the cardWSlider
    view.zoom             number, driven by the wheel
    view.panX             number, driven by canvas pan
    view.panY             number, driven by canvas pan
    card.scale            number, follows the selection
    card.rotation         number, follows the selection

The `card.*` getters return the selected file's value, or the
field's natural empty (1.0 scale, 0° rotation) when nothing is
selected.  Both go through `_sel()` (imported from
`pg_panel_fields.js` as `selectedFile`).

FIX HISTORY
===========
This file used to import three names from `./pg_core.js`:

    import { board, view, selectedFile, _installScrubInput_unused }
      from "./pg_core.js";

Neither `selectedFile` nor `_installScrubInput_unused` is exported
by pg_core.js (`selectedFile` lives in pg_panel_fields.js).  The
browser refuses to hand the module to the boot when any named
import is missing — the whole module graph fails, exactly the bug
class that was blanking the canvas before the fix.  Both names
have been removed; the correct source for `selectedFile` is the
existing `import { selectedFile as _sel } from "./pg_panel_fields.js"`
on the next line, and `_syncCardFields` was pulled in but never
used.

DYNAMIC IMPORTS
===============
None — `draw` is imported statically from `./pg_dispatch.js`.
The cycle pg_dispatch → pg_krita → pg_dispatch is safe because
all sides are `export function`, which is hoisted; the import
resolves to a valid binding even when the modules load in a
cycle.  Every setter here calls `draw()` explicitly, matching
board's "no afterChange hook" convention.
"""

PG_PANEL_SCRUBS_JS = r"""
import { board, view } from "./pg_core.js";
import { selectedFile as _sel } from "./pg_panel_fields.js";
import { draw } from "./pg_dispatch.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

export function syncPanelSliders() {
  const s = document.getElementById("cardWSlider");
  if (s && document.activeElement !== s) s.value = board.defaultWidth;
  updatePanelLabels();
}

export function updatePanelLabels() {
  const v = document.getElementById("cardWVal");
  const target = String(board.defaultWidth);
  if (v && v.textContent !== target) v.textContent = target;
}

pgkitRegisterField("board.cardWidth", {
  get: () => board.defaultWidth,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n) || n <= 0) return;
    const oldDefault = board.defaultWidth;
    if (oldDefault <= 0) board.defaultWidth = n;
    else {
      const factor = n / oldDefault;
      for (const f of board.files) { f.w *= factor; f.h *= factor; }
      board.defaultWidth = n;
    }
    const s = document.getElementById("cardWSlider");
    if (s && document.activeElement !== s) s.value = n;
    updatePanelLabels();
    draw();
  },
  meta: { kind: "number", label: "Card width", min: 120, max: 720 },
});

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
  set: (v) => { const n = parseFloat(v); if (isFinite(n)) { view.panX = n; draw(); } },
  meta: { kind: "number", label: "Pan X" },
});
pgkitRegisterField("view.panY", {
  get: () => view.panY,
  set: (v) => { const n = parseFloat(v); if (isFinite(n)) { view.panY = n; draw(); } },
  meta: { kind: "number", label: "Pan Y" },
});

function _selectedScale() {
  const f = _sel();
  if (!f) return 1;
  return f.w / (board.defaultWidth > 0 ? board.defaultWidth : 1);
}
function _selectedRotationDeg() {
  const f = _sel();
  if (!f) return 0;
  return f.rotation * 180 / Math.PI;
}

pgkitRegisterField("card.scale", {
  get: _selectedScale,
  set: (v) => {
    const f = _sel();
    if (!f) return;
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    const clamped = Math.max(0.05, Math.min(20, n));
    const d = board.defaultWidth > 0 ? board.defaultWidth : 1;
    const oldScale = f.w / d;
    if (oldScale <= 0) return;
    const factor = clamped / oldScale;
    f.w *= factor; f.h *= factor;
    draw();
  },
  meta: { kind: "number", label: "Card scale", min: 0.05, max: 20 },
});

pgkitRegisterField("card.rotation", {
  get: _selectedRotationDeg,
  set: (v) => {
    const f = _sel();
    if (!f) return;
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    f.rotation = n * Math.PI / 180;
    draw();
  },
  meta: { kind: "number", label: "Card rotation (deg)" },
});

const slider = document.getElementById("cardWSlider");
if (slider) slider.addEventListener("input", () => {
  const v = parseInt(slider.value, 10);
  if (!isFinite(v) || v <= 0) return;
  const oldDefault = board.defaultWidth;
  if (oldDefault <= 0) board.defaultWidth = v;
  else {
    const factor = v / oldDefault;
    for (const f of board.files) { f.w *= factor; f.h *= factor; }
    board.defaultWidth = v;
  }
  updatePanelLabels();
  pgkitNotifyLocal("board.cardWidth");
  draw();
});

const scaleInput = document.getElementById("cardScaleVal");
if (scaleInput) _installScrubInput(scaleInput, {
  rate: 0.005, snapStep: 0.25,
  read: () => _selectedScale(),
  write: (raw) => {
    const f = _sel();
    if (!f) return;
    const clamped = Math.max(0.05, Math.min(20, raw));
    const d = board.defaultWidth > 0 ? board.defaultWidth : 1;
    const oldScale = f.w / d;
    if (oldScale <= 0) return;
    const factor = clamped / oldScale;
    f.w *= factor; f.h *= factor;
    pgkitNotifyLocal("card.scale");
  },
});

const rotInput = document.getElementById("cardRotVal");
if (rotInput) _installScrubInput(rotInput, {
  rate: 0.5, snapStep: 15,
  read: () => _selectedRotationDeg(),
  write: (raw) => {
    const f = _sel();
    if (!f) return;
    f.rotation = raw * Math.PI / 180;
    pgkitNotifyLocal("card.rotation");
  },
});
"""
