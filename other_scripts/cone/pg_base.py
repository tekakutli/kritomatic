"""
pg_base.py — palette, stylesheet, HTML head, bootstrap.

The panel carries five graphics sliders, five scrub-inputs (the
selected patch's φ and s, the selected square's width, height, and
slope), ten buttons (Reset, Center apex, Save scene, Load scene,
Export visual state, Generate .kra, + Patch, + Clone, + Square,
+ Clone), a KRA-export options block (text position, text padding,
text warp mode, rectangle on/off, text color), two lists, a
persistent hint block.
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
  }
  #ui.collapsed { display:none; }

  #ui h3 {
    display:flex; align-items:center; gap:6px;
    margin:0 0 10px; padding:0 0 8px;
    border-bottom:1px solid #1e2836;
    font-size:11px; font-weight:700;
    letter-spacing:0.16em; text-transform:uppercase;
    color:#dce6f2;
  }

  #ui .field { display:flex; align-items:center; gap:8px; margin:7px 0; }
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

  #ui .row { display:flex; gap:6px; margin:6px 0; }
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

  #ui hr { border:0; border-top:1px solid #1e2836; margin:10px 0; }

  .quadList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:160px; overflow-y:auto;
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

  .hintLine {
    font-size:9px; color:#5a6774;
    letter-spacing:0.06em;
    padding:2px 0;
    line-height:1.5;
  }
  .hintLine .kbd {
    display:inline-block;
    padding:0 4px;
    border:1px solid #223040;
    border-radius:2px;
    background:#0e1622;
    color:#00e5ff;
    font-family:inherit;
    font-weight:700;
    letter-spacing:0.02em;
  }

  #status {
    margin-top:10px; padding-top:8px;
    border-top:1px solid #1e2836;
    font-size:10px; color:#7b8794;
    min-height:14px;
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
    <button id="collapseBtn" title="Collapse (H)"
      style="margin-left:auto; width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">−</button>
  </h3>

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

  <hr>

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

  <hr>

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
    <label>Slope</label>
    <input type="text" id="squareSlopeVal" class="scrubInput"
           value="&#8212;" autocomplete="off" spellcheck="false"
           disabled>
  </div>

  <hr>

  <div class="field" style="margin-bottom:2px;">
    <label>Patches</label>
    <button id="addQuadBtn" style="flex:1 1 auto;">+ Patch</button>
    <button id="cloneQuadBtn" style="flex:1 1 auto;">+ Clone</button>
  </div>
  <div id="quadList" class="quadList"></div>

  <div class="field" style="margin-top:8px; margin-bottom:2px;">
    <label>Squares</label>
    <button id="addSquareBtn" style="flex:1 1 auto;">+ Square</button>
    <button id="cloneSquareBtn" style="flex:1 1 auto;">+ Clone</button>
  </div>
  <div id="squareList" class="quadList"></div>

  <div class="hintLine">
    shift+click a patch to drop one there
  </div>
  <div class="hintLine">
    <span class="kbd">Shift</span>+<span class="kbd">A</span> align to 45°
  </div>
  <div class="hintLine">
    hold <span class="kbd">Shift</span> while dragging the rotate handle
    to jump
  </div>
  <div class="hintLine">
    hold <span class="kbd">Shift</span> while dragging a patch or a
    square to slide along the centre&rarr;apex axis only
  </div>
  <div class="hintLine">
    hold <span class="kbd">Alt</span> while clicking a square to grab
    its patch
  </div>
  <div class="hintLine">
    drag patch &phi;/s, square W/H, or slope to scrub &middot;
    <span class="kbd">Shift</span> to snap
  </div>
  <div class="hintLine">
    <span class="kbd">Generate .kra</span> writes one vector-text layer
    per square, laid out in the flat view
  </div>

  <hr>

  <div class="row">
    <button id="saveSceneBtn">Save scene</button>
    <button id="loadSceneBtn">Load scene</button>
  </div>
  <div class="row">
    <button id="resetBtn">Reset</button>
    <button id="centerBtn">Center apex</button>
  </div>
  <div class="row">
    <button id="exportBtn">Export visual state</button>
    <button id="generateKraBtn">Generate .kra</button>
  </div>

  <hr>

  <div class="field" style="margin-bottom:2px;">
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
    </select>
  </div>
  <div class="field">
    <label>Rectangles</label>
    <input type="checkbox" id="kraDrawRects" checked>
  </div>

  <div id="status">drag apex to tilt · scroll to change depth</div>
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
