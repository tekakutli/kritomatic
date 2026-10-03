"""
boxes_panel.py — the panel's HTML, dark stylesheet, and JS.

Three exports feed three concatenation slots:

    HTML_HEAD  — the panel markup, empty canvas, light base CSS
    CORE_JS    — placeholder (the model lives in bx_core.py)
    PANEL_JS   — installVerticalMenu + dark stylesheet + BOX inspector +
                 instructions popover + updatePanelOpacity +
                 updateTitleBlock
    BOOT_JS    — wires button handlers, kicks off the initial load
    HTML_TAIL  — closes the script tag and body

The panel is modelled on cable_playground's: dark, monospaced,
vertical, with ruled section labels.  Sections this panel uses:

    GUIDES   contextual — hidden unless draw-box mode is armed.
             Contains the ghost W / H / Rot fields and the four
             rotation buttons.
    DRAW     the draw-box tool toggle
    BOX      conditional — the selected box's editor
    EDIT     Save / Undo / Clear all
    OUTPUT   Print PNG / List boxes
    (title block)
    STATUS   fixed-height status line at the bottom

Contextual GUIDES
-----------------
The GUIDES section is the "what will a click place" configurator, so
it only matters when a click would actually place something — i.e.
while draw-box mode is armed.  Two independent pieces implement the
hiding:

    • every GUIDES element carries a `.guidesItem` class, added at
      load time by installVerticalMenu;
    • updateDrawBoxButton in bx_core.py toggles a `.draw-mode` class
      on the #ui root each time the mode flips;
    • a single CSS rule, #ui:not(.draw-mode) .guidesItem { display:
      none }, does the hiding.

The inputs' VALUES persist across mode toggles for free: display:none
does not clear an input's value, and nothing in the code resets it.
Do NOT add save/restore logic for these fields — it would be the only
way to lose them.

Instructions popover
--------------------
The verbose usage notes that used to sit under the panel as a visible
block now live in a floating modal, reached by the `?` button in the
panel header (or the `?` key).  The source of truth is now the
`popInstructionsHTML` key in bx_i18n.py; installInstructionsPopover
reads it directly.

Escape handling note
--------------------
The aggregator concatenates bx_dispatch.py's JS before this module,
so bx_dispatch's bubble-phase Escape listener (which disarms draw-box
mode) would otherwise run before the popover's own Escape listener
and close the popover AND disarm the mode in a single keypress.  The
popover installs its Escape listener in the CAPTURE phase instead, so
it fires first and calls stopPropagation when the popover is open.
The same trick applies to the `?` key, but only in the bubble phase
since nothing else competes for it.

Translation
-----------
Every string this module writes into the DOM — the relabelled
buttons, the section labels, the title block's field names and values,
the inspector chrome, the popover, the restore button — comes from
T() in bx_i18n.py.  The HTML_HEAD literals are first-paint
placeholders only: installVerticalMenu relabels everything that
matters.  The only literals left after the install pass are CSS class
names, HTML tag names, and a handful of em-dash/emoji glyphs.

Field layouts
-------------
Two field-row layouts:

    .fieldRow     a horizontal row of compact labels+inputs.  Used by
                  the Name field of the BOX inspector.

    .fieldColumn  a vertical stack of label+input rows, label flush
                  left at a fixed inset, input filling the rest of
                  the panel.  Used by the GUIDES strip at the top and
                  by the W / H / Rot trio inside the BOX inspector.

The distinction is deliberately kept: the Name field is a single
input and reads fine at full width either way, so it stays as the
compact form.  Every numeric trio is stacked.
"""


HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Box placement playground</title>
<style>
  html, body { margin:0; padding:0; height:100%; overflow:hidden; }
  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    background:rgba(255,255,255,0.96); padding:12px 14px;
    border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.18);
    font-size:13px; user-select:none; width:340px;
  }
  #ui.collapsed { display: none; }
  #ui h3 { margin:0 0 8px; font-size:14px; font-weight:600;
           display:flex; align-items:center; gap:6px; }
  #collapseBtn {
    margin-left:auto;
    padding:1px 8px; font-size:13px; font-weight:700; line-height:1.4;
    cursor:pointer; border:1px solid #bbb; background:#f7f7f7;
    border-radius:4px; color:#374151;
  }
  #collapseBtn:hover { background:#e5e7eb; }
  #restoreBtn {
    position:absolute; top:12px; left:12px; z-index:10;
    display:none;
    padding:6px 12px; cursor:pointer;
    border:1px solid #bbb; background:rgba(255,255,255,0.96);
    border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.18);
    font-size:12px; font-weight:600; color:#374151;
  }
  #restoreBtn:hover { background:#f3f4f6; }
  #restoreBtn.visible { display:block; }
  #ui .row { margin:6px 0; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  #ui label { display:inline-flex; align-items:center; gap:4px; }
  #ui input[type=number] { width:65px; padding:3px 5px; border:1px solid #bbb;
                           border-radius:4px; font-size:12px; }
  #ui input[type=text] { width:100%; padding:4px 6px; border:1px solid #bbb;
                         border-radius:4px; font-size:12px; box-sizing:border-box; }
  #ui input:disabled { background:#f3f3f3; color:#999; }
  #ui button { padding:5px 9px; cursor:pointer; border:1px solid #bbb;
               background:#f7f7f7; border-radius:4px; font-size:12px; }
  #ui button:hover { background:#eee; }
  #ui button.primary { background:#000000; color:#ffffff; border-color:#000000;
                       font-weight:600; }
  #ui button.primary:hover { background:#222222; }
  #ui hr { border:0; border-top:1px solid #eee; margin:9px 0; }
  #status { margin-top:8px; font-size:12px; min-height:16px; font-weight:600;
            font-family: ${FONT_MONO}; }
  #status.ok  { color:#15803d; }
  #status.bad { color:#dc2626; }
  #status.warn{ color:#ea580c; }
  canvas { display:block; touch-action:none; background:#fafafa; }
</style>
</head>
<body>
<div id="ui">
  <h3>Box planner
      <button id="collapseBtn" title="Collapse panel (H)">−</button></h3>
  <div class="row">
    <label>W <input type="number" id="boxW" value="2000" step="100"></label>
    <label>H <input type="number" id="boxH" value="1500" step="100"></label>
    <label>Rot <input type="number" id="boxRot" value="0" step="15">°</label>
  </div>
  <div class="row">
    <button id="rotL90">⟲ 90°</button>
    <button id="rotR90">⟳ 90°</button>
    <button id="rotL15">⟲ 15°</button>
    <button id="rotR15">⟳ 15°</button>
  </div>
  <div class="row">
    <button id="drawBoxBtn">✏️ Draw box</button>
  </div>
  <div class="row">
    <button id="saveBtn" class="primary">💾 Save</button>
    <button id="undoBtn">↶ Undo</button>
    <button id="clearBtn">🗑 Clear all</button>
  </div>
  <div class="row">
    <button id="pngBtn">🖼 Print PNG</button>
    <button id="listBtn">📋 List boxes</button>
  </div>
  <hr>
  <div id="status"></div>
</div>
<button id="restoreBtn" title="Show panel (H)">☰ Show panel</button>
<canvas id="c"></canvas>
"""


CORE_JS = r"""
/* Model and state live in bx_core.py — the aggregator concatenates
   that module before this one.  This placeholder keeps the import
   contract intact and is a no-op. */
"""


PANEL_JS = r"""
/* ==========================================================================
   VERTICAL MENU PANEL
   ==========================================================================
   Reshapes the base markup into the dark, monospaced, sectioned form.
   Runs once at load, after bx_dispatch.py has defined `draw`.

   Every human-readable string this block writes into the DOM comes
   from T() in bx_i18n.py.  Switching LANG there switches every
   button, tooltip, section label, title-block field name, and
   inspector caption the panel shows. */

(function installVerticalMenu() {
  const style = document.createElement("style");
  style.id = "verticalMenuStyle";
  style.textContent = `
    #ui { width: 200px; padding: 10px 12px 12px; box-sizing: border-box; }
    #ui h3 { margin: 0; }
    #ui .row {
      display: flex; flex-direction: column; gap: 3px; margin: 0;
      padding: 0; align-items: stretch;
    }
    #ui .row.grid2x2 {
      display: grid; grid-template-columns: 1fr 1fr; gap: 4px;
      flex-direction: initial;
    }
    #ui .row.grid3 {
      display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 4px;
      flex-direction: initial;
    }
    #ui .row.fieldRow {
      display: flex; flex-direction: row; gap: 6px;
      align-items: center; justify-content: space-between;
    }
    #ui .row.fieldRow label {
      display: flex; flex-direction: column; gap: 2px;
      flex: 1; min-width: 0;
    }
    #ui .row.fieldRow label > span.lbl {
      font-family: ${FONT_MONO};
      font-size: 8.5px; font-weight: 700; letter-spacing: 0.18em;
      text-transform: uppercase; color: #5a6774;
    }
    #ui .row.fieldRow input[type=number] {
      width: 100%; box-sizing: border-box;
    }
    /* fieldColumn — vertical stack of label+input rows, label flush
       left at a fixed inset, input filling the rest of the panel.
       Used by the GUIDES strip at the top of the panel and by the
       W / H / Rot trio inside the BOX inspector.  Three columns side
       by side squeeze each input into a third of the panel and the
       value becomes hard to read once it grows past four digits;
       stacked rows give the input the full panel width. */
    #ui .row.fieldColumn {
      display: flex; flex-direction: column; gap: 4px;
      align-items: stretch;
    }
    #ui .row.fieldColumn label {
      display: flex; flex-direction: row; gap: 8px;
      align-items: center;
    }
    #ui .row.fieldColumn label > span.lbl {
      flex: 0 0 34px;
      font-family: ${FONT_MONO};
      font-size: 8.5px; font-weight: 700; letter-spacing: 0.18em;
      text-transform: uppercase; color: #5a6774;
    }
    #ui .row.fieldColumn label > input {
      flex: 1 1 auto; min-width: 0;
    }
    #ui button {
      width: 100%; box-sizing: border-box; min-width: 0;
      white-space: normal; overflow-wrap: break-word;
      font-family: ${FONT_MONO};
    }
    #ui .row.grid2x2 button,
    #ui .row.grid3 button {
      text-align: center; padding: 6px 4px; font-size: 10px;
      letter-spacing: 0.04em;
    }
    /* Draw-tool button.  Outlined, uppercase, mono — the same idiom
       the cable playground uses for its own Draw cable and Measure
       buttons.  The active state is a yellow outline (never a fill),
       the same yellow the canvas uses for the ghost preview. */
    #ui #drawBoxBtn {
      text-align: center;
      padding: 7px 6px;
      border-color: #34445a;
      color: #dce6f2;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      font-size: 10px;
    }
    #ui #drawBoxBtn:hover {
      border-color: #4a5a70;
      background: rgba(220, 230, 242, 0.04);
    }
    #ui #drawBoxBtn.active {
      border-color: #ffe600;
      color: #ffe600;
      background: rgba(255, 230, 0, 0.04);
    }
    #ui #drawBoxBtn.active:hover {
      background: rgba(255, 230, 0, 0.08);
    }
    /* Header icon buttons — help and collapse.  Both share the same
       compact icon-button geometry; the help button carries the
       right-push margin so the two read as a right-aligned pair. */
    #ui #helpBtn,
    #ui #collapseBtn {
      width: auto !important; min-width: 22px !important;
      height: 20px !important; padding: 0 6px !important;
      flex: 0 0 auto !important;
      display: inline-flex !important; align-items: center !important;
      justify-content: center !important;
      font-size: 11px !important; font-weight: 700 !important;
      line-height: 1 !important; text-align: center !important;
      box-sizing: border-box !important;
    }
    #ui .titleBlock {
      display: flex; flex-direction: column; gap: 8px;
    }
    #ui .titleRow { display: flex; flex-direction: column; gap: 1px; }
    #ui .titleLabel {
      font-family: ${FONT_MONO};
      font-size: 8.5px; letter-spacing: 0.20em; font-weight: 700;
      line-height: 1.3; text-transform: uppercase;
    }
    #ui .titleValue {
      font-family: ${FONT_MONO};
      font-size: 10.5px; line-height: 1.4; overflow-wrap: anywhere;
    }
    #ui #status {
      font-family: ${FONT_MONO};
      font-size: 10px; line-height: 1.45;
      height: 48px; min-height: 48px; max-height: 48px;
      overflow-y: auto; overflow-x: hidden; overflow-wrap: anywhere;
      box-sizing: border-box;
    }
    /* Contextual GUIDES.  Every element inside the GUIDES section
       carries the .guidesItem class (added below, at load time).
       The whole group is hidden unless the panel root has the
       .draw-mode class, which bx_core.updateDrawBoxButton toggles
       whenever draw-box mode flips.  The inputs' VALUES are not
       touched by any of this: display:none leaves an <input>'s
       value alone, and nothing in the code resets it, so the
       defaults the user last set survive every mode toggle. */
    #ui:not(.draw-mode) .guidesItem { display: none; }
  `;
  document.head.appendChild(style);

  const ui = document.getElementById("ui");
  if (!ui) return;

  const sectionLabel = (text) => {
    const el = document.createElement("div");
    el.className = "sectionLabel";
    el.textContent = text;
    return el;
  };

  /* Rewrite header title */
  const h3 = ui.querySelector("h3");
  if (h3) {
    for (const node of h3.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim().length > 0) {
        node.textContent = T("appTitleShort");
        break;
      }
    }
  }

  /* GUIDES — W/H/Rot inputs + rotation buttons.
     The row is switched from .fieldRow (three side-by-side columns)
     to .fieldColumn (three stacked rows), the same layout the BOX
     inspector uses for its own W/H/Rot trio.  The inner markup is
     unchanged.

     Every element of the section — the "Guides" label, the field
     row, and the rotation row — gets a .guidesItem class so the
     contextual-hiding rule above can find them as one group. */
  const firstRow = ui.querySelector(".row");
  if (firstRow) {
    firstRow.classList.remove("row");
    firstRow.classList.add("row", "fieldColumn", "guidesItem");
    for (const lab of firstRow.querySelectorAll("label")) {
      const txt = lab.textContent.trim().split(/\s+/)[0];
      // Rebuild the label to wrap its text in a <span class="lbl">
      const input = lab.querySelector("input");
      lab.textContent = "";
      const span = document.createElement("span");
      span.className = "lbl";
      span.textContent = txt;
      lab.appendChild(span);
      if (input) lab.appendChild(input);
    }
    const guidesLabel = sectionLabel(T("secGuides"));
    guidesLabel.classList.add("guidesItem");
    firstRow.parentNode.insertBefore(guidesLabel, firstRow);
  }

  /* Rotation buttons into 2×2 grid, tagged as part of GUIDES */
  const rotRow = document.getElementById("rotL90").closest(".row");
  if (rotRow) {
    rotRow.classList.add("grid2x2");
    rotRow.classList.add("guidesItem");
  }

  /* DRAW — the draw-box tool toggle gets its own ruled section */
  const drawRow = document.getElementById("drawBoxBtn").closest(".row");
  if (drawRow) {
    drawRow.parentNode.insertBefore(sectionLabel(T("secDraw")), drawRow);
  }

  /* EDIT — Save / Undo / Clear all in 3 columns */
  const saveRow = document.getElementById("saveBtn").closest(".row");
  if (saveRow) {
    saveRow.classList.add("grid3");
    saveRow.parentNode.insertBefore(sectionLabel(T("secEdit")), saveRow);
  }

  /* OUTPUT — PNG / List in 2 columns */
  const pngRow = document.getElementById("pngBtn").closest(".row");
  if (pngRow) {
    pngRow.classList.add("grid2x2");
    pngRow.parentNode.insertBefore(sectionLabel(T("secOutput")), pngRow);
  }

  /* Shorten button labels (drop emoji) — every text is T()-sourced */
  const relabel = (id, text, title) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (title) el.title = title;
  };
  relabel("rotL90",  T("btnRotL90"), T("tipRotL90"));
  relabel("rotR90",  T("btnRotR90"), T("tipRotR90"));
  relabel("rotL15",  T("btnRotL15"), T("tipRotL15"));
  relabel("rotR15",  T("btnRotR15"), T("tipRotR15"));
  relabel("drawBoxBtn", T("btnDrawBox"), T("tipDrawBox"));
  relabel("saveBtn", T("btnSave"),      T("tipSave"));
  relabel("undoBtn", T("btnUndo"),      T("tipUndo"));
  relabel("clearBtn",T("btnClearAll"),  T("tipClearAll"));
  relabel("pngBtn",  T("btnPrintPng"),  T("tipPrintPng"));
  relabel("listBtn", T("btnListBoxes"), T("tipListBoxes"));

  /* Restore (show-panel) button.  This one lives outside #ui, so it
     is not touched by the vertical-menu pass above; relabel it here
     so its text matches the current language. */
  const restoreBtn0 = document.getElementById("restoreBtn");
  if (restoreBtn0) {
    restoreBtn0.textContent = T("btnShowPanel");
    restoreBtn0.title       = T("tipShowPanel");
  }

  /* ---- BOX section (conditional) ---- */
  const boxSection = document.createElement("div");
  boxSection.id = "boxSection";
  boxSection.className = "boxSection";
  boxSection.style.display = "none";
  boxSection.innerHTML =
    '<div class="sectionLabel">' + T("secBox") + '</div>' +
    '<div class="boxInspector">' +
      '<div id="boxIndex" class="boxIndex">—</div>' +
      '<div class="row fieldRow">' +
        '<label style="flex:1;">' +
          '<span class="lbl">' + T("lblName") + '</span>' +
          '<input type="text" id="boxName" autocomplete="off" spellcheck="false">' +
        '</label>' +
      '</div>' +
      '<div class="row fieldColumn">' +
        '<label><span class="lbl">' + T("lblW") + '</span>' +
          '<input type="number" id="selW" step="10" min="10"></label>' +
        '<label><span class="lbl">' + T("lblH") + '</span>' +
          '<input type="number" id="selH" step="10" min="10"></label>' +
        '<label><span class="lbl">' + T("lblRot") + '</span>' +
          '<input type="number" id="selRot" step="15"></label>' +
      '</div>' +
      '<div id="selInfo" class="boxSelInfo">' + T("boxSelNone") + '</div>' +
      '<button id="boxDeleteBtn">' + T("btnDeleteBox") + '</button>' +
    '</div>';

  // Insert BOX section right before the EDIT section label
  const editLabel = Array.from(ui.querySelectorAll(".sectionLabel"))
    .find(el => el.textContent === T("secEdit"));
  if (editLabel && editLabel.parentNode) {
    editLabel.parentNode.insertBefore(boxSection, editLabel);
  } else {
    ui.appendChild(boxSection);
  }

  /* Wire the box inspector fields */
  const nameInput   = document.getElementById("boxName");
  const selWInput   = document.getElementById("selW");
  const selHInput   = document.getElementById("selH");
  const selRotInput = document.getElementById("selRot");
  const boxDelBtn   = document.getElementById("boxDeleteBtn");

  nameInput.addEventListener("input", () => {
    if (selectedBox) {
      selectedBox.name = nameInput.value;
      draw();
    }
  });

  selWInput.addEventListener("change", () => {
    if (!selectedBox) return;
    const v = parseFloat(selWInput.value);
    if (!(v > 0)) { syncSelectionInputs(); return; }
    tryApplyBoxChange(selectedBox, { w: Math.max(MIN_BOX_MM, v) });
    syncSelectionInputs();
    draw();
  });
  selHInput.addEventListener("change", () => {
    if (!selectedBox) return;
    const v = parseFloat(selHInput.value);
    if (!(v > 0)) { syncSelectionInputs(); return; }
    tryApplyBoxChange(selectedBox, { h: Math.max(MIN_BOX_MM, v) });
    syncSelectionInputs();
    draw();
  });
  selRotInput.addEventListener("change", () => {
    if (!selectedBox) return;
    const v = parseFloat(selRotInput.value);
    if (!isFinite(v)) { syncSelectionInputs(); return; }
    const newRot = ((v % 360) + 360) % 360;
    tryApplyBoxChange(selectedBox, { rot: newRot });
    syncSelectionInputs();
    draw();
  });

  boxDelBtn.addEventListener("click", () => {
    if (!selectedBox) return;
    const idx = placedBoxes.indexOf(selectedBox);
    if (idx >= 0) placedBoxes.splice(idx, 1);
    setSelected(null);
  });

  /* ---- Title block ---- */
  const block = document.createElement("div");
  block.className = "titleBlock";
  block.innerHTML =
    '<div class="titleRow">' +
      '<div class="titleLabel">' + T("tbProject") + '</div>' +
      '<div class="titleValue" id="tb_project">' + T("tbProjectVal") +
      '</div>' +
    '</div>' +
    '<div class="titleRow">' +
      '<div class="titleLabel">' + T("tbSheet") + '</div>' +
      '<div class="titleValue" id="tb_sheet">—</div>' +
    '</div>' +
    '<div class="titleRow">' +
      '<div class="titleLabel">' + T("tbBoxes") + '</div>' +
      '<div class="titleValue" id="tb_boxes">—</div>' +
    '</div>' +
    '<div class="titleRow">' +
      '<div class="titleLabel">' + T("tbScale") + '</div>' +
      '<div class="titleValue" id="tb_scale">—</div>' +
    '</div>' +
    '<div class="titleRow">' +
      '<div class="titleLabel">' + T("tbSelection") + '</div>' +
      '<div class="titleValue" id="tb_selection">—</div>' +
    '</div>';
  ui.appendChild(block);

  /* Move status to the very bottom, above a STATUS label */
  const statusNode = document.getElementById("status");
  if (statusNode) {
    ui.appendChild(statusNode);
    statusNode.parentNode.insertBefore(sectionLabel(T("secStatus")),
                                       statusNode);
  }

  /* Hook up the collapse button */
  const collapseBtn = document.getElementById("collapseBtn");
  const restoreBtn  = document.getElementById("restoreBtn");
  if (collapseBtn) collapseBtn.onclick = () => {
    ui.classList.add("collapsed");
    restoreBtn.classList.add("visible");
  };
  if (restoreBtn) restoreBtn.onclick = () => {
    ui.classList.remove("collapsed");
    restoreBtn.classList.remove("visible");
  };
})();

