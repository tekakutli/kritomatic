"""
pg_base.py — cable playground's panel CSS and panel body.

The shared shell — `<html>`/`<head>`, the `#ui` wrapper, the collapse
button in the header, the restore button, the "controlled remotely"
banner, and the `<canvas>` — comes from `pgkit.html_shell.page()`.
This file supplies the rest: **the final panel CSS and the final
panel markup**.

WHAT THE OLD VERSION DID WRONG
==============================
In the concatenation era, this file supplied only a *base*:
light-themed panel CSS and a bootstrap panel body.  Two JS-side
installers patched the base into the final shape at boot:

    installVerticalMenu   appended a second `<style>` that reshaped
                          the panel to 200px and injected section
                          labels, the snap pill row, the MEASURE /
                          CABLE sections, the title block, and the
                          output buttons.

    installDarkScreenStyles
                          appended a third `<style>` that reskinned
                          everything to dark theme and hid
                          `#rulerNumericInput` and `#helpPopover`
                          until their `.show` class is set.

Both run mid-boot, after 45 dynamic imports have resolved.  For the
~200–500 ms between the browser's first paint and the moment those
installers fire, the page shows:

    · a light-themed 460px panel with bare buttons,
    · a white 300×150 canvas at its default CSS size,
    · the ruler numeric input (`#rulerNumericInput`) visible as an
      unstyled white box labelled with `rlInputSpan` ("span"/"luz").

That is the flash-of-unstyled-content the user sees on every reload.

WHAT THIS VERSION DOES
======================
Both runtime stylesheets are concatenated into `PG_CSS` here, and
the final panel markup is baked into `PG_PANEL_BODY`.  `page()`
injects `PG_CSS` into `<head>` at page-assembly time, so every rule
— dark theme, 200px width, ruler numeric input hidden, panel
sections in place, title block present — is active from the
browser's first paint.  There is no "before" state.

`installVerticalMenu` and `installDarkScreenStyles` in `pg_panel.py`
become no-ops.  The DOM-creation parts of `installExportButton`,
`installArrowDiagButton`, and `installArrowToggle` also become
no-ops; those buttons are baked here with `data-i18n` attributes and
the installers only attach event handlers.

TRANSLATION
===========
Every text element carries `data-i18n="<key>"` (or
`data-i18n-title="<key>"` for the title attribute).  `installVerticalMenu`
iterates those at boot and sets the text from `T()`.  The English
text baked here is the fallback — if `T()` hasn't run yet (or the
element is dynamic and set by another module's update function),
the English string is what the user sees.

Dynamic elements (`#drawBtn`, `#rulerBtn`, `#snapPill`, `#status`,
the title-block values, the measure/cable fields) do NOT have
`data-i18n`; they are set by their own update functions at draw
time.

`#rulerNumericInput` HIDING
===========================
The element is created by `pg_ruler.js` at module load — after 45
`await import()` calls.  Because this file bakes the `display: none`
rule (from the old `installDarkScreenStyles`) into `PG_CSS`, the
moment `pg_ruler.js` appends the element to `<body>`, the browser
applies `display: none` immediately.  It never appears in any
intermediate state.  Same for `#helpPopover`.
"""

from playground_fonts import FONT_FACE_CSS


