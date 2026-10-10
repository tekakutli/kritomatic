"""
pg_base.py — palette, stylesheet, HTML head, bootstrap.

The panel carries five graphics sliders, seven scrub-inputs bound to
the selection (the selected patch's φ and s, the selected square's
φ, s, W, H, and slope), a global standing-height multiplier, ten
buttons (Reset, Center apex, Save scene, Load scene, Export visual
state, Generate .kra, + Patch, + Clone, + Square, + Clone), a
KRA-export options block (text position, text padding, text warp
mode, rectangle on/off, text color), a per-patch mirror checkbox,
two lists, and a help popup.

SCROLLABLE, COLLAPSIBLE PANEL
=============================
The panel can be taller than the viewport on short screens.  Two
mechanisms keep every control reachable:

    scroll      the whole panel body (everything below the title
                bar) lives inside a scroll container.  The title
                bar and its collapse button stay pinned.  When the
                content is shorter than the viewport, no scrollbar
                is shown.

    sections    the panel's fields are grouped into five
                collapsible <details> blocks: View, Patches,
                Squares, Draw, Export.  Each one's summary is
                styled like the section headers the panel used to
                carry as plain text; clicking a summary toggles
                that block.  Every section starts open by default;
                the user can collapse the ones they don't need to
                cut the panel's height.  Open/closed state is
                per-session; a page reload returns every section
                to open.

CHANGES FROM THE PREVIOUS REVISION
==================================
The persistent hint block that used to live inline in the panel
has been moved into a separate help popup subwindow, opened by a
"?" button in the panel's title bar.  The panel also carries a
"Draw fit" select, a "No rotate" checkbox, and a "Draw Quad"
button, which drive the four-corner fitting tool (see pg_core.py,
DRAW TOOL).  The help card in the popup documents both fit modes
and the rotation lock; the panel itself carries no inline hint
text any more.

A per-patch "Mirror" checkbox sits next to the Patch φ and Patch s
scrub fields.  When checked, every square on the selected patch is
drawn a second time on the patch's mirror — the same patch shifted
by π in φ, i.e. on the diametrically opposite side of the cone.
The mirror copy is a pure visual clone: it shares the square's full
local state and is not independently editable.  See
pg_view_squares.py (MIRROR) for the model and helpers.  Two
companion fields — a "Mirror ∠" scrub input and a "Mirror flip"
checkbox — let the mirror be pulled off the diametric line and
reflected across the patch's V axis rather than merely shifted,
respectively.  See pg_panel.py for the model.

A per-square "Visual bot." checkbox sits after the Slope field.
When checked, the shape's text anchor and the four corner senses
(which corner is read as top-left, top-right, bottom-right,
bottom-left) are anchored to the visual bottom of the scene — the
edge of the square whose midpoint sits lowest on screen — instead
of the shape's own longest edge.  The flag is a per-square field
(sq.visualBottom) and is read by pg_kra.py's
_shapeLabelScreenAngleDeg; see that module and pg_panel.py for
the details.

Two per-square scrub inputs, "Square φ" and "Square s", sit above
the Square W field.  They show and set the selected shape's centre
in the FLAT view's (φ, s) coordinates — the same numbers the
unfolded sheet is drawn in, and the same numbers the status line
reports for a cursor over that sheet.  φ is displayed in radians on
[0, 2π); the cone wraps, so typing a value on the far side of the
seam moves the shape the short way around.  See the SQUARE PHI / S
section in pg_panel.py.

The Slope row now carries a second input, the "standing height
multiplier".  It is a global aesthetic knob (not per-square):
controls how tall a hinged shape stands, as a multiple of its
flat-view height.  See STANDING HEIGHT MULTIPLIER in
pg_view_squares.py.

Every checkbox in the panel carries a `title` attribute, so
hovering it (or its surrounding field) shows a one-line explanation
of what the checkbox does and — where the flag is read at
draw-finish time rather than at toggle time — when the change takes
effect.  The three checkboxes that already carried one
(patchMirror, patchMirrorFlip, squareVisualBottom) are joined by
drawNoRotate and kraDrawRects; no other panel control is
unexplained.

The "Generate .kra" button carries a bright-red accent — the
export action reads as its own tier in the palette, distinct from
the neutral grey/cyan sliders and the amber patch lists.  The
resting text is bright enough (a light rose with a faint glow) to
stay legible at the 10px uppercase size the panel uses, rather
than receding into the background until hover.
"""

HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cone interior — viewer</title>
<style>
  html, body {
    margin:0; padding:0; height:100%; overflow:hidden;
    background:#0a0e14; color:#dce6f2;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas',
                 'SF Mono', monospace;
  }

  /* The panel is a flex column: the title bar stays pinned, the
     body scrolls.  max-height is the viewport minus the 12px top
     and 12px bottom margins the panel's absolute position already
     reserves, so the panel never exceeds the visible area. */
  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    background:#0b1018;
    padding:12px 14px;
    border:1px solid #1e2836;
    border-radius:3px;
    box-shadow:0 6px 28px rgba(0,0,0,0.65);
    font-size:11px; color:#a8b5c4;
    user-select:none;
    width:288px; box-sizing:border-box;
    max-height: calc(100vh - 24px);
    display: flex;
    flex-direction: column;
  }
  #ui.collapsed { display:none; }

  #ui h3 {
    display:flex; align-items:center; gap:6px;
    margin:0 0 4px; padding:0 0 8px;
    border-bottom:1px solid #1e2836;
    font-size:11px; font-weight:700;
    letter-spacing:0.16em; text-transform:uppercase;
    color:#dce6f2;
    flex: 0 0 auto;
  }

  /* The scrollable body.  min-height: 0 is the flexbox quirk that
     lets a flex child actually overflow instead of forcing the
     parent to grow.  The horizontal negative margin + matching
     right padding keeps the scrollbar from eating layout width,
     so field widths don't visibly shift when the bar appears. */
  #ui .panel-body {
    overflow-y: auto;
    overflow-x: hidden;
    min-height: 0;
    flex: 1 1 auto;
    margin-right: -6px;
    padding-right: 6px;
  }
  #ui .panel-body::-webkit-scrollbar { width: 6px; }
  #ui .panel-body::-webkit-scrollbar-track { background: transparent; }
  #ui .panel-body::-webkit-scrollbar-thumb {
    background: #1e2836; border-radius: 3px;
  }
  #ui .panel-body::-webkit-scrollbar-thumb:hover {
    background: #2a3848;
  }

  #ui .field { display:flex; align-items:center; gap:8px; margin:4px 0; }
  #ui .field label {
    flex:0 0 78px;
    font-size:9px; color:#5a6774;
    letter-spacing:0.14em; text-transform:uppercase;
    font-weight:700;
  }
  #ui .field input[type=range] {
    flex:1 1 auto; min-width:0;
    accent-color:#00e5ff;
  }
  #ui .field input[type=checkbox] {
    flex:0 0 auto;
    margin-left:auto;
    accent-color:#00e5ff;
    cursor:pointer;
    width:14px; height:14px;
  }
  #ui .field .val {
    flex:0 0 44px;
    font-size:11px; color:#00e5ff;
    text-align:right; font-weight:700;
    font-variant-numeric: tabular-nums;
  }

  #ui .field input.scrubInput {
    flex:1 1 auto;
    min-width:0;
    background:transparent;
    border:0;
    border-bottom:1px solid #223040;
    color:#00e5ff;
    font-family:inherit;
    font-size:12px;
    font-weight:700;
    font-variant-numeric: tabular-nums;
    text-align:right;
    padding:2px 4px;
    margin:0;
    outline:none;
    cursor:ew-resize;
    user-select:none;
    transition: border-color 0.1s ease;
  }
  #ui .field input.scrubInput:hover {
    border-bottom-color:#34445a;
  }
  #ui .field input.scrubInput:focus {
    border-bottom-color:#00e5ff;
    cursor:text;
    user-select:text;
  }
  #ui .field input.scrubInput:disabled {
    color:#3d4756;
    border-bottom-color:#1e2836;
    cursor:not-allowed;
  }

  #ui .field select {
    flex:1 1 auto;
    min-width:0;
    background:#0e1622;
    color:#b8c4d2;
    border:1px solid #223040;
    font-family:inherit;
    font-size:11px;
    padding:2px 4px;
    border-radius:2px;
    outline:none;
    cursor:pointer;
  }
  #ui .field select:hover {
    border-color:#34445a;
    color:#dce6f2;
  }
  #ui .field select:focus {
    border-color:#00e5ff;
  }

  #ui .row { display:flex; gap:6px; margin:4px 0; }
  #ui .row button { flex:1 1 auto; }

  #ui button {
    padding:5px 10px; cursor:pointer;
    border:1px solid #223040; background:transparent;
    color:#b8c4d2; border-radius:2px;
    font-family:inherit; font-size:10px;
    letter-spacing:0.10em; text-transform:uppercase;
    font-weight:700;
    transition: background 0.08s ease, border-color 0.08s ease;
  }
  #ui button:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  #ui button:active { background:rgba(220,230,242,0.07); }

  /* The "Generate .kra" button carries a red accent — the export
     action reads as destructive-tier in the palette, distinct from
     the neutral grey/cyan sliders and the amber patch lists.  The
     resting text is deliberately bright (a light rose) so the
     label stays legible at 10px uppercase; the faint text-shadow
     adds body to the glyphs without creating a glow. */
  #ui button#generateKraBtn {
    border-color: rgba(255, 105, 125, 0.80);
    color: #ffb0bc;
    text-shadow: 0 0 4px rgba(255, 80, 100, 0.45);
  }
  #ui button#generateKraBtn:hover {
    border-color: rgba(255, 130, 150, 1.00);
    color: #ffd0d8;
    background: rgba(220, 90, 110, 0.12);
    text-shadow: 0 0 6px rgba(255, 80, 100, 0.65);
  }
  #ui button#generateKraBtn:active {
    background: rgba(220, 90, 110, 0.20);
  }

  /* ---- Collapsible sections -----------------------------------
     Each group of fields lives in a <details> whose <summary> is
     styled like the old section headers.  A rotating caret shows
     open/closed state; the summary's top border doubles as the
     <hr> between sections, so no standalone rules remain. */
  #ui details.section {
    margin: 0;
  }
  #ui details.section > summary {
    list-style: none;
    cursor: pointer;
    user-select: none;
    padding: 6px 0 5px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #5a6774;
    display: flex;
    align-items: center;
    gap: 6px;
    border-top: 1px solid #1e2836;
    margin-top: 6px;
    transition: color 0.08s ease;
  }
  #ui details.section:first-of-type > summary {
    margin-top: 0;
    border-top: 0;
  }
  #ui details.section > summary::-webkit-details-marker {
    display: none;
  }
  #ui details.section > summary::before {
    content: "\25B8";
    display: inline-block;
    font-size: 8px;
    color: #3d4756;
    transition: transform 0.1s ease;
  }
  #ui details.section[open] > summary::before {
    transform: rotate(90deg);
  }
  #ui details.section > summary:hover {
    color: #a8b5c4;
  }
  #ui details.section[open] > summary {
    color: #00e5ff;
  }
  #ui details.section[open] > summary::before {
    color: #00e5ff;
  }
  #ui details.section > .section-body {
    padding-bottom: 4px;
  }

  .quadList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:110px; overflow-y:auto;
  }
  .quadList::-webkit-scrollbar { width:6px; }
  .quadList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .quadList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .quadRow {
    display:flex; align-items:center; gap:7px;
    padding:3px 6px;
    border:1px solid #223040;
    border-radius:2px;
    cursor:pointer;
    font-size:10px;
    color:#a8b5c4;
    letter-spacing:0.04em;
    flex:0 0 auto;
  }
  .quadRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .quadRow.selected {
    border-color:rgba(255,180,60,0.65);
    color:#ffc966;
    background:rgba(255,180,60,0.06);
  }
  .quadSwatch {
    width:9px; height:9px;
    background:rgba(255,200,90,0.55);
    border:1px solid rgba(255,200,90,0.85);
    border-radius:1px;
    flex:0 0 auto;
  }
  .quadRow.selected .quadSwatch {
    background:rgba(255,200,90,0.95);
    border-color:#ffc966;
  }
  .quadRow.squareRow.selected {
    border-color:rgba(120,220,255,0.65);
    color:#b8ecff;
    background:rgba(120,220,255,0.06);
  }
  .quadRow.squareRow .quadSwatch {
    background:rgba(120,220,255,0.55);
    border-color:rgba(140,225,255,0.85);
  }
  .quadRow.squareRow.selected .quadSwatch {
    background:rgba(180,240,255,0.95);
    border-color:#b8ecff;
  }
  .quadLabel {
    flex:1 1 auto; font-weight:700;
    font-variant-numeric: tabular-nums;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }

  .quadLabelInput {
    flex:1 1 auto;
    min-width:0;
    background:transparent;
    border:0;
    border-bottom:1px solid transparent;
    color:inherit;
    font-family:inherit;
    font-size:inherit;
    font-weight:700;
    letter-spacing:inherit;
    padding:0;
    margin:0;
    outline:none;
    overflow:hidden;
    text-overflow:ellipsis;
    transition: border-color 0.1s ease;
  }
  .quadLabelInput:hover {
    border-bottom-color:#223040;
  }
  .quadLabelInput:focus {
    border-bottom-color:#00e5ff;
  }

  .quadDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
  }
  .quadDel:hover {
    background:rgba(212,133,144,0.10) !important;
    border-color:#7a3e4a !important;
    color:#e8a0a8 !important;
  }

  #status {
    margin-top:6px; padding-top:6px;
    border-top:1px solid #1e2836;
    font-size:10px; color:#7b8794;
    min-height:0;
    font-variant-numeric: tabular-nums;
    overflow-wrap:anywhere; line-height:1.5;
  }
  #status.ok   { color:#00e5ff; }
  #status.bad  { color:#d48590; }
  #status.warn { color:#ffe600; }

  canvas { display:block; touch-action:none; background:#0a0e14; }

  #restoreBtn {
    position:absolute; top:12px; left:12px; z-index:10;
    display:none;
    padding:6px 12px; cursor:pointer;
    border:1px solid #1e2836; background:#0b1018;
    border-radius:3px; box-shadow:0 6px 28px rgba(0,0,0,0.65);
    font-family:inherit;
    font-size:10px; font-weight:700; letter-spacing:0.10em;
    text-transform:uppercase; color:#a8b5c4;
  }
  #restoreBtn:hover { border-color:#34445a; color:#dce6f2; }
  #restoreBtn.visible { display:block; }
