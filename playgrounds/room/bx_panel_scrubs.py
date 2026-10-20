"""
bx_panel_scrubs.py — field registrations and DOM bindings.

ES module.  Pure side effect at module load: registers every value
the remote API exposes and installs the slider + checkbox listeners.

FIELDS REGISTERED HERE
======================
    board.cardWidth        number, driven by the card width slider
    box.placeWidth         number, driven by the "W" preset input
    box.placeHeight        number, driven by the "H" preset input
    box.placeRotation      number, driven by the "Rot" preset input
    box.selected.name      text, follows selection
    box.selected.width     number, follows selection
    box.selected.height    number, follows selection
    box.selected.rotationDeg number, follows selection
    box.drawMode           bool, reads drawBoxMode
    view.zoom              number, driven by the wheel
    view.panX              number, driven by canvas pan
    view.panY              number, driven by canvas pan

Every selection-scoped `box.selected.*` setter calls
`tryApplyBoxChange` (in bx_edit) so the wall gate applies whether the
edit came from the panel or the remote.  The wall gate returns a
boolean; on failure we flash the same message the local editor does,
so the remote and the local side agree about what happened.

DYNAMIC IMPORTS
===============
`import("./bx_dispatch.js").then(m => m.draw())` appears in every
setter.  A static import of `draw` from this module would create a
cycle: `bx_dispatch` imports `bx_panel_bindings`, which imports this
module.  Dynamic imports are the simplest non-cyclic path.
"""

PANEL_SCRUBS_JS = r"""
import {
  board, view, selectedItem, state,
  boxOverlapsWalls, invalidateBox, getBoxParams, setRotation,
} from "./bx_core.js";
import { tryApplyBoxChange } from "./bx_edit.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { T } from "./bx_i18n.js";

/* ==========================================================================
   BOARD.CARDWIDTH — the reference width slider
   ========================================================================== */

pgkitRegisterField("board.cardWidth", {
  get: () => board.defaultWidth,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n) || n <= 0) return;
    const oldDefault = board.defaultWidth;
    if (oldDefault <= 0) board.defaultWidth = n;
    else {
      const factor = n / oldDefault;
      for (const b of board.items) { b.w *= factor; b.h *= factor; invalidateBox(b); }
      board.defaultWidth = n;
    }
    const s = document.getElementById("boxW");
    if (s && document.activeElement !== s) s.value = n;
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Card width", min: 120, max: 720 },
});

/* ==========================================================================
   BOX.PLACE* — the GUIDES strip's W / H / Rot presets
   ========================================================================== */

pgkitRegisterField("box.placeWidth", {
  get: () => {
    const el = document.getElementById("boxW");
    return el ? parseFloat(el.value) || 100 : 100;
  },
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n) || n <= 0) return;
    const el = document.getElementById("boxW");
    if (el && document.activeElement !== el) el.value = n;
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Preset W", min: 10 },
});

pgkitRegisterField("box.placeHeight", {
  get: () => {
    const el = document.getElementById("boxH");
    return el ? parseFloat(el.value) || 100 : 100;
  },
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n) || n <= 0) return;
    const el = document.getElementById("boxH");
    if (el && document.activeElement !== el) el.value = n;
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Preset H", min: 10 },
});

pgkitRegisterField("box.placeRotation", {
  get: () => {
    const el = document.getElementById("boxRot");
    return el ? parseFloat(el.value) || 0 : 0;
  },
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    setRotation(n);
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Preset rotation (deg)" },
});

/* ==========================================================================
   BOX.SELECTED.* — selection-scoped
   ========================================================================== */

function _selectedWidth() {
  const b = selectedItem();
  return b ? b.w : 0;
}
function _selectedHeight() {
  const b = selectedItem();
  return b ? b.h : 0;
}
function _selectedRotationDeg() {
  const b = selectedItem();
  return b ? b.rot : 0;
}
function _selectedName() {
  const b = selectedItem();
  return b ? (b.name || "") : "";
}

pgkitRegisterField("box.selected.name", {
  get: _selectedName,
  set: (v) => {
    const b = selectedItem();
    if (!b) return;
    const name = String(v == null ? "" : v);
    if (b.name === name) return;
    b.name = name;
    const el = document.getElementById("boxName");
    if (el && document.activeElement !== el) el.value = name;
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "text", label: "Selected name" },
});

pgkitRegisterField("box.selected.width", {
  get: _selectedWidth,
  set: (v) => {
    const b = selectedItem();
    if (!b) return;
    const n = parseFloat(v);
    if (!isFinite(n) || n <= 0) return;
    const ok = tryApplyBoxChange(b, { w: Math.max(10, n) });
    if (!ok) { flashStatus(T("msgWallBlocked"), "bad"); return; }
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Selected W", min: 10 },
});

pgkitRegisterField("box.selected.height", {
  get: _selectedHeight,
  set: (v) => {
    const b = selectedItem();
    if (!b) return;
    const n = parseFloat(v);
    if (!isFinite(n) || n <= 0) return;
    const ok = tryApplyBoxChange(b, { h: Math.max(10, n) });
    if (!ok) { flashStatus(T("msgWallBlocked"), "bad"); return; }
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Selected H", min: 10 },
});

pgkitRegisterField("box.selected.rotationDeg", {
  get: _selectedRotationDeg,
  set: (v) => {
    const b = selectedItem();
    if (!b) return;
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    const newRot = ((n % 360) + 360) % 360;
    const ok = tryApplyBoxChange(b, { rot: newRot });
    if (!ok) { flashStatus(T("msgRotationBlocked"), "bad"); return; }
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Selected rotation (deg)" },
});

/* ==========================================================================
   BOX.DRAWMODE — reads and toggles state.dragBoxMode
   ========================================================================== */

pgkitRegisterField("box.drawMode", {
  get: () => !!state.dragBoxMode,
  set: (v) => {
    import("./bx_dispatch.js").then(m => m.setDrawBoxMode(!!v)).catch(() => {});
  },
  meta: { kind: "bool", label: "Draw box mode" },
});

/* ==========================================================================
   VIEW.*
   ========================================================================== */

pgkitRegisterField("view.zoom", {
  get: () => view.zoom,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.zoom = Math.max(0.05, Math.min(8, n));
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Zoom", min: 0.05, max: 8 },
});

pgkitRegisterField("view.panX", {
  get: () => view.panX,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panX = n;
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Pan X" },
});

pgkitRegisterField("view.panY", {
  get: () => view.panY,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panY = n;
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Pan Y" },
});

/* ==========================================================================
   DOM BINDINGS — slider + checkboxes (the checkboxes that remain
   in the panel body; the "Word boxes" toggle was removed from the
   boxes playground so there is nothing to bind here)
   ========================================================================== */

const cardWSlider = document.getElementById("cardWSlider");
if (cardWSlider) cardWSlider.addEventListener("input", () => {
  const v = parseInt(cardWSlider.value, 10);
  if (!isFinite(v) || v <= 0) return;
  const oldDefault = board.defaultWidth;
  if (oldDefault <= 0) board.defaultWidth = v;
  else {
    const factor = v / oldDefault;
    for (const b of board.items) { b.w *= factor; b.h *= factor; invalidateBox(b); }
    board.defaultWidth = v;
  }
  pgkitNotifyLocal("board.cardWidth");
  import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
});
"""