PG_CSS = FONT_FACE_CSS + r"""
  html, body {
    margin:0; padding:0; height:100%; overflow:hidden;
    background:#0a0e14;
    color:#dce6f2;
  }

  /* ==========================================================================
     PANEL SHELL — final dark form
     ==========================================================================
     Base layout, panel width, palette, borders, button style,
     snap-pill, section labels, title block.  This is the final
     stylesheet; `installDarkScreenStyles` and `installVerticalMenu`
     used to append equivalent rules at boot, and their combined
     output is what has been folded in here. */

  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    width: 200px;
    padding: 10px 12px 12px;
    box-sizing: border-box;
    background: #0b1018;
    border: 1px solid #1e2836;
    border-radius: 3px;
    box-shadow: 0 6px 28px rgba(0, 0, 0, 0.65);
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 10.5px;
    line-height: 1.35;
    color: #a8b5c4;
    user-select: none;
    max-height: calc(100vh - 24px);
    display: flex;
    flex-direction: column;
  }
  #ui.collapsed { display: none; }

  #ui .panel-body {
    overflow-y: auto;
    overflow-x: hidden;
    min-height: 0;
    flex: 1 1 auto;
  }
  #ui .panel-body::-webkit-scrollbar { width: 6px; }
  #ui .panel-body::-webkit-scrollbar-thumb {
    background: #1e2836; border-radius: 3px;
  }

  /* ---- header ---- */
  #ui h3 {
    display: flex;
    align-items: center;
    gap: 4px;
    margin: 0 0 10px;
    padding: 0 0 8px;
    border-bottom: 1px solid #1e2836;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #dce6f2;
    flex: 0 0 auto;
  }

  /* ---- rows ---- */
  #ui .row {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin: 0;
    padding: 0;
    align-items: stretch;
  }
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
  #ui .row.grid2x2 button {
    text-align: center;
    padding: 6px 4px;
    font-size: 10px;
    letter-spacing: 0.04em;
  }

  /* ---- buttons ---- */
  #ui button {
    width: 100%;
    background: transparent;
    border: 1px solid #223040;
    color: #b8c4d2;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
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
  #ui button:active { background: rgba(220, 230, 242, 0.07); }

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
    border-color: #ffe600; color: #ffe600;
    background: rgba(255, 230, 0, 0.04);
  }
  #ui #rulerBtn.active {
    border-color: #00e5ff; color: #00e5ff;
    background: rgba(0, 229, 255, 0.05);
  }

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

  /* Old del-vertex/del-cable buttons: hidden, exposed via CABLE section */
  #ui #delVertexBtn,
  #ui #deleteBtn { display: none !important; }

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
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace !important;
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

  #ui hr { display: none !important; }

  /* ---- section labels: "GUIDES ────────────────" ---- */
  #ui .sectionLabel {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 12px 0 6px;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
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

  /* ---- snap pill ---- */
  #ui #snapPill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    margin: 0;
    padding: 3px 9px;
    background: transparent;
    border: 1px solid #223040;
    border-radius: 2px;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
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
  #ui #snapPill.on { border-color: #00e5ff; color: #00e5ff; }
  #ui #snapPill.on::after { content: "●"; color: #00e5ff; }

  /* ---- zoom reset ---- */
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
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 11px;
    font-weight: 700;
    line-height: 1;
    color: #5a6774;
    cursor: pointer;
    white-space: nowrap;
    box-sizing: border-box;
  }
  #ui #zoomResetBtn:hover {
    border-color: #34445a; color: #dce6f2;
    background: rgba(220, 230, 242, 0.03);
  }

  /* ---- title block ---- */
  #ui .titleBlock {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 12px 0 0;
    padding: 10px 0 0;
    border-top: 1px solid #1e2836;
  }
  #ui .titleRow { display: flex; flex-direction: column; gap: 1px; }
  #ui .titleLabel {
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 8.5px;
    letter-spacing: 0.20em;
    font-weight: 700;
    line-height: 1.3;
    text-transform: uppercase;
    color: #5a6774;
  }
  #ui .titleValue {
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 10.5px;
    line-height: 1.4;
    overflow-wrap: anywhere;
    color: #dce6f2;
  }

  /* ---- status ---- */
  #ui #status {
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 10px;
    line-height: 1.45;
    height: 48px;
    min-height: 48px;
    max-height: 48px;
    overflow-y: auto;
    overflow-x: hidden;
    overflow-wrap: anywhere;
    box-sizing: border-box;
    margin: 6px 0 0;
    padding: 6px 0 0;
    border-top: 1px solid #1e2836;
    color: #7b8794;
  }
  #ui #status.ok   { color: #00e5ff; }
  #ui #status.bad  { color: #d48590; }
  #ui #status.warn { color: #ffe600; }

  /* ---- MEASURE section ---- */
  #ui .measureSection { display: none; }
  #ui .measureInspector {
    display: flex; flex-direction: column; gap: 6px;
    margin: 2px 0 0; padding: 8px 8px;
    border: 1px solid #1e2836;
    border-left: 2px solid #00e5ff;
    border-radius: 2px;
    background: rgba(0, 229, 255, 0.02);
  }
  #ui .measureIndex {
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 8.5px; font-weight: 700;
    letter-spacing: 0.20em; text-transform: uppercase;
    color: #00e5ff;
  }
  #ui .measureGrid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 4px;
  }
  #ui .measureField {
    display: flex; flex-direction: column; gap: 2px; min-width: 0;
  }
  #ui .measureField .lbl {
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 8.5px; font-weight: 700;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: #5a6774;
  }
  #ui .measureField input {
    width: 100%; box-sizing: border-box;
    background: transparent;
    border: 1px solid #223040;
    border-radius: 2px;
    padding: 3px 6px;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 11px; font-weight: 700;
    color: #00e5ff; text-align: right; outline: 0;
  }
  #ui .measureField input:focus {
    border-color: #00e5ff;
    background: rgba(0, 229, 255, 0.06);
  }
  #ui #measureDeleteBtn {
    width: 100%; padding: 5px 8px; text-align: center;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.10em; text-transform: uppercase;
    background: transparent;
    border: 1px solid #4a2830; border-radius: 2px;
    color: #d48590; cursor: pointer;
  }
  #ui #measureDeleteBtn:hover {
    background: rgba(212, 133, 144, 0.10);
    border-color: #7a3e4a; color: #e8a0a8;
  }

  /* ---- CABLE section ---- */
  #ui .cableSection { display: none; }
  #ui .cableInspector {
    display: flex; flex-direction: column; gap: 6px;
    margin: 2px 0 0; padding: 8px 8px;
    border: 1px solid #1e2836;
    border-left: 2px solid #d48590;
    border-radius: 2px;
    background: rgba(212, 133, 144, 0.02);
  }
  #ui .cableIndex {
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 8.5px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: #dce6f2; line-height: 1.5;
    overflow-wrap: anywhere;
  }
  #ui .cableVertexInfo {
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.04em;
    color: #a8b5c4; overflow-wrap: anywhere;
  }
  #ui #cableDeleteVertexBtn,
  #ui #cableDeleteCableBtn {
    width: 100%; padding: 5px 8px; text-align: center;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.10em; text-transform: uppercase;
    background: transparent;
    border: 1px solid #4a2830; border-radius: 2px;
    color: #d48590; cursor: pointer;
  }
  #ui #cableDeleteVertexBtn:hover,
  #ui #cableDeleteCableBtn:hover {
    background: rgba(212, 133, 144, 0.10);
    border-color: #7a3e4a; color: #e8a0a8;
  }

  /* ---- arrow toggle (checkbox+label inside a row) ---- */
  #ui label.toggleLabel {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    user-select: none;
  }
  #ui label.toggleLabel input[type=checkbox] {
    width: auto;
    margin: 0;
    accent-color: #00e5ff;
    cursor: pointer;
  }

  /* ---- canvas ---- */
  canvas {
    display: block;
    touch-action: none;
    background: #0a0e14;
    width: 100vw;
    height: 100vh;
  }

  /* ---- restore button ---- */
  #restoreBtn {
    position: absolute; top: 12px; left: 12px; z-index: 10;
    display: none;
    padding: 6px 12px;
    background: #0b1018;
    border: 1px solid #1e2836;
    border-radius: 3px;
    box-shadow: 0 6px 28px rgba(0, 0, 0, 0.65);
    color: #a8b5c4;
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.10em; text-transform: uppercase;
    cursor: pointer;
  }
  #restoreBtn:hover {
    background: #141a24;
    border-color: #34445a;
    color: #dce6f2;
  }
  #restoreBtn.visible { display: block; }

  /* ---- instructions popover ---- */
  #helpPopover {
    position: fixed;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    z-index: 100;
    background: #0e1420;
    padding: 20px 24px;
    border: 1px solid #223040;
    border-radius: 3px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.70);
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
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
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    font-size: 11px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
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
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.10em; text-transform: uppercase;
    cursor: pointer;
  }
  #helpPopover .closeBtn:hover {
    border-color: #34445a; color: #dce6f2;
    background: rgba(220, 230, 242, 0.03);
  }
  #helpPopover .body {
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 12.5px;
    line-height: 1.65;
    color: #a8b5c4;
  }
  #helpPopover .body b { color: #dce6f2; font-weight: 700; }

  /* ---- ruler numeric input ---- */
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
    font-family: 'Fira Code', ui-monospace, Menlo, Consolas, monospace;
    color: #dce6f2;
    user-select: none;
  }
  #rulerNumericInput.show { display: inline-flex; }
  #rulerNumericInput .lbl {
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.20em; text-transform: uppercase;
    color: #5a6774;
  }
  #rulerNumericInput input {
    background: transparent; border: 0; outline: 0;
    padding: 0; margin: 0; width: 76px;
    font-family: inherit; font-size: 13px; font-weight: 700;
    color: #00e5ff; text-align: right; letter-spacing: 0.02em;
  }
  #rulerNumericInput .unit {
    font-size: 9px; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: #5a6774; min-width: 18px;
  }
"""


