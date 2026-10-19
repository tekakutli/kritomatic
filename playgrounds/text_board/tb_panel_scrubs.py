"""
tb_panel_scrubs.py — pgkit field registrations and small DOM bindings.

Every field the remote panel sees is registered here, in one place:

    board.cardWidth       number, via the slider below.
    board.showWordBoxes   bool, via the checkbox below.
    view.zoom             number, driven by the wheel.
    view.panX, view.panY  number, driven by canvas pan.
    item.text             text, follows selection.
    item.fontFamily       text, follows selection.
    item.fontSize         number, follows selection.
    item.color            text, follows selection.
    item.alignment        enum (left|center|right), follows selection.
    item.rotationDeg      number, follows selection.

The item.* getters return empty / zero when nothing is selected.
The item.* setters route through `_applyItemField` in
tb_panel_fields.py, which mutates the model, queues the Krita
patch, and fires pgkitNotifyLocal — safe on both the local and
remote path thanks to pgkit's `_pgkitApplyingRemote` guard.

The slider and checkbox bindings live here too, since they drive
the two board.* fields.
"""

TB_PANEL_SCRUBS_JS = r"""
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
    _boardScale         = _computeBoardScale();
    _rebaseAllItems();
    updatePanelLabels();
    const s = document.getElementById("cardWSlider");
    if (s && document.activeElement !== s) s.value = n;
    draw();
  },
  meta: { kind: "number", label: "Card width", min: 120, max: 720 },
});

pgkitRegisterField("board.showWordBoxes", {
  get: () => !!board.showWordBoxes,
  set: (v) => {
    board.showWordBoxes = !!v;
    const cb = document.getElementById("wordBoxToggle");
    if (cb && document.activeElement !== cb) cb.checked = board.showWordBoxes;
    draw();
  },
  meta: { kind: "bool", label: "Word boxes" },
});

/* ==========================================================================
   VIEW.* FIELDS
   ==========================================================================
   The model lives in `view`; the canvas wheel and pan handlers are
   the only writers, and both already call pgkitNotifyLocal after
   their mutation (see tb_dispatch.py).  The `set` here is a plain
   model write followed by a draw — sufficient for the remote-patch
   path. */

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
   ITEM.* FIELDS — FOLLOW SELECTION
   ==========================================================================
   The getters return the selected item's value, or the field's
   natural empty ("" / 0) when nothing is selected.  The setters
   route through _applyItemField in tb_panel_fields.py, which handles
   the model write, the Krita patch, and the notify. */

function _itemGet(key, dflt) {
  const it = selectedItem();
  return it ? it[key] : dflt;
}

function _itemSet(key, value) {
  const it = selectedItem();
  if (!it) return;
  _applyItemField(it, key, value);
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
  meta: {
    kind: "enum",
    label: "Alignment",
    options: ["left", "center", "right"],
  },
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

(function installSliderAndCheckboxBindings() {
  const slider   = document.getElementById("cardWSlider");
  const wbToggle = document.getElementById("wordBoxToggle");

  if (slider) slider.addEventListener("input", () => {
    const v = parseInt(slider.value, 10);
    if (!isFinite(v) || v <= 0) return;
    board.defaultWidth  = v;
    board._scaleUserSet = true;
    _boardScale         = _computeBoardScale();
    _rebaseAllItems();
    updatePanelLabels();
    pgkitNotifyLocal("board.cardWidth");
    draw();
  });

  if (wbToggle) wbToggle.addEventListener("change", () => {
    board.showWordBoxes = !!wbToggle.checked;
    pgkitNotifyLocal("board.showWordBoxes");
    draw();
  });
})();
"""
