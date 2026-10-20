"""
pg_base.py — boxes' project CSS and panel body markup.

The shared shell — the <html>/<head> structure, the panel wrapper,
status line, the restore button, the "controlled remotely" banner,
and the <canvas> — is provided by `pgkit.html_shell.page()`.  This
file supplies the two pieces that are specific to the boxes
playground:

    PG_CSS          extras beyond pgkit's SHELL_CSS: the conditional
                    GUIDES strip (visible only while draw-box mode is
                    armed), the conditional BOX inspector (visible
                    only while a box is selected), the instructions
                    popover, and the title block.

    PG_PANEL_BODY   the panel's markup — the GUIDES strip, the draw
                    tool toggle, the Edit and Output sections, the
                    BOX inspector, the title block, the status line.

In the previous concatenation model, the CSS and the panel markup
started from small placeholders and were dynamically reshaped at
runtime by `installVerticalMenu` and `installDarkScreenStyles` in
`boxes_panel.py`.  In the ES-module model, the CSS and markup below
are already the final shape; both functions become no-ops on the
first call.  This is the same convention `board/pg_base.py` and
`cone/pg_base.py` use: the panel CSS and the panel body are the
authoritative end-state, not a base that gets patched.

The @font-face block and every `font-family` reference come from
`playground_fonts.py` via the same placeholder-substitution the
current `boxes_html.py` already performs in `inject_head`.  The
substitution happens here so the page() call and the CSS agree
exactly.

Conditional visibility
----------------------
Two elements begin hidden and are shown by a sync function called
from `draw()`:

    #boxSection    starts display:none in the CSS; `_boxSyncInspector`
                   sets display:block when a box is selected

    .guidesItem    every element that belongs to the GUIDES strip
                   carries this class; the CSS rule
                   #ui:not(.draw-mode) .guidesItem { display:none }
                   hides them while draw-box mode is off, and
                   `updateDrawBoxButton` (in bx_dispatch.py) toggles
                   the `draw-mode` class on `#ui`.

The inputs' VALUES are untouched by these toggles: display:none does
not clear an <input>'s value, so the defaults the user last set
survive every mode flip with no save/restore code.

Field chrome
------------
The text and number inputs share the visual chrome of the board
playground's scrub-input fields (pgkit/styles.py:SHELL_CSS,
`input.scrubInput`): transparent background, a single bottom rule,
cyan tabular numerals, an underline that brightens on hover and
goes full cyan on focus, and a dimmer disabled state.  Matching
that chrome makes the two surfaces — boxes and board — read as the
same family.

The boxes inputs are typed rather than scrubbed, so the base rule
keeps the browser's text cursor rather than advertising scrubbing
with `cursor: ew-resize`; the underline alone carries the visual
cue.  Native `type="number"` spinners are hidden for the same
reason the board avoids them: the chrome is "text-like field with
an underline", not "browser number spinner with an underline".

Instructions popover
--------------------
The instructions popover (id `helpPopover`) is injected into
document.body by `installInstructionsPopover` in
`bx_panel_bindings.py`.  Its styling lives in PG_CSS here so
the CSS is loaded once, at page assembly, rather than injected at
runtime.  This matches the pattern in the cable playground, where
the same popover is styled in `pg_panel.py` via the panel style
sheet.
"""

from playground_fonts import (
    FONT_FAMILY_MONO,
    FONT_FAMILY_SANS,
    FONT_FACE_CSS,
)


def _subst(template):
    return (template
            .replace("@FONT_MONO@", FONT_FAMILY_MONO)
            .replace("@FONT_SANS@", FONT_FAMILY_SANS))


# ----------------------------------------------------------------------------
#  PG_CSS — the panel's post-install stylesheet
# ----------------------------------------------------------------------------

