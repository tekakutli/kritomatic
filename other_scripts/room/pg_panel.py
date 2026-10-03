"""
pg_panel.py — the UI panel, dark stylesheet, title block, status line,
cursor, popover, panel fade.

Everything that is HTML/CSS rather than canvas.  Two sources of
change reach the same panel:

    • The control panel's button markup lives in pg_core's HTML_HEAD.
      installVerticalMenu reshapes it at runtime — repositioning,
      relabelling, injecting the MEASURE inspector and the CABLE
      inspector between DRAW and EDIT, appending the title block, and
      moving the status line to the bottom.

    • installDarkScreenStyles appends a second stylesheet that
      reskins every element HTML_HEAD and installVerticalMenu paint
      by hand, plus the ruler's floating canvas input, the MEASURE
      inspector's fields, and the CABLE inspector's buttons.

Two IIFEs (installPanelFade, installInstructionsPopover) and six
plain functions (updateTitleBlock, updatePanelOpacity, updateStatus,
updateCursor, _rulerSyncMeasureInspector's peer _cableSyncInspector,
and _cableSyncInspector) round out the module.  updateStatus and
updateCursor are called from draw() on every frame; the two IIFEs
run once at load.

Translation
-----------
Every string this module writes into the DOM — the relabelled
buttons, the section labels, the title block's field names, the
inspector chrome, the popover, the status line's live readout, the
title block's values — comes from T() in pg_i18n.py.  The only
literals left in this file are CSS class names, HTML tag names, and
the em-dash used as a null placeholder where a translation would be
overkill (T("tbSelNone") is available if you want to translate it
too).

Two conditional inspectors
--------------------------
Both follow the same pattern: a hidden section that becomes visible
when a selection of the matching kind is active.

    MEASURE inspector  (pg_ruler.py owns it)
        visible while ruler mode is armed AND a completed
        measurement is selected
        carries: index badge, span field (cm), angle field (°),
                 "Delete measurement"

    CABLE inspector    (this module owns it)
        visible while a cable is selected
        carries: info badge naming the true-cable group,
                 a vertex line naming the selected vertex (or
                 "no vertex selected"),
                 "Delete vertex" — only shown when a vertex is
                 actually selected,
                 "Delete cable"

The MEASURE inspector was previously the only conditional section;
the CABLE inspector mirrors it.  The two destructive actions that
used to be permanently visible in the EDIT row (Delete vertex /
Delete cable) are now hidden by CSS and re-exposed through the
CABLE section — same DOM elements, same BOOT_JS onclick handlers,
just relocated into a contextual surface.

Each inspector has a sync function called once per frame from
draw().  The sync functions read the current state and set
style.display on their section element, which is why they can never
go stale — the same draw() pass that repaints the canvas also
repaints the panel.

Snap-row controls
-----------------
The row directly under the header carries two controls side by side:

    the snap pill            reports the snap state, filled in
                             pg_core.updateSnapPill
    the zoom-reset button    calls resetZoomPan() — the same function
                             the Home key binds to.  Clears uniform
                             zoom, the horizontal wall stretch
                             (viewWall.zoomX), and both pans, so
                             every view-state field the user can move
                             returns to its load-time default in one
                             click.

Putting them in one row with justify-content: space-between keeps
them together as the panel's view-state surface and reads as two
distinct controls rather than one merged blob.  The button carries
width: auto to override the vertical-menu's global
"#ui button { width: 100% }" — without that override the button
would stretch to fill the flex row and the pill would be squeezed
against the left edge.
"""