/* ==========================================================================
   DARK SCREEN STYLE
   ==========================================================================
   No strings here — all translations live in bx_i18n.py. */

(function installDarkScreenStyles() {
  const style = document.createElement("style");
  style.id = "darkScreenStyle";
  style.textContent = `
    html, body {
      background: #0a0e14 !important;
      color: #dce6f2 !important;
    }
    canvas { background: #0a0e14 !important; }

    #ui {
      background: #0b1018;
      border: 1px solid #1e2836;
      border-radius: 3px;
      box-shadow: 0 6px 28px rgba(0, 0, 0, 0.65);
      font-family: ${FONT_MONO};
      font-size: 10.5px;
      line-height: 1.35;
      color: #a8b5c4;
      user-select: none;
    }

    #ui h3 {
      display: flex; align-items: center; gap: 4px;
      margin: 0 0 10px; padding: 0 0 8px;
      border-bottom: 1px solid #1e2836;
      font-family: ${FONT_MONO};
      font-size: 11px; font-weight: 700;
      letter-spacing: 0.16em; text-transform: uppercase;
      color: #dce6f2;
    }

    #ui .sectionLabel {
      display: flex; align-items: center; gap: 8px;
      margin: 12px 0 6px;
      font-family: ${FONT_MONO};
      font-size: 8.5px; font-weight: 700;
      letter-spacing: 0.22em; color: #5a6774;
      text-transform: uppercase;
      user-select: none; pointer-events: none;
    }
    #ui .sectionLabel::after {
      content: ""; flex: 1; height: 1px; background: #1e2836;
    }

    #ui button {
      width: 100%;
      background: transparent;
      border: 1px solid #223040;
      color: #b8c4d2;
      font-family: ${FONT_MONO};
      font-size: 10.5px; font-weight: 500;
      letter-spacing: 0.02em; line-height: 1.35;
      text-align: left; padding: 5px 9px;
      border-radius: 2px; box-sizing: border-box;
      min-width: 0; white-space: normal; overflow-wrap: break-word;
      cursor: pointer;
    }
    #ui button:hover {
      background: rgba(220, 230, 242, 0.035);
      border-color: #34445a; color: #dce6f2;
    }
    #ui button:active { background: rgba(220, 230, 242, 0.07); }

    #ui button.primary {
      background: transparent;
      border-color: #00e5ff;
      color: #00e5ff;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      text-align: center;
    }
    #ui button.primary:hover {
      background: rgba(0, 229, 255, 0.05);
      border-color: #4af0ff; color: #4af0ff;
    }

    /* Draw-tool button — dark-theme override, matching the idiom
       the cable playground uses for its own draw button. */
    #ui #drawBoxBtn {
      text-align: center;
      padding: 7px 6px;
      border-color: #34445a;
      color: #dce6f2;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      font-size: 10px;
      background: transparent;
    }
    #ui #drawBoxBtn:hover {
      border-color: #4a5a70;
      background: rgba(220, 230, 242, 0.04);
      color: #dce6f2;
    }
    #ui #drawBoxBtn.active {
      border-color: #ffe600;
      color: #ffe600;
      background: rgba(255, 230, 0, 0.04);
    }
    #ui #drawBoxBtn.active:hover {
      background: rgba(255, 230, 0, 0.08);
      border-color: #ffe600;
      color: #ffe600;
    }

    #ui #boxDeleteBtn {
      width: 100%; padding: 5px 8px; text-align: center;
      font-family: ${FONT_MONO};
      font-size: 10px; font-weight: 700;
      letter-spacing: 0.10em; text-transform: uppercase;
      background: transparent;
      border: 1px solid #4a2830; border-radius: 2px;
      color: #d48590; cursor: pointer;
    }
    #ui #boxDeleteBtn:hover {
      background: rgba(212, 133, 144, 0.10);
      border-color: #7a3e4a; color: #e8a0a8;
    }

    /* Header icon buttons.  The help button carries the right-push
       margin so both it and the collapse button cluster on the right
       of the header, side by side. */
    #ui #helpBtn,
    #ui #collapseBtn {
      width: auto !important; min-width: 22px !important;
      height: 20px !important; padding: 0 6px !important;
      flex: 0 0 auto !important;
      display: inline-flex !important; align-items: center !important;
      justify-content: center !important;
      font-family: ${FONT_MONO} !important;
      font-size: 11px !important; font-weight: 700 !important;
      line-height: 1 !important; text-align: center !important;
      background: transparent !important;
      border: 1px solid #223040 !important;
      border-radius: 2px !important;
      color: #a8b5c4 !important;
      box-sizing: border-box !important;
      cursor: pointer !important;
      transition: none !important;
    }
    #ui #helpBtn { margin-left: auto !important; }
    #ui #collapseBtn { margin-left: 4px !important; }
    #ui #helpBtn:hover,
    #ui #collapseBtn:hover {
      border-color: #34445a !important;
      color: #dce6f2 !important;
      background: rgba(220, 230, 242, 0.03) !important;
    }

    #ui hr { display: none !important; }

    #ui .titleBlock {
      margin: 12px 0 0; padding: 10px 0 0;
      border-top: 1px solid #1e2836;
    }
    #ui .titleLabel { color: #5a6774; }
    #ui .titleValue { color: #dce6f2; }

    #ui #status {
      margin: 0; padding: 0;
      color: #7b8794; border: 0;
    }
    #ui #status.ok   { color: #00e5ff; }
    #ui #status.bad  { color: #d48590; }
    #ui #status.warn { color: #ffe600; }

    #restoreBtn {
      padding: 6px 12px; background: #0b1018;
      border: 1px solid #1e2836; border-radius: 3px;
      box-shadow: 0 6px 28px rgba(0, 0, 0, 0.65);
      color: #a8b5c4;
      font-family: ${FONT_MONO};
      font-size: 10px; font-weight: 700;
      letter-spacing: 0.10em; text-transform: uppercase;
      cursor: pointer;
    }
    #restoreBtn:hover {
      background: #141a24; border-color: #34445a; color: #dce6f2;
    }

    #ui input[type=number], #ui input[type=text] {
      background: transparent;
      border: 1px solid #223040;
      border-radius: 2px;
      padding: 3px 6px;
      font-family: ${FONT_MONO};
      font-size: 11px; font-weight: 700;
      color: #dce6f2;
      outline: 0;
      width: 100%; box-sizing: border-box;
    }
    #ui input[type=number]:focus, #ui input[type=text]:focus {
      border-color: #00e5ff;
      background: rgba(0, 229, 255, 0.06);
    }
    #ui input:disabled {
      background: transparent; color: #5a6774;
      border-color: #1e2836;
    }

    #ui .boxSection { display: none; }
    #ui .boxInspector {
      display: flex; flex-direction: column; gap: 6px;
      margin: 2px 0 0; padding: 8px;
      border: 1px solid #1e2836;
      border-left: 2px solid #00e5ff;
      border-radius: 2px;
      background: rgba(0, 229, 255, 0.02);
    }
    #ui .boxIndex {
      font-family: ${FONT_MONO};
      font-size: 8.5px; font-weight: 700;
      letter-spacing: 0.16em; text-transform: uppercase;
      color: #00e5ff;
      line-height: 1.5; overflow-wrap: anywhere;
    }
    #ui .boxSelInfo {
      font-family: ${FONT_MONO};
      font-size: 10px; font-weight: 600;
      letter-spacing: 0.04em; color: #a8b5c4;
      overflow-wrap: anywhere;
    }

    /* ---- instructions popover — same printed-form idiom ---- */
    #helpPopover {
      position: fixed;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      z-index: 100;
      background: #0e1420;
      padding: 20px 24px;
      border: 1px solid #223040;
      border-radius: 3px;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.70);
      font-family: ${FONT_MONO};
      font-size: 11px;
      line-height: 1.6;
      color: #a8b5c4;
      width: calc(100vw - 48px);
      max-width: 680px;
      max-height: 80vh;
      overflow-y: auto;
      display: none;
      box-sizing: border-box;
    }
    #helpPopover.show { display: block; }
    #helpPopover > h3 {
      display: flex;
      align-items: center;
      gap: 12px;
      margin: 0 0 16px;
      padding: 0 0 10px;
      border-bottom: 1px solid #1e2836;
      font-family: ${FONT_MONO};
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: #dce6f2;
    }
    #helpPopover .closeBtn {
      margin-left: auto;
      padding: 4px 12px;
      background: transparent;
      border: 1px solid #223040;
      border-radius: 2px;
      color: #a8b5c4;
      font-family: inherit;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      cursor: pointer;
    }
    #helpPopover .closeBtn:hover {
      border-color: #34445a;
      color: #dce6f2;
      background: rgba(220, 230, 242, 0.03);
    }
    #helpPopover .body {
      font-family: ${FONT_SANS};
      font-size: 12.5px;
      line-height: 1.65;
      color: #a8b5c4;
    }
    #helpPopover .body b {
      color: #dce6f2;
      font-weight: 700;
    }
  `;
  document.head.appendChild(style);
})();