_PG_CSS_RAW = r"""
  /* ==========================================================================
     DARK SCREEN STYLE — "printed form" idiom
     ==========================================================================
     Everything below is the stable stylesheet the panel has at rest
     after the old installDarkScreenStyles pass ran.  Section labels
     are real static markup (in PG_PANEL_BODY), not injected, so the
     whole panel reads the same way whether it was opened by a
     first-load of a fresh tab or reloaded with a saved board. */

  html, body {
    background: #0a0e14 !important;
    color: #dce6f2 !important;
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    font-family: @FONT_MONO@;
  }
  canvas { background: #0a0e14 !important; display: block; }

  #ui {
    background: #0b1018;
    border: 1px solid #1e2836;
    border-radius: 3px;
    box-shadow: 0 6px 28px rgba(0, 0, 0, 0.65);
    font-family: @FONT_MONO@;
    font-size: 10.5px;
    line-height: 1.35;
    color: #a8b5c4;
    user-select: none;
    width: 200px;
    padding: 10px 12px 12px;
    box-sizing: border-box;
    position: absolute;
    top: 12px; left: 12px; z-index: 10;
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
    font-family: @FONT_MONO@;
    font-size: 11px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: #dce6f2;
    flex: 0 0 auto;
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
    font-family: @FONT_MONO@ !important;
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

  /* ---- section labels: "GUIDES ────────────────" ---- */
  #ui .sectionLabel {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 12px 0 6px;
    font-family: @FONT_MONO@;
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

  /* ---- buttons ---- */
  #ui button {
    width: 100%;
    background: transparent;
    border: 1px solid #223040;
    color: #b8c4d2;
    font-family: @FONT_MONO@;
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

  #ui #boxDeleteBtn {
    width: 100%; padding: 5px 8px; text-align: center;
    font-family: @FONT_MONO@;
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

  #ui hr { display: none !important; }

  /* ---- rows ---- */
  #ui .row {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin: 0;
    padding: 0;
    align-items: stretch;
  }
  #ui .row.grid2x2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    flex-direction: initial;
  }
  #ui .row.grid3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 4px;
    flex-direction: initial;
  }
  #ui .row.grid2x2 button,
  #ui .row.grid3 button {
    text-align: center; padding: 6px 4px; font-size: 10px;
    letter-spacing: 0.04em;
  }

  /* ---- text / number fields ----
     The visual chrome matches the board playground's scrub-input
     fields (pgkit/styles.py:SHELL_CSS `input.scrubInput`): a
     transparent background, a single bottom rule, cyan tabular
     numerals, and an underline that brightens on hover and goes
     full cyan on focus.  Numeric fields right-align so their
     digits line up; the name field left-aligns.  Native number
     spinners are hidden so the fields read as text, matching the
     board's type=text scrub inputs.  The inputs are typed, not
     scrubbed, so the base cursor is the browser's text cursor
     rather than the scrub-input's `ew-resize` hint — the
     underline alone is the visual cue. */
  #ui input[type=number],
  #ui input[type=text] {
    background: transparent;
    border: 0;
    border-bottom: 1px solid #223040;
    color: #00e5ff;
    font-family: inherit;
    font-size: 12px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    padding: 2px 4px;
    margin: 0;
    outline: none;
    box-sizing: border-box;
    text-align: right;
    transition: border-color 0.1s ease;
  }
  #ui input[type=text] { text-align: left; }
  #ui input[type=number]:hover,
  #ui input[type=text]:hover {
    border-bottom-color: #34445a;
  }
  #ui input[type=number]:focus,
  #ui input[type=text]:focus {
    border-bottom-color: #00e5ff;
  }
  #ui input[type=number]:disabled,
  #ui input[type=text]:disabled {
    color: #3d4756;
    border-bottom-color: #1e2836;
    cursor: not-allowed;
  }
  #ui input[type=number]::-webkit-outer-spin-button,
  #ui input[type=number]::-webkit-inner-spin-button {
    -webkit-appearance: none;
    margin: 0;
  }
  #ui input[type=number] {
    -moz-appearance: textfield;
    appearance: textfield;
  }

  /* ---- GUIDES strip ---- */
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
    font-family: @FONT_MONO@;
    font-size: 8.5px; font-weight: 700; letter-spacing: 0.18em;
    text-transform: uppercase; color: #5a6774;
  }
  #ui .row.fieldColumn label > input {
    flex: 1 1 auto; min-width: 0;
  }

  /* ---- fieldRow (name field) ----
     The name field uses a single-column label stack so the input
     stretches the full panel width, matching the numeric fields'
     visual weight. */
  #ui .row.fieldRow label {
    display: flex; flex-direction: column; gap: 2px;
    flex: 1; min-width: 0;
  }
  #ui .row.fieldRow label > span.lbl {
    font-family: @FONT_MONO@;
    font-size: 8.5px; font-weight: 700; letter-spacing: 0.18em;
    text-transform: uppercase; color: #5a6774;
  }
  #ui .row.fieldRow input {
    width: 100%; box-sizing: border-box;
  }

  /* The GUIDES strip is the "what will a click place" configurator
     and only matters when draw-box mode is armed.  Every element
     inside carries .guidesItem and the following rule hides them
     unless the root has .draw-mode.  The inputs' VALUES are not
     touched; display:none leaves an <input>'s value alone. */
  #ui:not(.draw-mode) .guidesItem { display: none; }

  /* ---- BOX inspector (conditional) ---- */
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
    font-family: @FONT_MONO@;
    font-size: 8.5px; font-weight: 700;
    letter-spacing: 0.16em; text-transform: uppercase;
    color: #00e5ff;
    line-height: 1.5; overflow-wrap: anywhere;
  }
  #ui .boxSelInfo {
    font-family: @FONT_MONO@;
    font-size: 10px; font-weight: 600;
    letter-spacing: 0.04em; color: #a8b5c4;
    overflow-wrap: anywhere;
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
    font-family: @FONT_MONO@;
    font-size: 8.5px; letter-spacing: 0.20em; font-weight: 700;
    line-height: 1.3; text-transform: uppercase;
    color: #5a6774;
  }
  #ui .titleValue {
    font-family: @FONT_MONO@;
    font-size: 10.5px; line-height: 1.4; overflow-wrap: anywhere;
    color: #dce6f2;
  }

  /* ---- status ---- */
  #ui #status {
    font-family: @FONT_MONO@;
    font-size: 10px;
    line-height: 1.45;
    height: 48px;
    min-height: 48px;
    max-height: 48px;
    overflow-y: auto;
    overflow-x: hidden;
    overflow-wrap: anywhere;
    box-sizing: border-box;
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid #1e2836;
    color: #7b8794;
  }
  #ui #status.ok   { color: #00e5ff; }
  #ui #status.bad  { color: #d48590; }
  #ui #status.warn { color: #ffe600; }

  /* ---- collapse / restore / banner ---- */
  #restoreBtn {
    padding: 6px 12px; background: #0b1018;
    border: 1px solid #1e2836; border-radius: 3px;
    box-shadow: 0 6px 28px rgba(0, 0, 0, 0.65);
    color: #a8b5c4;
    font-family: @FONT_MONO@;
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.10em; text-transform: uppercase;
    cursor: pointer;
    position: absolute; top: 12px; left: 12px; z-index: 10;
    display: none;
  }
  #restoreBtn:hover {
    background: #141a24; border-color: #34445a; color: #dce6f2;
  }
  #restoreBtn.visible { display: block; }

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
    font-family: @FONT_MONO@;
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
    font-family: @FONT_MONO@;
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
    font-family: @FONT_SANS@;
    font-size: 12.5px;
    line-height: 1.65;
    color: #a8b5c4;
  }
  #helpPopover .body b { color: #dce6f2; font-weight: 700; }
"""

