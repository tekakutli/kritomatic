"""
tb_panel_scrubs.py — field registrations and DOM bindings.

ES module.  Pure side effect at module load: registers every field
the remote API exposes and installs the slider + word-box checkbox
listeners.  No named exports — the boot module imports this file for
its side effects only.

FIELDS REGISTERED HERE
======================
    board.cardWidth       number, driven by the slider
    board.showWordBoxes   bool, driven by the checkbox
    view.zoom             number, driven by the wheel
    view.panX             number, driven by canvas pan
    view.panY             number, driven by canvas pan
    item.text             text, follows selection
    item.fontFamily       text, follows selection
    item.fontSize         number, follows selection
    item.color            text, follows selection
    item.alignment        enum (left|center|right), follows selection
    item.rotationDeg      number, follows selection

The item.* getters return the selected item's value, or the field's
natural empty ("" / 0) when nothing is selected.  The item.* setters
route through `applyItemField` in `tb_panel_fields.py` — same code
path as a local editor change — so a remote edit triggers the same
Krita round-trip that a local edit does.  The kit's
`_pgkitApplyingRemote` guard prevents the echo.

DYNAMIC IMPORTS
===============
`import("./tb_dispatch.js").then(m => m.draw())` appears in every
`set` and slider handler.  `tb_dispatch` imports `tb_core` (for
`board`, `view`, etc.); a static import of `draw` here would create
a load-order surprise because `tb_panel_scrubs` is imported bare by
the boot module, before `tb_core` has been evaluated.  Dynamic
import is the simplest non-cyclic path.
"""

TB_PANEL_SCRUBS_JS = r"""
import {
  board, view, selectedItem, _computeBoardScale, _rebaseAllItems,
} from "./tb_core.js";
import { applyItemField, updatePanelLabels } from "./tb_panel_fields.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

/* ==========================================================================
   BOARD.* FIELDS
   ========================================================================== */

pgkitRegisterField("board.cardWidth", {
  get: () => board.defaultWidth,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n) || n <= 0) return;
    board.defaultWidth  = n;
    board._scaleUserSet = true;
    board.scale         = _computeBoardScale();
    _rebaseAllItems();
    updatePanelLabels();
    const s = document.getElementById("cardWSlider");
    if (s && document.activeElement !== s) s.value = n;
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Card width", min: 120, max: 720 },
});

pgkitRegisterField("board.showWordBoxes", {
  get: () => !!board.showWordBoxes,
  set: (v) => {
    board.showWordBoxes = !!v;
    const cb = document.getElementById("wordBoxToggle");
    if (cb && document.activeElement !== cb) cb.checked = board.showWordBoxes;
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "bool", label: "Word boxes" },
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
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Zoom", min: 0.05, max: 8 },
});

pgkitRegisterField("view.panX", {
  get: () => view.panX,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panX = n;
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Pan X" },
});

pgkitRegisterField("view.panY", {
  get: () => view.panY,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panY = n;
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Pan Y" },
});

/* ==========================================================================
   ITEM.* FIELDS
   ========================================================================== */

function _itemGet(key, dflt) {
  const it = selectedItem();
  return it ? it[key] : dflt;
}
function _itemSet(key, value) {
  const it = selectedItem();
  if (!it) return;
  applyItemField(it, key, value);
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
}

pgkitRegisterField("item.text", {
  get: () => _itemGet("text", ""),
  set: (v) => _itemSet("text", String(v == null ? "" : v)),
  meta: { kind: "text", label: "Text" },
});

pgkitRegisterField("item.fontFamily", {
  get: () => _itemGet("fontFamily", ""),
  set: (v) => _itemSet("fontFamily", String(v == null ? "" : v)),
  meta: { kind: "text", label: "Font family" },
});

pgkitRegisterField("item.fontSize", {
  get: () => _itemGet("fontSize", 12),
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    _itemSet("fontSize", n);
  },
  meta: { kind: "number", label: "Font size", min: 1 },
});

pgkitRegisterField("item.color", {
  get: () => _itemGet("color", "#000000"),
  set: (v) => _itemSet("color", String(v == null ? "" : v)),
  meta: { kind: "text", label: "Color" },
});

pgkitRegisterField("item.alignment", {
  get: () => _itemGet("alignment", "left"),
  set: (v) => _itemSet("alignment", String(v == null ? "left" : v)),
  meta: { kind: "enum", label: "Alignment", options: ["left", "center", "right"] },
});

pgkitRegisterField("item.rotationDeg", {
  get: () => _itemGet("rotationDeg", 0),
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    _itemSet("rotationDeg", n);
  },
  meta: { kind: "number", label: "Rotation (deg)" },
});

/* ==========================================================================
   SLIDER + CHECKBOX DOM BINDINGS
   ========================================================================== */

const slider   = document.getElementById("cardWSlider");
const wbToggle = document.getElementById("wordBoxToggle");

if (slider) slider.addEventListener("input", () => {
  const v = parseInt(slider.value, 10);
  if (!isFinite(v) || v <= 0) return;
  board.defaultWidth  = v;
  board._scaleUserSet = true;
  board.scale         = _computeBoardScale();
  _rebaseAllItems();
  updatePanelLabels();
  pgkitNotifyLocal("board.cardWidth");
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
});

if (wbToggle) wbToggle.addEventListener("change", () => {
  board.showWordBoxes = !!wbToggle.checked;
  pgkitNotifyLocal("board.showWordBoxes");
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
});
"""