/* ==========================================================================
   CONDITIONAL BOX INSPECTOR — sync
   ========================================================================== */

function syncSelectionInputs() {
  const nameInput   = document.getElementById("boxName");
  const selWInput   = document.getElementById("selW");
  const selHInput   = document.getElementById("selH");
  const selRotInput = document.getElementById("selRot");
  const selInfo     = document.getElementById("selInfo");
  if (!nameInput) return;

  if (selectedBox) {
    const b = selectedBox;
    nameInput.disabled = false;
    nameInput.value    = b.name || "";
    selWInput.disabled = false;
    selHInput.disabled = false;
    selRotInput.disabled = false;
    selWInput.value    = Math.round(b.w);
    selHInput.value    = Math.round(b.h);
    selRotInput.value  = Math.round(b.rot * 100) / 100;
    selInfo.textContent = T("boxInfo")(b.x.toFixed(0), b.y.toFixed(0));
  } else {
    nameInput.disabled = true;
    nameInput.value    = "";
    selWInput.disabled = true;
    selHInput.disabled = true;
    selRotInput.disabled = true;
    selWInput.value    = "";
    selHInput.value    = "";
    selRotInput.value  = "";
    selInfo.textContent = T("boxSelNone");
  }
}

function _boxSyncInspector() {
  const section = document.getElementById("boxSection");
  const idxEl   = document.getElementById("boxIndex");
  if (!section || !idxEl) return;

  if (!selectedBox) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";

  const b = selectedBox;
  const inv = boxIsInvalid(b);
  const idx = placedBoxes.indexOf(b) + 1;
  const status = inv ? T("boxInvalid") : T("boxValid");
  idxEl.textContent = T("boxIdx")(idx, placedBoxes.length, status);
}