PG_CSS = FONT_FACE_CSS + _subst(_PG_CSS_RAW)


# ----------------------------------------------------------------------------
#  PG_PANEL_BODY — the final panel markup
# ----------------------------------------------------------------------------

PG_PANEL_BODY = r"""
    <div class="sectionLabel">Guides</div>
    <div class="row fieldColumn guidesItem">
      <label><span class="lbl">W</span>
        <input type="number" id="boxW" value="2000" step="100"></label>
      <label><span class="lbl">H</span>
        <input type="number" id="boxH" value="1500" step="100"></label>
      <label><span class="lbl">Rot</span>
        <input type="number" id="boxRot" value="0" step="15"></label>
    </div>
    <div class="row grid2x2 guidesItem">
      <button id="rotL90" title="Rotate the selected (or hovered) box by -90°">- 90°</button>
      <button id="rotR90" title="Rotate the selected (or hovered) box by +90°">+ 90°</button>
      <button id="rotL15" title="Rotate by -15°">- 15°</button>
      <button id="rotR15" title="Rotate by +15°">+ 15°</button>
    </div>

    <div class="sectionLabel">Draw</div>
    <div class="row">
      <button id="drawBoxBtn" title="Arm the drawing tool — click to place a preset box, drag to draw an axis-aligned box (Esc to disarm)">Draw box</button>
    </div>

    <div id="boxSection" class="boxSection">
      <div class="sectionLabel">Box</div>
      <div class="boxInspector">
        <div id="boxIndex" class="boxIndex">—</div>
        <div class="row fieldRow">
          <label style="flex:1;">
            <span class="lbl">Name</span>
            <input type="text" id="boxName" autocomplete="off" spellcheck="false">
          </label>
        </div>
        <div class="row fieldColumn">
          <label><span class="lbl">W</span>
            <input type="number" id="selW" step="10" min="10"></label>
          <label><span class="lbl">H</span>
            <input type="number" id="selH" step="10" min="10"></label>
          <label><span class="lbl">Rot</span>
            <input type="number" id="selRot" step="15"></label>
        </div>
        <div id="selInfo" class="boxSelInfo">no box selected</div>
        <button id="boxDeleteBtn">Delete box</button>
      </div>
    </div>

    <div class="sectionLabel">Edit</div>
    <div class="row grid3">
      <button id="saveBtn" class="primary" title="Write room_boxes.json (Ctrl/Cmd+S)">Save</button>
      <button id="undoBtn" title="Remove the last placed box">Undo</button>
      <button id="clearBtn" title="Delete every box">Clear all</button>
    </div>

    <div class="sectionLabel">Output</div>
    <div class="row grid2x2">
      <button id="pngBtn" title="Save a black-and-white room_boxes.png">Print PNG</button>
      <button id="listBtn" title="Print the box table to the terminal">List boxes</button>
    </div>

    <div class="titleBlock">
      <div class="titleRow">
        <div class="titleLabel">Project</div>
        <div class="titleValue" id="tb_project">room_walls.json</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel">Sheet</div>
        <div class="titleValue" id="tb_sheet">—</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel">Boxes</div>
        <div class="titleValue" id="tb_boxes">—</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel">Scale</div>
        <div class="titleValue" id="tb_scale">—</div>
      </div>
      <div class="titleRow">
        <div class="titleLabel">Selection</div>
        <div class="titleValue" id="tb_selection">—</div>
      </div>
    </div>

    <div class="sectionLabel">Status</div>
    <div id="status"></div>
"""