</style>
</head>
<body>

<div id="ui">
  <h3>Cone interior
    <button id="helpBtn" title="Help (opens a separate window)"
      style="margin-left:auto; width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">?</button>
    <button id="collapseBtn" title="Collapse (H)"
      style="width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">−</button>
  </h3>

  <div class="panel-body">

    <details class="section" open>
      <summary>View</summary>
      <div class="section-body">
        <div class="field">
          <label>Depth</label>
          <input type="range" id="depthSlider"
                 min="3" max="40" step="0.5" value="15">
          <span class="val" id="depthVal">15.0</span>
        </div>
        <div class="field">
          <label>Half-angle</label>
          <input type="range" id="angleSlider"
                 min="10" max="60" step="1" value="30">
          <span class="val" id="angleVal">30°</span>
        </div>
        <div class="field">
          <label>Rings</label>
          <input type="range" id="ringsSlider"
                 min="4" max="40" step="1" value="14">
          <span class="val" id="ringsVal">14</span>
        </div>
        <div class="field">
          <label>Meridians</label>
          <input type="range" id="meridiansSlider"
                 min="4" max="48" step="1" value="24">
          <span class="val" id="meridiansVal">24</span>
        </div>
        <div class="field">
          <label>Shape depth</label>
          <input type="range" id="shapeDepthSlider"
                 min="0.05" max="2.00" step="0.05" value="1.00">
          <span class="val" id="shapeDepthVal">1.00</span>
        </div>
        <div class="row">
          <button id="resetBtn">Reset</button>
          <button id="centerBtn">Center apex</button>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Patches</summary>
      <div class="section-body">
        <div class="field">
          <label>Patch &phi;</label>
          <input type="text" id="patchPhiVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Patch s</label>
          <input type="text" id="patchSVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Mirror</label>
          <input type="checkbox" id="patchMirror"
                 title="Draw a visual clone of every square on this
                        patch on the diametrically opposite side of
                        the cone">
        </div>
        <div class="field">
          <label>Mirror &ang;</label>
          <input type="text" id="patchMirrorAngle" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Mirror flip</label>
          <input type="checkbox" id="patchMirrorFlip"
                 title="Negate the mirror's rotation so tilted shapes
                        are not 180°-rotated relative to the
                        original">
        </div>

        <div class="field" style="margin-top:6px; margin-bottom:2px;">
          <label>List</label>
          <button id="addQuadBtn" style="flex:1 1 auto;">+ Patch</button>
          <button id="cloneQuadBtn" style="flex:1 1 auto;">+ Clone</button>
        </div>
        <div id="quadList" class="quadList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Squares</summary>
      <div class="section-body">
        <div class="field">
          <label>Square &phi;</label>
          <input type="text" id="squarePhiVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Centre of the selected shape along the
                        unfolded cone's φ axis (radians, 0 to 2π).
                        The cone wraps, so a typed value outside
                        the range folds back in, and a value far
                        from the current one moves the shape the
                        short way around.">
        </div>
        <div class="field">
          <label>Square s</label>
          <input type="text" id="squareSVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Centre of the selected shape along the
                        unfolded cone's axial axis (0 at base,
                        1 at apex).">
        </div>
        <div class="field">
          <label>Square W</label>
          <input type="text" id="squareWVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Square H</label>
          <input type="text" id="squareHVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Slope &times;</label>
          <input type="text" id="squareSlopeVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Hinge amount for the selected shape, 0 to 1.">
          <input type="text" id="shapeStandingMultVal" class="scrubInput"
                 value="2.00" autocomplete="off" spellcheck="false"
                 title="Standing height multiplier — how tall a
                        hinged shape stands, as a multiple of its
                        flat-view height.  Global; applies to every
                        shape.">
        </div>
        <div class="field">
          <label>Visual bot.</label>
          <input type="checkbox" id="squareVisualBottom"
                 title="When checked, the shape's text anchor and
                        corner senses follow the visual bottom of
                        the scene — the side of the square that
                        sits lowest on screen — instead of the
                        shape's own longest edge.  Changes both the
                        label's baseline angle and which corner is
                        read as top-left, top-right, bottom-right,
                        bottom-left.">
        </div>

        <div class="field" style="margin-top:6px; margin-bottom:2px;">
          <label>List</label>
          <button id="addSquareBtn" style="flex:1 1 auto;">+ Square</button>
          <button id="cloneSquareBtn" style="flex:1 1 auto;">+ Clone</button>
        </div>
        <div id="squareList" class="quadList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Draw</summary>
      <div class="section-body">
        <div class="field">
          <label>Draw fit</label>
          <select id="drawFitMode">
            <option value="shape-first" selected>shape &rarr; patch</option>
            <option value="patch-first">patch &rarr; shape</option>
          </select>
        </div>
        <div class="field">
          <label>No rotate</label>
          <input type="checkbox" id="drawNoRotate"
                 title="Lock the fitted shape's rotation to zero, so
                        its edges run parallel to the flat view's
                        φ and s axes — the shape reads as
                        north/south aligned on the unfolded sheet.
                        Read at draw-finish time, so it can be
                        changed mid-draw.">
        </div>
        <div class="row" style="margin-top:-2px;">
          <button id="drawQuadBtn">Draw Quad</button>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Export</summary>
      <div class="section-body">
        <div class="row">
          <button id="saveSceneBtn">Save scene</button>
          <button id="loadSceneBtn">Load scene</button>
        </div>
        <div class="row">
          <button id="exportBtn">Export visual state</button>
          <button id="generateKraBtn">Generate .kra</button>
        </div>

        <div class="field" style="margin-top:6px;">
          <label>KRA text</label>
          <select id="kraTextPos">
            <option value="center">center</option>
            <option value="tl">top-left</option>
            <option value="tr">top-right</option>
            <option value="bl">bottom-left</option>
            <option value="br">bottom-right</option>
            <option value="top">top edge</option>
            <option value="bottom">bottom edge</option>
            <option value="left">left edge</option>
            <option value="right">right edge</option>
          </select>
        </div>
        <div class="field">
          <label>Padding</label>
          <input type="text" id="kraTextPad" class="scrubInput"
                 value="0.06" autocomplete="off" spellcheck="false">
        </div>
        <div class="field">
          <label>Text color</label>
          <select id="kraTextColor">
            <option value="color">patch hue</option>
            <option value="black">black</option>
          </select>
        </div>
        <div class="field">
          <label>Warp</label>
          <select id="kraTextWarp">
            <option value="square">per-square</option>
            <option value="patch">per-patch</option>
            <option value="text">per-text</option>
            <option value="text-shear">per-text shear</option>
            <option value="patch-shear">per-patch shear</option>
          </select>
        </div>
        <div class="field">
          <label>Rectangles</label>
          <input type="checkbox" id="kraDrawRects" checked
                 title="When checked, the KRA export writes one
                        filled rectangle per square alongside its
                        text, using the square's projected corners.
                        When unchecked, only the text layers are
                        written.">
        </div>
      </div>
    </details>

    <div id="status">drag apex to tilt · scroll to change depth</div>

  </div><!-- .panel-body -->
</div>

<button id="restoreBtn" title="Show panel (H)">☰ Show panel</button>

<canvas id="c"></canvas>
"""


HTML_TAIL = r"""</script>
</body>
</html>
"""


BOOT_JS = r"""
(function installShellControls() {
  const ui          = document.getElementById("ui");
  const restoreBtn  = document.getElementById("restoreBtn");
  const collapseBtn = document.getElementById("collapseBtn");

  const collapse = () => {
    ui.classList.add("collapsed");
    restoreBtn.classList.add("visible");
  };
  const restore = () => {
    ui.classList.remove("collapsed");
    restoreBtn.classList.remove("visible");
  };

  collapseBtn.addEventListener("click", collapse);
  restoreBtn.addEventListener("click", restore);

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    if (e.key === "h" || e.key === "H") {
      if (ui.classList.contains("collapsed")) restore();
      else                                     collapse();
    }
  });
})();

syncPanelSliders();
syncQuadList();
resize();
"""