/* ==========================================================================
   INSTRUCTIONS POPOVER
   ==========================================================================
   The verbose usage notes that used to sit under the panel as a
   visible block now live in a floating modal, sourced from
   T("popInstructionsHTML") in bx_i18n.py.

   This IIFE installs:

     • a `?` button in the panel header (next to the collapse button),
     • a fixed-position `#helpPopover` div appended to document.body
       with the translated content inside,
     • a close button,
     • a `?` keyboard shortcut to toggle it,
     • an Escape listener that closes the popover when it is open.

   The Escape listener is installed in the CAPTURE phase on purpose.
   The aggregator orders bx_dispatch.py's JS before this module, so
   bx_dispatch's bubble-phase Escape listener (which disarms draw-box
   mode) is registered first and would fire before a bubble-phase
   Escape listener on the same window.  Capture phase fires before
   bubble phase on the same element, so this listener runs first and
   calls stopPropagation to shield bx_dispatch from the event.  The
   `?` key needs no such guard — nothing else claims it — so it is a
   plain bubble-phase listener. */

(function installInstructionsPopover() {
  const ui = document.getElementById("ui");
  if (!ui) return;

  const h3 = ui.querySelector("h3");
  const collapseBtn = document.getElementById("collapseBtn");
  const helpBtn = document.createElement("button");
  helpBtn.id = "helpBtn";
  helpBtn.title = T("tipHelp");
  helpBtn.textContent = T("btnHelp");
  if (collapseBtn && collapseBtn.parentNode === h3) {
    h3.insertBefore(helpBtn, collapseBtn);
  } else if (h3) {
    h3.appendChild(helpBtn);
  }

  const pop = document.createElement("div");
  pop.id = "helpPopover";
  pop.innerHTML =
    '<h3>' + T("popTitle") +
      '<button class="closeBtn" id="helpClose">' + T("btnClose") +
      '</button>' +
    '</h3>' +
    '<div class="body">' + T("popInstructionsHTML") + '</div>';
  document.body.appendChild(pop);

  const closeBtn = pop.querySelector("#helpClose");

  function openPop()  { pop.classList.add("show"); }
  function closePop() { pop.classList.remove("show"); }

  helpBtn.addEventListener("click", () => {
    if (pop.classList.contains("show")) closePop();
    else                                 openPop();
  });
  closeBtn.addEventListener("click", closePop);

  /* Escape — CAPTURE phase.  Fires before bx_dispatch's bubble-phase
     Escape listener, so a single Escape keypress that closes the
     popover does NOT also disarm draw-box mode or clear the
     selection. */
  window.addEventListener("keydown", (e) => {
    if (!pop.classList.contains("show")) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closePop();
    }
  }, { capture: true });

  /* ? — bubble phase.  No conflict with anything else. */
  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    if (e.key === "?" || (e.shiftKey && e.key === "/")) {
      if (pop.classList.contains("show")) return;
      e.preventDefault();
      openPop();
    }
  });
})();

