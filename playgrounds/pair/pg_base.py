"""
pg_base.py — palette, stylesheet, HTML head, bootstrap.

The panel carries six scrub inputs for the selected pair
(inclination, metric A, metric B, offset, gap, loop gap), a pair
list, a "+ Pair" / "+ Clone" button pair, a Segments section with
an "Overlap" toggle, a "Draw Segment" toggle, a "Toggle Fill"
button, a "Clear Segments" button and a segment list, an Export
section, a KRA section, and a help popup.

DIRECTION: ONE FIELD, NOT TWO
=============================
A pair of parallel lines is symmetric under a 180° reversal, so
only one direction field survives: `Inclination°`, the pair's tilt
against the horizontal axis in [-90, +90].  The stored value
`p.angle` remains the full-circle direction in radians.

LOOPBACK GAP
============
The "Loop gap" field is a per-pair abstract distance appended to
the pair's axis period.  At 0 the historic behaviour applies: a
segment dragged past one edge of the visible window immediately
has its wrapped copy at the other edge.  At larger values, the
copies are spaced further apart, so the segment has to travel the
extra distance before its wrapped copy emerges — a stretch where
neither the segment nor its copy is on screen.  See the module
docstring of pg_core.py, LOOPBACK GAP.

SHIFT-DRAG SET
==============
While the user holds Shift, successive shift-clicks on segment
bodies accumulate into a persistent set; a shift-drag on any
member then moves the whole set together.  Members get a bright
white dashed outline in the segment view and a soft cyan inset
outline in the panel's segment list.  The set survives Shift
release and is cleared only on a non-shift canvas click, on
segment deletion, or on scene load.  See pg_dispatch.py,
SHIFT-DRAG SET, for the full semantics.

OVERLAP MODE
============
A global viewing aid, toggled by the "Overlap" checkbox.  When
ON, any segment whose trapezoid visually overlaps a trapezoid on
another pair gets a red dashed outline on the canvas and in the
segment view, and its rows in the panel's lists are marked.  Two
segments on the SAME pair are never compared.  See pg_core.py,
`_segmentOverlapsOtherPair`.

OVERLAP RESOLUTION — "MAKE ROOM"
================================
While Overlap mode is on, a segment row may carry a small amber
`⇣` "make room" button when it overlaps a segment on the pair
immediately below it.  Clicking it shifts the offending segment
on the lower pair — plus every segment that follows it in the
same direction — by the smallest amount that clears the overlap.

PAIR ORDER
==========
The pair list carries ↑ / ↓ arrows that swap a pair with its
neighbour; the same move is on Alt+↑ / Alt+↓.  Reordering the list
also reorders the segment-view rows and the canvas's paint order.

KRA EXPORT
==========
The KRA section carries the options for the .kra export; each
segment gets a text label warped by an affine derived from the
trapezoid's own local frame.  See pg_kra.py.
"""

HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pairs — viewer</title>
<style>
  html, body {
    margin:0; padding:0; height:100%; overflow:hidden;
    background:#0a0e14; color:#dce6f2;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas',
                 'SF Mono', monospace;
  }

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
  #ui .field input[type=checkbox] {
    flex:0 0 auto;
    margin-left:auto;
    accent-color:#00e5ff;
    cursor:pointer;
    width:14px; height:14px;
  }
  #ui .field input#overlapHighlight {
    accent-color:#ff5a69;
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

  #ui button.active {
    border-color: #00e5ff;
    color: #00e5ff;
    background: rgba(0,229,255,0.06);
  }

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

  #ui details.section { margin: 0; }
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
  #ui details.section > summary::-webkit-details-marker { display: none; }
  #ui details.section > summary::before {
    content: "\25B8";
    display: inline-block;
    font-size: 8px;
    color: #3d4756;
    transition: transform 0.1s ease;
  }
  #ui details.section[open] > summary::before { transform: rotate(90deg); }
  #ui details.section > summary:hover { color: #a8b5c4; }
  #ui details.section[open] > summary { color: #00e5ff; }
  #ui details.section[open] > summary::before { color: #00e5ff; }
  #ui details.section > .section-body { padding-bottom: 4px; }

  .quadList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:140px; overflow-y:auto;
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
    display:flex; align-items:center; gap:5px;
    padding:3px 6px;
    border:1px solid #223040;
    border-radius:2px;
    cursor:pointer;
    font-size:10px;
    color:#a8b5c4;
    letter-spacing:0.04em;
    flex: 0 0 auto;
  }
  .quadRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .quadRow.selected {
    border-color:rgba(0,229,255,0.65);
    color:#b8ecff;
    background:rgba(0,229,255,0.06);
  }
  .quadRow.overlapRow {
    border-color: rgba(255, 90, 105, 0.55);
  }
  .quadRow.overlapRow.selected {
    border-color: rgba(255, 90, 105, 0.95);
  }
  .quadRow.overlapRow .quadSwatch {
    box-shadow: 0 0 0 2px rgba(255, 90, 105, 0.55);
  }
  .quadRow.shiftMember {
    box-shadow: inset 0 0 0 1px rgba(0, 229, 255, 0.55);
  }
  .quadRow.shiftMember.selected {
    box-shadow: inset 0 0 0 1px rgba(0, 229, 255, 0.90);
  }
  .quadSwatch {
    width:9px; height:9px;
    border-radius:1px;
    flex:0 0 auto;
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
  .quadLabelInput:hover { border-bottom-color:#223040; }
  .quadLabelInput:focus { border-bottom-color:#00e5ff; }

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

  .quadMv {
    width:18px !important;
    min-width:18px !important;
    padding:1px 0 !important;
    font-size:11px !important;
    line-height:1 !important;
    border-color:#223040 !important;
    color:#a8b5c4 !important;
    letter-spacing:0 !important;
  }
  .quadMv:hover:not(:disabled) {
    background:rgba(220,230,242,0.06) !important;
    border-color:#34445a !important;
    color:#dce6f2 !important;
  }
  .quadMv:disabled {
    opacity:0.30 !important;
    cursor:not-allowed !important;
  }

  .quadRoom {
    width:auto !important;
    padding:1px 6px !important;
    font-size:11px !important;
    line-height:1 !important;
    border-color: rgba(255, 200, 90, 0.60) !important;
    color: #ffc966 !important;
    letter-spacing:0 !important;
  }
  .quadRoom:hover {
    background: rgba(255, 200, 90, 0.12) !important;
    border-color: rgba(255, 200, 90, 1.00) !important;
    color: #ffe0a0 !important;
  }
  .quadRoom:active {
    background: rgba(255, 200, 90, 0.20) !important;
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
  <h3>Pairs
    <button id="helpBtn" title="Help"
      style="margin-left:auto; width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">?</button>
    <button id="collapseBtn" title="Collapse (H)"
      style="width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">−</button>
  </h3>

  <div class="panel-body">

    <details class="section" open>
      <summary>Pair</summary>
      <div class="section-body">
        <div class="field">
          <label>Incl.°</label>
          <input type="text" id="pairInclinationVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Inclination of the pair against the
                        horizontal axis, degrees, in [-90, +90].">
        </div>
        <div class="field">
          <label>Metric A</label>
          <input type="text" id="pairMetricAVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="World units per abstract unit along line A.">
        </div>
        <div class="field">
          <label>Metric B</label>
          <input type="text" id="pairMetricBVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="World units per abstract unit along line B.">
        </div>
        <div class="field">
          <label>Offset</label>
          <input type="text" id="pairOffsetVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Shift of line B along the direction of line A,
                        in world units.">
        </div>
        <div class="field">
          <label>Gap</label>
          <input type="text" id="pairGapVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Perpendicular distance between the two lines,
                        in world units.">
        </div>
        <div class="field">
          <label>Loop gap</label>
          <input type="text" id="pairLoopbackVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Extra abstract distance appended to the
                        pair's axis period.  0 reproduces the
                        historic behaviour, where a segment dragged
                        past one edge has its wrapped copy appear
                        immediately at the other edge.  A larger
                        value inserts a stretch where neither the
                        segment nor its copy is on screen.">
        </div>
        <div class="row">
          <button id="addPairBtn">+ Pair</button>
          <button id="clonePairBtn">+ Clone</button>
        </div>
        <div id="pairList" class="quadList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Segments</summary>
      <div class="section-body">
        <div class="field">
          <label>Overlap</label>
          <input type="checkbox" id="overlapHighlight"
                 title="Highlight segments whose trapezoids visually
                        overlap a trapezoid on another pair, on the
                        canvas.  When on, such segments get a red
                        dashed outline on the canvas and in the
                        segment view, their rows in the pair and
                        segment lists are marked, and a 'make room'
                        button appears on any row that overlaps a
                        segment on the pair immediately below.  The
                        test is a screen-space polygon test, so it
                        depends on the current zoom and pan.">
        </div>
        <div class="row">
          <button id="drawSegmentBtn">Draw Segment</button>
        </div>
        <div class="row">
          <button id="toggleFillBtn">Toggle Fill</button>
          <button id="clearSegmentsBtn">Clear Segments</button>
        </div>
        <div id="segmentList" class="quadList"></div>
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
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>KRA</summary>
      <div class="section-body">
        <div class="field">
          <label>Position</label>
          <select id="kraTextPos">
            <option value="center">center</option>
            <option value="tl">top-left</option>
            <option value="tc">top-center</option>
            <option value="tr">top-right</option>
            <option value="ml">mid-left</option>
            <option value="mr">mid-right</option>
            <option value="bl">bottom-left</option>
            <option value="bc">bottom-center</option>
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
            <option value="color">pair hue (light)</option>
            <option value="black">black</option>
          </select>
        </div>
        <div class="field">
          <label>Rectangles</label>
          <input type="checkbox" id="kraDrawRects" checked
                 title="When checked, each filled segment also emits a
                        filled polygon matching its trapezoid.  Empty
                        segments never emit a rect.">
        </div>
        <div class="row">
          <button id="generateKraBtn">Generate .kra</button>
        </div>
      </div>
    </details>

    <div id="status">drag pairs &middot; drag segment edges below</div>

  </div>
</div>

<button id="restoreBtn" title="Show panel (H)">&#9776; Show panel</button>

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

syncPairList();
resize();
"""