PG_PANEL_BODY = r"""
    <div class="sectionLabel" data-i18n="secGuides">Guides</div>
    <div class="row grid2x2">
      <button id="addNsGrid" data-i18n="btnAddNsGrid" data-i18n-title="tipAddNsGrid">+ N–S grid</button>
      <button id="addEwGrid" data-i18n="btnAddEwGrid" data-i18n-title="tipAddEwGrid">+ E–W grid</button>
      <button id="addWallV"  data-i18n="btnAddWallV"  data-i18n-title="tipAddWallV">+ Wall |</button>
      <button id="addWallH"  data-i18n="btnAddWallH"  data-i18n-title="tipAddWallH">+ Wall ─</button>
    </div>

    <div class="sectionLabel" data-i18n="secDraw">Draw</div>
    <div class="row grid2x2">
      <button id="drawBtn"  data-i18n-title="tipDrawCable">Draw cable</button>
      <button id="rulerBtn" data-i18n-title="tipMeasure">Measure</button>
    </div>

    <div id="measureSection" class="measureSection" style="display:none;">
      <div class="sectionLabel" data-i18n="secMeasure">Measure</div>
      <div id="measureInspector" class="measureInspector">
        <div id="measureIndex" class="measureIndex">—</div>
        <div class="measureGrid">
          <div class="measureField">
            <span class="lbl" data-i18n="lblSpanCm">Span cm</span>
            <input type="text" inputmode="decimal" id="measureSpan"
                   autocomplete="off" spellcheck="false">
          </div>
          <div class="measureField">
            <span class="lbl" data-i18n="lblAngleDeg">Angle °</span>
            <input type="text" inputmode="decimal" id="measureAngle"
                   autocomplete="off" spellcheck="false">
          </div>
        </div>
        <button id="measureDeleteBtn" data-i18n="btnDeleteMeasurement">Delete measurement</button>
      </div>
    </div>

    <div id="cableSection" class="cableSection" style="display:none;">
      <div class="sectionLabel" data-i18n="secCable">Cable</div>
      <div id="cableInspector" class="cableInspector">
        <div id="cableIndex" class="cableIndex">—</div>
        <div id="cableVertexInfo" class="cableVertexInfo">—</div>
        <button id="cableDeleteVertexBtn" data-i18n="btnDeleteVertex">Delete vertex</button>
        <button id="cableDeleteCableBtn"  data-i18n="btnDeleteCable">Delete cable</button>
      </div>
    </div>

    <div class="sectionLabel" data-i18n="secEdit">Edit</div>
    <div class="row grid2x2">
      <button id="saveBtn"      class="primary" data-i18n="btnSave"         data-i18n-title="tipSave">Save</button>
      <button id="delVertexBtn" class="danger"  data-i18n="btnDeleteVertex" data-i18n-title="tipDeleteVertex">Delete vertex</button>
      <button id="deleteBtn"    class="danger"  data-i18n="btnDeleteCable"  data-i18n-title="tipDeleteCable">Delete cable</button>
      <button id="clearBtn"                     data-i18n="btnClearAll"     data-i18n-title="tipClearAll">Clear all</button>
    </div>

    <div class="sectionLabel" data-i18n="secOutput">Output</div>
    <div class="row">
      <button id="exportBtn" data-i18n="exportButton" data-i18n-title="exportButtonTitle">Export cable runs (print)</button>
    </div>
    <div class="row">
      <button id="arrowDiagBtn" data-i18n="btnDiagnostics" data-i18n-title="tipDiagnostics">Diagnostics</button>
    </div>
    <div class="row">
      <label class="toggleLabel">
        <input type="checkbox" id="arrowVisibilityToggle" data-i18n-title="arrowToggleTitle">
        <span data-i18n="arrowToggleLabel">Show arrows</span>
      </label>
    </div>

    <div class="titleBlock">
      <div class="titleRow">
        <div class="titleLabel" data-i18n="tbProject">Project</div>
        <div class="titleValue" id="tb_project" data-i18n="tbProjectVal">room.step</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel" data-i18n="tbSheet">Sheet</div>
        <div class="titleValue" id="tb_sheet">—</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel" data-i18n="tbCeiling">Ceiling</div>
        <div class="titleValue" id="tb_ceiling">—</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel" data-i18n="tbScale">Scale</div>
        <div class="titleValue" id="tb_scale">—</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel" data-i18n="tbSelection">Selection</div>
        <div class="titleValue" id="tb_selection">—</div>
      </div>
    </div>

    <div class="sectionLabel" data-i18n="secStatus">Status</div>
    <div id="status"></div>
"""