PANEL_JS = r"""
/* ==========================================================================
   VERTICAL MENU PANEL
   ==========================================================================
   The control panel's button markup lives in pg_core.py's HTML_HEAD;
   pg_core is not modified for this.  Instead, this block injects a
   <style> element into the document head that reshapes the panel into
   a narrow vertical printed form, and a small DOM pass that relabels
   every control through T().

   Section labels are inserted by the same DOM pass.  They are pure
   decoration — pointer-events:none, user-select:none — and never take
   a click.  Each one renders as "LABEL ──────────", the rule extending
   to the panel's right edge, matching the section breaks printed on
   pg_export.py's page.

   Every human-readable string in this block is wrapped in T().  The
   translation table is pg_i18n.py's TRANSLATIONS; switching LANG
   there switches every label, tooltip, status message, and popover
   line the playground shows. */

(function installVerticalMenu() {
  const style = document.createElement("style");
  style.id = "verticalMenuStyle";
  style.textContent = `
    /* 200 px, not 160: the snap row carries both the snap pill
       (~80 px) and the zoom-reset button with its new explicit
       label (~85 px).  At 160 px the button's text wraps onto two
       lines; 200 px gives the pair enough room to sit side by side
       on one baseline with the 6 px gap between them.  Every other
       control in the panel is narrow enough that the extra 40 px
       costs nothing visually. */
    #ui {
      width: 200px;
      padding: 10px 12px 12px;
      box-sizing: border-box;
    }
    #ui h3 {
      margin: 0;
    }
    #ui .row {
      display: flex;
      flex-direction: column;
      gap: 3px;
      margin: 0;
      padding: 0;
      align-items: stretch;
    }
    /* The row directly under the header carries the snap pill and the
       zoom-reset button.  space-between pushes them to opposite ends
       of the panel width, and align-items: center keeps them on the
       same baseline.  The gap guarantees a minimum separation so the
       two never touch when the panel is at its narrowest and the
       pill's text is at its longest. */
    #ui .row.snapRow {
      flex-direction: row;
      justify-content: space-between;
      align-items: center;
      gap: 6px;
      margin: 0 0 6px;
    }
    #ui .row.grid2x2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px;
      flex-direction: initial;
    }
    #ui button {
      width: 100%;
      box-sizing: border-box;
      min-width: 0;
      white-space: normal;
      overflow-wrap: break-word;
      font-family: ${FONT_MONO};
    }
    #ui .row.grid2x2 button {
      text-align: center;
      padding: 6px 4px;
      font-size: 10px;
      letter-spacing: 0.04em;
    }
    #ui #helpBtn,
    #ui #collapseBtn {
      width: auto !important;
      min-width: 22px !important;
      height: 20px !important;
      padding: 0 6px !important;
      flex: 0 0 auto !important;
      display: inline-flex !important;
      align-items: center !important;
      justify-content: center !important;
      font-size: 11px !important;
      font-weight: 700 !important;
      line-height: 1 !important;
      text-align: center !important;
      box-sizing: border-box !important;
    }

    /* Title block.  Five stacked label / value pairs, ruled off from
       the buttons above.  Each row is a small-caps mono label above an
       ink-mono value, exactly the idiom used for the printed title
       block in pg_export.py. */
    #ui .titleBlock {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    #ui .titleRow {
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    #ui .titleLabel {
      font-family: ${FONT_MONO};
      font-size: 8.5px;
      letter-spacing: 0.20em;
      font-weight: 700;
      line-height: 1.3;
      text-transform: uppercase;
    }
    #ui .titleValue {
      font-family: ${FONT_MONO};
      font-size: 10.5px;
      line-height: 1.4;
      overflow-wrap: anywhere;
    }

    /* Status line.  Sits at the very bottom of the panel and is
       bounded to a fixed height so a growing status cannot push the
       panel taller.  Overflow scrolls inside the box. */
    #ui #status {
      font-family: ${FONT_MONO};
      font-size: 10px;
      line-height: 1.45;
      height: 48px;
      min-height: 48px;
      max-height: 48px;
      overflow-y: auto;
      overflow-x: hidden;
      overflow-wrap: anywhere;
      box-sizing: border-box;
    }
  `;
  document.head.appendChild(style);

  const ui = document.getElementById("ui");
  if (!ui) return;

  /* Helper for the section labels. */
  const sectionLabel = (text) => {
    const el = document.createElement("div");
    el.className = "sectionLabel";
    el.textContent = text;
    return el;
  };

  /* 1. The four grid / wall buttons go into a 2x2 grid, under a
     GUIDES section label. */
  const firstRow = ui.querySelector(".row");
  if (firstRow) {
    firstRow.parentNode.insertBefore(sectionLabel(T("secGuides")), firstRow);
    firstRow.classList.add("grid2x2");
  }

  /* 2. Header title.  The snap pill and icon buttons are element
     children and are handled separately. */
  const h3 = ui.querySelector("h3");
  if (h3) {
    for (const node of h3.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim().length > 0) {
        node.textContent = T("appTitleShort");
        break;
      }
    }
  }

  /* 3. Snap pill onto its own row, directly below the header, with
        a compact zoom-reset button beside it.  The two together are
        the panel's view-state control surface: the pill reports
        snap mode, the button calls resetZoomPan() — the same
        function the Home key binds to — and clears uniform zoom,
        the horizontal wall stretch (viewWall.zoomX), and both pans. */
  const pill = document.getElementById("snapPill");
  if (pill && h3) {
    const row = document.createElement("div");
    row.className = "row snapRow";
    pill.parentNode.removeChild(pill);
    row.appendChild(pill);

    const zoomResetBtn = document.createElement("button");
    zoomResetBtn.id = "zoomResetBtn";
    zoomResetBtn.type = "button";
    zoomResetBtn.textContent = T("btnZoomReset");
    zoomResetBtn.title = T("tipZoomReset");
    zoomResetBtn.addEventListener("click", () => resetZoomPan());
    row.appendChild(zoomResetBtn);

    h3.parentNode.insertBefore(row, h3.nextSibling);
  }

  /* 4. Relabel every button through T().  Tooltips too. */
  const relabel = (id, text, title) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    if (title) el.title = title;
  };
  relabel("addNsGrid",    T("btnAddNsGrid"),    T("tipAddNsGrid"));
  relabel("addEwGrid",    T("btnAddEwGrid"),    T("tipAddEwGrid"));
  relabel("addWallV",     T("btnAddWallV"),     T("tipAddWallV"));
  relabel("addWallH",     T("btnAddWallH"),     T("tipAddWallH"));
  relabel("saveBtn",      T("btnSave"),         T("tipSave"));
  relabel("delVertexBtn", T("btnDeleteVertex"), T("tipDeleteVertex"));
  relabel("deleteBtn",    T("btnDeleteCable"),  T("tipDeleteCable"));
  relabel("clearBtn",     T("btnClearAll"),     T("tipClearAll"));

  /* 5. DRAW section, and the ruler button beside Draw cable. */
  const drawBtnEl = document.getElementById("drawBtn");
  let drawRowEl = null;
  if (drawBtnEl) {
    drawRowEl = drawBtnEl.closest(".row");
    if (drawRowEl) {
      drawRowEl.classList.add("grid2x2");
      drawRowEl.parentNode.insertBefore(sectionLabel(T("secDraw")), drawRowEl);
      const rulerBtn = document.createElement("button");
      rulerBtn.id = "rulerBtn";
      rulerBtn.textContent = T("btnMeasure");
      rulerBtn.title = T("tipMeasure");
      drawRowEl.appendChild(rulerBtn);
      rulerBtn.addEventListener("click", toggleRuler);
    }
    /* The Draw cable button's own label is set by updateDrawButton in
       pg_core's CORE_JS — it also routes through T() and re-reads it
       on every mode toggle. */
    drawBtnEl.title = T("tipDrawCable");
  }

  /* 5b. MEASURE section — the panel-side inspector for the selected
     ruler measurement. */
  const measureSection = document.createElement("div");
  measureSection.id = "measureSection";
  measureSection.className = "measureSection";
  measureSection.style.display = "none";
  measureSection.innerHTML =
    '<div class="sectionLabel">' + T("secMeasure") + '</div>' +
    '<div id="measureInspector" class="measureInspector">' +
      '<div id="measureIndex" class="measureIndex">—</div>' +
      '<div class="measureGrid">' +
        '<div class="measureField">' +
          '<span class="lbl">' + T("lblSpanCm") + '</span>' +
          '<input type="text" inputmode="decimal" id="measureSpan" ' +
            'autocomplete="off" spellcheck="false">' +
        '</div>' +
        '<div class="measureField">' +
          '<span class="lbl">' + T("lblAngleDeg") + '</span>' +
          '<input type="text" inputmode="decimal" id="measureAngle" ' +
            'autocomplete="off" spellcheck="false">' +
        '</div>' +
      '</div>' +
      '<button id="measureDeleteBtn">' + T("btnDeleteMeasurement") +
      '</button>' +
    '</div>';

  if (drawRowEl && drawRowEl.parentNode) {
    drawRowEl.parentNode.insertBefore(measureSection, drawRowEl.nextSibling);
  } else {
    ui.appendChild(measureSection);
  }

  const measureSpanEl    = measureSection.querySelector("#measureSpan");
  const measureAngleEl   = measureSection.querySelector("#measureAngle");
  const measureDeleteBtn = measureSection.querySelector("#measureDeleteBtn");

  measureSpanEl.addEventListener("input", () => {
    const v = parseFloat(measureSpanEl.value);
    if (!isFinite(v) || v <= 0) return;
    _rulerApplyPanelSpan(v * 10);   // cm → mm
    draw();
  });
  measureSpanEl.addEventListener("change", () => {
    const v = parseFloat(measureSpanEl.value);
    if (!isFinite(v) || v <= 0) { _rulerSyncMeasureInspector(); return; }
    _rulerApplyPanelSpan(v * 10);
    draw();
  });

  measureAngleEl.addEventListener("input", () => {
    const v = parseFloat(measureAngleEl.value);
    if (!isFinite(v)) return;
    _rulerApplyPanelAngle(v);
    draw();
  });
  measureAngleEl.addEventListener("change", () => {
    const v = parseFloat(measureAngleEl.value);
    if (!isFinite(v)) { _rulerSyncMeasureInspector(); return; }
    _rulerApplyPanelAngle(v);
    draw();
  });

  measureDeleteBtn.addEventListener("click", () => {
    const sel = state.ruler.selected;
    if (sel < 0 || sel >= state.ruler.measures.length) return;
    state.ruler.measures.splice(sel, 1);
    state.ruler.selected = -1;
    state.ruler.hoverIdx = -1;
    _rulerSyncMeasureInspector();
    draw();
  });

  /* 5c. CABLE section — the panel-side inspector for the selected
     cable. */
  const cableSection = document.createElement("div");
  cableSection.id = "cableSection";
  cableSection.className = "cableSection";
  cableSection.style.display = "none";
  cableSection.innerHTML =
    '<div class="sectionLabel">' + T("secCable") + '</div>' +
    '<div id="cableInspector" class="cableInspector">' +
      '<div id="cableIndex" class="cableIndex">—</div>' +
      '<div id="cableVertexInfo" class="cableVertexInfo">—</div>' +
      '<button id="cableDeleteVertexBtn">' + T("btnDeleteVertex") +
      '</button>' +
      '<button id="cableDeleteCableBtn">' + T("btnDeleteCable") +
      '</button>' +
    '</div>';

  if (measureSection.parentNode) {
    measureSection.parentNode.insertBefore(
      cableSection, measureSection.nextSibling);
  } else {
    ui.appendChild(cableSection);
  }

  const cableDeleteVertexBtn =
    cableSection.querySelector("#cableDeleteVertexBtn");
  const cableDeleteCableBtn  =
    cableSection.querySelector("#cableDeleteCableBtn");

  cableDeleteVertexBtn.addEventListener("click", () => {
    deleteSelectedVertex();
  });
  cableDeleteCableBtn.addEventListener("click", () => {
    deleteSelectedCable();
  });

  /* Add EDIT section label. */
  const saveBtnEl = document.getElementById("saveBtn");
  if (saveBtnEl) {
    const saveRow = saveBtnEl.closest(".row");
    if (saveRow) {
      saveRow.classList.add("grid2x2");
      saveRow.parentNode.insertBefore(sectionLabel(T("secEdit")), saveRow);
    }
  }

  /* 6. OUTPUT section label, before the hidden hr; the arrow-diag and
     export buttons are injected by their own modules between this
     label and the hr. */
  const hrEl = ui.querySelector("hr");
  if (hrEl) {
    hrEl.parentNode.insertBefore(sectionLabel(T("secOutput")), hrEl);
  }
  const relabelLate = () => {
    relabel("arrowDiagBtn", T("btnDiagnostics"), T("tipDiagnostics"));
    relabel("exportBtn",    T("btnExportRuns"),  T("tipExportRuns"));
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", relabelLate);
  } else {
    relabelLate();
  }

  /* 7. Title block. */
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
      '<div class="titleLabel">' + T("tbCeiling") + '</div>' +
      '<div class="titleValue" id="tb_ceiling">—</div>' +
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

  /* 8. Status line at the bottom, under a STATUS label. */
  const statusNode = document.getElementById("status");
  if (statusNode) {
    ui.appendChild(statusNode);
    statusNode.parentNode.insertBefore(sectionLabel(T("secStatus")),
                                       statusNode);
  }
})();

/* ==========================================================================
   DARK SCREEN STYLE — "printed form" idiom
   ==========================================================================
   The palette inversion in JavaScript handles the canvas.  Everything
   about the panel, popover, status colours and body background is
   pure CSS and needs a second stylesheet layered on top of the ones
   already shipped by HTML_HEAD and installVerticalMenu.

   No strings here — all translations live in pg_i18n.py. */

(function installDarkScreenStyles() {
  const style = document.createElement("style");
  style.id = "darkScreenStyle";
  style.textContent = `
    /* ---- app background ---- */
    html, body {
      background: #0a0e14 !important;
      color: #dce6f2 !important;
    }
    canvas { background: #0a0e14 !important; }

    /* ---- panel shell ---- */
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

    /* ---- header ---- */
    #ui h3 {
      display: flex;
      align-items: center;
      gap: 4px;
      margin: 0 0 10px;
      padding: 0 0 8px;
      border-bottom: 1px solid #1e2836;
      font-family: ${FONT_MONO};
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: #dce6f2;
    }

    /* ---- section labels: "GUIDES ────────────────" ---- */
    #ui .sectionLabel {
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 12px 0 6px;
      font-family: ${FONT_MONO};
      font-size: 8.5px;
      font-weight: 700;
      letter-spacing: 0.22em;
      color: #5a6774;
      text-transform: uppercase;
      user-select: none;
      pointer-events: none;
    }
    #ui .sectionLabel::after {
      content: "";
      flex: 1;
      height: 1px;
      background: #1e2836;
    }

    /* ---- snap pill: outlined status chip with ●/○ indicator ---- */
    #ui #snapPill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      margin: 0;
      padding: 3px 9px;
      background: transparent;
      border: 1px solid #223040;
      border-radius: 2px;
      font-family: ${FONT_MONO};
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.14em;
      color: #5a6774;
      text-transform: uppercase;
    }
    #ui #snapPill::after {
      content: "○";
      font-size: 9px;
      line-height: 1;
      color: #5a6774;
    }
    #ui #snapPill.on {
      border-color: #00e5ff;
      color: #00e5ff;
    }
    #ui #snapPill.on::after {
      content: "●";
      color: #00e5ff;
    }

    /* Zoom-reset button.  Pairs with the snap pill in the same
       status row: same mono small-caps idiom, same outline weight,
       same muted resting colour.  No trailing dot indicator, so it
       reads as an action rather than a state; the border brightens
       on hover instead of staying static.

       width: auto overrides the vertical-menu's global
       "#ui button { width: 100% }" — without that override the
       button would stretch to fill the flex row and the pill would
       be squeezed against the left edge, reading as one merged
       control rather than two distinct ones.

       Calls resetZoomPan(), the same function the Home key binds
       to.  Clears uniform zoom, the horizontal wall stretch
       (viewWall.zoomX), and both pans. */
    #ui #zoomResetBtn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: auto;
      flex: 0 0 auto;
      padding: 3px 9px;
      background: transparent;
      border: 1px solid #223040;
      border-radius: 2px;
      font-family: ${FONT_MONO};
      font-size: 11px;
      font-weight: 700;
      line-height: 1;
      color: #5a6774;
      cursor: pointer;
      white-space: nowrap;
      box-sizing: border-box;
    }
    #ui #zoomResetBtn:hover {
      border-color: #34445a;
      color: #dce6f2;
      background: rgba(220, 230, 242, 0.03);
    }
    #ui #zoomResetBtn:active {
      background: rgba(220, 230, 242, 0.07);
    }

    /* ---- buttons: outlined fields, never filled ---- */
    #ui button {
      width: 100%;
      background: transparent;
      border: 1px solid #223040;
      color: #b8c4d2;
      font-family: ${FONT_MONO};
      font-size: 10.5px;
      font-weight: 500;
      letter-spacing: 0.02em;
      line-height: 1.35;
      text-align: left;
      padding: 5px 9px;
      border-radius: 2px;
      box-sizing: border-box;
      min-width: 0;
      white-space: normal;
      overflow-wrap: break-word;
      cursor: pointer;
    }
    #ui button:hover {
      background: rgba(220, 230, 242, 0.035);
      border-color: #34445a;
      color: #dce6f2;
    }
    #ui button:active {
      background: rgba(220, 230, 242, 0.07);
    }

    /* draw + ruler buttons — the two tools, sharing a row */
    #ui #drawBtn,
    #ui #rulerBtn {
      text-align: center;
      padding: 7px 6px;
      border-color: #34445a;
      color: #dce6f2;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      font-size: 10px;
    }
    #ui #drawBtn:hover,
    #ui #rulerBtn:hover {
      border-color: #4a5a70;
      background: rgba(220, 230, 242, 0.04);
    }
    #ui #drawBtn.active {
      border-color: #ffe600;
      color: #ffe600;
      background: rgba(255, 230, 0, 0.04);
    }
    #ui #drawBtn.active:hover {
      background: rgba(255, 230, 0, 0.08);
    }
    #ui #rulerBtn.active {
      border-color: #00e5ff;
      color: #00e5ff;
      background: rgba(0, 229, 255, 0.05);
    }
    #ui #rulerBtn.active:hover {
      background: rgba(0, 229, 255, 0.10);
    }

    /* primary — SAVE, an outline in cyan, never a fill */
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
      border-color: #4af0ff;
      color: #4af0ff;
    }

    /* danger — a real rose, not a warm gray. */
    #ui button.danger {
      background: transparent !important;
      border-color: #4a2830;
      color: #d48590;
    }
    #ui button.danger:hover {
      background: rgba(212, 133, 144, 0.10) !important;
      border-color: #7a3e4a;
      color: #e8a0a8;
    }

    /* Hide the old always-visible delete buttons in the EDIT row. */
    #ui #delVertexBtn,
    #ui #deleteBtn { display: none !important; }

    /* icon buttons in the header */
    #ui #helpBtn,
    #ui #collapseBtn {
      width: auto !important;
      min-width: 22px !important;
      height: 20px !important;
      padding: 0 6px !important;
      flex: 0 0 auto !important;
      display: inline-flex !important;
      align-items: center !important;
      justify-content: center !important;
      font-family: ${FONT_MONO} !important;
      font-size: 11px !important;
      font-weight: 700 !important;
      line-height: 1 !important;
      text-align: center !important;
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

    /* hr is hidden — section labels provide all the rules */
    #ui hr { display: none !important; }

    /* title block */
    #ui .titleBlock {
      margin: 12px 0 0;
      padding: 10px 0 0;
      border-top: 1px solid #1e2836;
    }
    #ui .titleLabel { color: #5a6774; }
    #ui .titleValue { color: #dce6f2; }

    /* status line */
    #ui #status {
      margin: 0;
      padding: 0;
      color: #7b8794;
      border: 0;
    }
    #ui #status.ok   { color: #00e5ff; }
    #ui #status.bad  { color: #d48590; }
    #ui #status.warn { color: #ffe600; }

    /* collapse / restore */
    #restoreBtn {
      padding: 6px 12px;
      background: #0b1018;
      border: 1px solid #1e2836;
      border-radius: 3px;
      box-shadow: 0 6px 28px rgba(0, 0, 0, 0.65);
      color: #a8b5c4;
      font-family: ${FONT_MONO};
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      cursor: pointer;
    }
    #restoreBtn:hover {
      background: #141a24;
      border-color: #34445a;
      color: #dce6f2;
    }

    /* ---- instructions popover ---- */
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

    /* ---- ruler numeric input (canvas, transient) ---- */
    #rulerNumericInput {
      position: fixed;
      z-index: 200;
      display: none;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      background: #0e1420;
      border: 1px solid #00e5ff;
      border-radius: 2px;
      box-shadow:
        0 12px 32px rgba(0, 0, 0, 0.70),
        0 0 0 1px rgba(0, 229, 255, 0.15);
      font-family: ${FONT_MONO};
      color: #dce6f2;
      user-select: none;
    }
    #rulerNumericInput.show { display: inline-flex; }
    #rulerNumericInput .lbl {
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.20em;
      text-transform: uppercase;
      color: #5a6774;
    }
    #rulerNumericInput input {
      background: transparent;
      border: 0;
      outline: 0;
      padding: 0;
      margin: 0;
      width: 76px;
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      color: #00e5ff;
      text-align: right;
      letter-spacing: 0.02em;
    }
    #rulerNumericInput input::selection {
      background: rgba(0, 229, 255, 0.30);
    }
    #rulerNumericInput .unit {
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: #5a6774;
      min-width: 18px;
    }

    /* ---- measure inspector ---- */
    #ui .measureSection { display: none; }
    #ui .measureInspector {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin: 2px 0 0;
      padding: 8px 8px;
      border: 1px solid #1e2836;
      border-left: 2px solid #00e5ff;
      border-radius: 2px;
      background: rgba(0, 229, 255, 0.02);
    }
    #ui .measureIndex {
      font-family: ${FONT_MONO};
      font-size: 8.5px;
      font-weight: 700;
      letter-spacing: 0.20em;
      text-transform: uppercase;
      color: #00e5ff;
    }
    #ui .measureGrid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px;
    }
    #ui .measureField {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    #ui .measureField .lbl {
      font-family: ${FONT_MONO};
      font-size: 8.5px;
      font-weight: 700;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: #5a6774;
    }
    #ui .measureField input {
      width: 100%;
      box-sizing: border-box;
      background: transparent;
      border: 1px solid #223040;
      border-radius: 2px;
      padding: 3px 6px;
      font-family: ${FONT_MONO};
      font-size: 11px;
      font-weight: 700;
      color: #00e5ff;
      text-align: right;
      outline: 0;
    }
    #ui .measureField input:focus {
      border-color: #00e5ff;
      background: rgba(0, 229, 255, 0.06);
    }
    #ui #measureDeleteBtn {
      width: 100%;
      padding: 5px 8px;
      text-align: center;
      font-family: ${FONT_MONO};
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      background: transparent;
      border: 1px solid #4a2830;
      border-radius: 2px;
      color: #d48590;
      cursor: pointer;
    }
    #ui #measureDeleteBtn:hover {
      background: rgba(212, 133, 144, 0.10);
      border-color: #7a3e4a;
      color: #e8a0a8;
    }

    /* ---- cable inspector ---- */
    #ui .cableSection { display: none; }
    #ui .cableInspector {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin: 2px 0 0;
      padding: 8px 8px;
      border: 1px solid #1e2836;
      border-left: 2px solid #d48590;
      border-radius: 2px;
      background: rgba(212, 133, 144, 0.02);
    }
    #ui .cableIndex {
      font-family: ${FONT_MONO};
      font-size: 8.5px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: #dce6f2;
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    #ui .cableVertexInfo {
      font-family: ${FONT_MONO};
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.04em;
      color: #a8b5c4;
      overflow-wrap: anywhere;
    }
    #ui #cableDeleteVertexBtn,
    #ui #cableDeleteCableBtn {
      width: 100%;
      padding: 5px 8px;
      text-align: center;
      font-family: ${FONT_MONO};
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      background: transparent;
      border: 1px solid #4a2830;
      border-radius: 2px;
      color: #d48590;
      cursor: pointer;
    }
    #ui #cableDeleteVertexBtn:hover,
    #ui #cableDeleteCableBtn:hover {
      background: rgba(212, 133, 144, 0.10);
      border-color: #7a3e4a;
      color: #e8a0a8;
    }
  `;
  document.head.appendChild(style);
})();

/* ==========================================================================
   TITLE BLOCK REFRESHER
   ========================================================================== */

const _TB_REFS = {
  project: null, sheet: null, ceiling: null, scale: null, selection: null,
};
function updateTitleBlock() {
  if (!_TB_REFS.project) {
    _TB_REFS.project   = document.getElementById("tb_project");
    _TB_REFS.sheet     = document.getElementById("tb_sheet");
    _TB_REFS.ceiling   = document.getElementById("tb_ceiling");
    _TB_REFS.scale     = document.getElementById("tb_scale");
    _TB_REFS.selection = document.getElementById("tb_selection");
    if (!_TB_REFS.project) return;
  }

  const scaleN = Math.max(1, Math.round(1 / Math.max(viewFloor.scale, 1e-6)));

  let selText = T("tbSelNone");
  if (state.selectedCable) {
    const tid = trueCableIdOf(state.selectedCable);
    const sibs = trueCableSiblings(state.selectedCable);
    selText = T("tbSelCable")(tid, sibs.length);
  } else if (state.selectedVertex) {
    selText = T("tbSelVertex")(state.selectedVertex.index + 1);
  } else if (drawing) {
    selText = T("tbSelDrawing")(drawing.anchorIds.length);
  }

  _TB_REFS.project.textContent   = T("tbProjectVal");
  _TB_REFS.sheet.textContent     = T("tbSheetVal")(GEOMETRY.cutZ);
  _TB_REFS.ceiling.textContent   = T("tbCeilingVal")(WALL_HEIGHT);
  _TB_REFS.scale.textContent     = T("tbScaleVal")(scaleN, viewFloor.zoom);
  _TB_REFS.selection.textContent = selText;
}

/* ==========================================================================
   CABLE INSPECTOR REFRESHER
   ========================================================================== */

function _cableSyncInspector() {
  const section   = document.getElementById("cableSection");
  const idxEl     = document.getElementById("cableIndex");
  const vertexEl  = document.getElementById("cableVertexInfo");
  const delVertEl = document.getElementById("cableDeleteVertexBtn");
  if (!section || !idxEl || !vertexEl || !delVertEl) return;

  const cable = state.selectedCable;
  if (!cable) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";

  const tid    = trueCableIdOf(cable);
  const sibs   = trueCableSiblings(cable);
  const floors = sibs.filter(c => state.floorCables.includes(c)).length;
  const walls  = sibs.filter(c => state.wallCables.includes(c)).length;

  idxEl.textContent = T("ciHeader")(tid, sibs.length, floors, walls);

  const sv = state.selectedVertex;
  if (sv && sv.cable === cable) {
    const a = anchors.get(cable.anchorIds[sv.index]);
    let tag = T("ciVertexBase")(sv.index + 1);
    if (a) {
      if (a.space === "wall-edge") {
        const side = anchorSide(a);
        if (side === "top")         tag += "  ·  " + T("ciVertexStepTop");
        else if (side === "bottom") tag += "  ·  " + T("ciVertexStepBot");
        else                        tag += "  ·  " + T("ciVertexWallEdge");
        if (junctionOfAnchor(a) >= 0)
          tag += "  ·  " + T("ciVertexCorner");
      } else if (a.gridId != null) {
        tag += "  ·  " + T("ciVertexGrid");
      } else {
        tag += "  ·  " + T("ciVertexFree");
      }
    }
    vertexEl.textContent = tag;
    delVertEl.style.display = "block";
  } else {
    vertexEl.textContent = T("ciVertexNone");
    delVertEl.style.display = "none";
  }
}

/* ==========================================================================
   PANEL FADE
   ========================================================================== */

(function installPanelFade() {
  const ui = document.getElementById("ui");
  if (!ui) return;
  ui.style.transition = "none";
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
  if (panelHovered || drawing || state.dragGrid || state.dragVertex ||
      state.pan || state.ruler.active) {
    ui.style.opacity = "";
    return;
  }
  if (!mouse.inside) { ui.style.opacity = ""; return; }
  const idle = performance.now() - panelLastActivity;
  ui.style.opacity = idle > 1800 ? "0.42" : "";
}

/* ==========================================================================
   INSTRUCTIONS POPOVER
   ==========================================================================
   The popover body comes from T("popInstructionsHTML") — the English
   original that used to live in the #hint div in pg_core's HTML_HEAD
   now lives in pg_i18n's translation table.  Switching LANG switches
   the popover's language along with every other label.

   The popover is appended to document.body (outside #ui), so its
   styling lives in installDarkScreenStyles rather than inheriting
   from the panel. */

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

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pop.classList.contains("show")) {
      closePop();
      e.stopPropagation();
    }
    if (e.key === "?" || (e.shiftKey && e.key === "/")) {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      e.preventDefault();
      if (pop.classList.contains("show")) closePop();
      else                                 openPop();
    }
  });
})();

/* ==========================================================================
   STATUS / CURSOR
   ==========================================================================
   Every user-facing string in updateStatus is T()-sourced.  The
   status line's fixed priority — pan > ruler > drawing > grid drag >
   vertex drag > hovered wall > selected cable > cursor position —
   is unchanged. */

function updateStatus() {
  if (statusTimer) return;
  if (state.pan) {
    statusEl.textContent = T("stPanning")(state.pan.view);
    statusEl.className = "";
    return;
  }
  if (state.ruler.active) {
    const n = state.ruler.measures.length;
    const sel = state.ruler.selected;
    const step = state.ruler.pending
      ? (state.ruler.pendingEnd
          ? T("stRulerStepLocked")
          : T("stRulerStepSecond"))
      : (sel >= 0
          ? T("stRulerStepEditing")(sel + 1)
          : T("stRulerStepNew"));
    let hints = "";
    if (state.ruler.pending && !state.ruler.pendingEnd) {
      if (state.ruler.axisPref) {
        hints = T("stRulerAxisFixed")(state.ruler.axisPref);
      } else {
        hints = T("stRulerAxisAuto");
      }
    }
    const numeric = state.ruler.pending ? T("stRulerNumeric") : "";
    const stepHint = state.ruler.ignoreSteps
      ? T("stRulerSkipOn")
      : T("stRulerSkipOff");
    statusEl.textContent =
      T("stRulerCount")(n) + " · " + step +
      hints + numeric + stepHint + T("stRulerExit");
    statusEl.className = "warn";
    return;
  }
  if (drawing) {
    const n = drawing.anchorIds.length;
    const snapHint = snapEnabled ? T("stDrawingSnapOn")
                                 : T("stDrawingSnapHint");
    if (drawing.view === null) {
      statusEl.textContent = T("stDrawingFirst") + snapHint;
    } else {
      const base = drawing.baseCableId != null
        ? T("stDrawingAdopted") : "";
      let focusHint = "";
      if (drawing.view === "wall" && focusSeg >= 0) {
        const s = WALL.segments[focusSeg];
        const tag = s && s.tag ? s.tag : ("segment " + (focusSeg + 1));
        focusHint = T("stDrawingFocus")(tag);
      }
      statusEl.textContent =
        T("stDrawingN")(drawing.view, n) + base + focusHint +
        snapHint + T("stDrawingFinish");
    }
    statusEl.className = "warn"; return;
  }
  if (state.dragGrid) {
    const g = state.dragGrid.grid;
    statusEl.textContent = T("stGridDrag")(state.dragGrid.view, g.type,
                                           g.pos.toFixed(0));
    statusEl.className = ""; return;
  }
  if (state.dragVertex) {
    const { cable, index } = state.dragVertex;
    const a = anchors.get(cable.anchorIds[index]);
    let tag = "";
    if (a) {
      if (a.space === "wall-edge") {
        const s = anchorSide(a);
        if (s === "top")         tag = T("stVertexStepTop");
        else if (s === "bottom") tag = T("stVertexStepBot");
        else                     tag = T("stVertexWallEdge");
        if (junctionOfAnchor(a) >= 0) tag += T("stVertexCorner");
      } else if (a.gridId != null) {
        tag = T("stVertexGrid");
      } else {
        tag = T("stVertexFree");
      }
    }
    const snapHint = snapEnabled ? "" : T("stVertexSnapHint");
    statusEl.textContent = T("stVertexTagBase")(index) + tag + snapHint;
    statusEl.className = (a && a.space === "wall-edge") ? "warn" : "";
    return;
  }
  if (state.hoveredRoute) {
    const seg = WALL.segments[state.hoveredRoute.segIdx];
    const tag = seg && seg.tag ? seg.tag
              : ("segment " + (state.hoveredRoute.segIdx + 1));
    const len = seg ? fmtCm(seg.len) : "?";
    const kind = isStepFace(seg) ? T("stHoverStep") : "";
    statusEl.textContent = T("stHoverSeg")(tag) + kind + T("stHoverLen")(len);
    statusEl.className = "warn"; return;
  }
  if (state.selectedCable) {
    const sibs = trueCableSiblings(state.selectedCable);
    if (sibs.length > 1) {
      const tid = trueCableIdOf(state.selectedCable);
      const floors = sibs.filter(c => state.floorCables.includes(c)).length;
      const walls  = sibs.filter(c => state.wallCables.includes(c)).length;
      statusEl.textContent = T("stCableGroup")(tid, sibs.length, floors, walls);
      statusEl.className = "ok";
      return;
    }
  }
  if (!mouse.inside || !mouse.view) {
    statusEl.textContent = ""; statusEl.className = ""; return;
  }
  if (mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    const onStep = pointOnStep(wx, wy) ? T("stOnStep") : "";
    const zSuffix = Math.abs(viewFloor.zoom - 1) > 0.02
      ? T("stZoom")(Math.round(viewFloor.zoom * 100)) : "";
    statusEl.textContent = T("stFloorPos")(wx.toFixed(0), wy.toFixed(0)) +
                           onStep + zSuffix;
    statusEl.className = "ok";
  } else {
    const [u, v] = s2wWall(mouse.sx, mouse.sy);
    const zSuffix = Math.abs(viewWall.zoom - 1) > 0.02
      ? T("stZoom")(Math.round(viewWall.zoom * 100)) : "";
    statusEl.textContent = T("stWallPos")(u.toFixed(0), v.toFixed(0)) +
                           zSuffix;
    statusEl.className = "ok";
  }
}

function updateCursor() {
  if (!mouse.inside) { canvas.style.cursor = "default"; return; }
  if (state.pan) { canvas.style.cursor = "grabbing"; return; }
  if (state.ruler.active) { canvas.style.cursor = "crosshair"; return; }
  if (spaceHeld && !drawing) { canvas.style.cursor = "grab"; return; }
  if (drawing) { canvas.style.cursor = "crosshair"; return; }
  if (state.hoveredRoute) { canvas.style.cursor = "pointer"; return; }
  canvas.style.cursor = "crosshair";
}
"""