/* ==========================================================================
   PANEL FADE
   ========================================================================== */

(function installPanelFade() {
  const ui = document.getElementById("ui");
  if (!ui) return;
  ui.addEventListener("mouseenter", () => {
    panelHovered = true;
    updatePanelOpacity();
  });
  ui.addEventListener("mouseleave", () => {
    panelHovered = false;
    panelLastActivity = performance.now();
    updatePanelOpacity();
  });
  ui.addEventListener("mousedown", () => {
    panelLastActivity = performance.now();
  });
})();

function updatePanelOpacity() {
  const ui = document.getElementById("ui");
  if (!ui) return;
  if (ui.classList.contains("collapsed")) return;
  if (panelHovered || drag || handleDrag || panning || drawBoxMode) {
    ui.style.opacity = "";
    return;
  }
  if (!mouse.inside) { ui.style.opacity = ""; return; }
  const idle = performance.now() - panelLastActivity;
  ui.style.opacity = idle > 1800 ? "0.42" : "";
}

/* ==========================================================================
   TITLE BLOCK
   ========================================================================== */

const _TB_REFS = {
  project: null, sheet: null, boxes: null, scale: null, selection: null,
};

function updateTitleBlock() {
  if (!_TB_REFS.project) {
    _TB_REFS.project   = document.getElementById("tb_project");
    _TB_REFS.sheet     = document.getElementById("tb_sheet");
    _TB_REFS.boxes     = document.getElementById("tb_boxes");
    _TB_REFS.scale     = document.getElementById("tb_scale");
    _TB_REFS.selection = document.getElementById("tb_selection");
    if (!_TB_REFS.project) return;
  }

  _TB_REFS.sheet.textContent = T("tbSheetVal")(GEOMETRY.cutZ);

  const validN = placedBoxes.filter(b => !boxIsInvalid(b)).length;
  const totalN = placedBoxes.length;
  _TB_REFS.boxes.textContent = T("tbBoxesVal")(validN, totalN);

  const scaleN = Math.max(1, Math.round(1 / Math.max(view.scale, 1e-6)));
  _TB_REFS.scale.textContent = T("tbScaleVal")(scaleN);

  let selText = T("tbSelNone");
  if (selectedBox) {
    const b = selectedBox;
    const inv = boxIsInvalid(b);
    const status = inv ? T("boxInvalid") : T("boxValid");
    const name = b.name || T("boxUnnamed");
    selText = T("tbSelBox")(name, status);
  }
  _TB_REFS.selection.textContent = selText;

  /* Keep the conditional BOX section in sync on the same frame */
  _boxSyncInspector();
}

/* ==========================================================================
   KEYBOARD — H toggles panel
   ========================================================================== */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  if (e.key === "h" || e.key === "H") {
    const ui = document.getElementById("ui");
    const restore = document.getElementById("restoreBtn");
    if (ui.classList.contains("collapsed")) {
      ui.classList.remove("collapsed");
      restore.classList.remove("visible");
    } else {
      ui.classList.add("collapsed");
      restore.classList.add("visible");
    }
  }
});
"""


BOOT_JS = r"""
/* ==========================================================================
   BOOTSTRAP
   ========================================================================== */

document.title = T("pageTitle");

document.getElementById("rotL90").onclick = () => { rotateSelection(-90); draw(); };
document.getElementById("rotR90").onclick = () => { rotateSelection( 90); draw(); };
document.getElementById("rotL15").onclick = () => { rotateSelection(-15); draw(); };
document.getElementById("rotR15").onclick = () => { rotateSelection( 15); draw(); };

document.getElementById("drawBoxBtn").onclick = () => {
  toggleDrawBoxMode();
};

document.getElementById("saveBtn").onclick = () => doSave(true);
document.getElementById("listBtn").onclick = () => printBoxList();
document.getElementById("pngBtn").onclick  = () => renderFloorPlanPNG();

document.getElementById("undoBtn").onclick = () => {
  if (!placedBoxes.length) return;
  const last = placedBoxes[placedBoxes.length - 1];
  placedBoxes.pop();
  if (last === selectedBox) setSelected(null);
  else { syncSelectionInputs(); draw(); }
};

document.getElementById("clearBtn").onclick = () => {
  if (placedBoxes.length && !confirm(T("msgConfirmClear"))) return;
  placedBoxes = [];
  setSelected(null);
};

for (const id of ["boxW", "boxH", "boxRot"]) {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", draw);
}

resize();
setRotation(0);
setSelected(null);
updateDrawBoxButton();
autoload();
"""


HTML_TAIL = r"""</script>
</body>
</html>
"""
