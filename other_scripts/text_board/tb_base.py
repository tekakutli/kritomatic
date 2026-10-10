"""
tb_base.py — palette, stylesheet, HTML head, bootstrap.

The panel has one Krita-facing sync action (Refresh from Krita), two
file-loading actions (+ Add files…, Open all), two layout actions
(Arrange grid, Fit), one view reset, one grouping action, a
reference-width slider, a word-box toggle, an item list, and a
selected-item editor: text, font, size, color, alignment, rotation,
and an Apply button.
"""

HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Text board — every text shape on a canvas</title>
<style>
  html, body {
    margin:0; padding:0; height:100%; overflow:hidden;
    background:#0a0e14; color:#dce6f2;
    font-family:'JetBrains Mono','Fira Code','Consolas','SF Mono',monospace;
  }
  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    background:#0b1018; padding:12px 14px;
    border:1px solid #1e2836; border-radius:3px;
    box-shadow:0 6px 28px rgba(0,0,0,0.65);
    font-size:11px; color:#a8b5c4;
    user-select:none;
    width:340px; box-sizing:border-box;
    max-height:calc(100vh - 24px); overflow-y:auto;
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
    flex:0 0 74px;
    font-size:9px; color:#5a6774;
    letter-spacing:0.14em; text-transform:uppercase;
    font-weight:700;
  }
  #ui .field input[type=range] {
    flex:1 1 auto; min-width:0; accent-color:#00e5ff;
  }
  #ui .field input[type=text],
  #ui .field input[type=number],
  #ui .field select {
    flex:1 1 auto; min-width:0;
    background:transparent; border:0;
    border-bottom:1px solid #223040;
    color:#00e5ff; font-family:inherit; font-size:12px;
    font-weight:700; padding:2px 4px; margin:0;
    outline:none;
  }
  #ui .field input[type=text]:focus,
  #ui .field select:focus { border-bottom-color:#00e5ff; }
  #ui .field input:disabled,
  #ui .field select:disabled {
    color:#3d4756; border-bottom-color:#1e2836;
    cursor:not-allowed;
  }
  #ui textarea {
    width:100%; box-sizing:border-box;
    background:#0d1219; color:#00e5ff;
    border:1px solid #223040; border-radius:2px;
    font-family:inherit; font-size:12px;
    padding:4px 6px; margin:2px 0 6px; outline:none;
    resize:vertical; min-height:44px;
  }
  #ui textarea:focus { border-color:#00e5ff; }
  #ui textarea:disabled { color:#3d4756; }
  #ui .row { display:flex; gap:6px; margin:6px 0; }
  #ui .row button { flex:1 1 auto; }
  #ui button {
    padding:5px 10px; cursor:pointer;
    border:1px solid #223040; background:transparent;
    color:#b8c4d2; border-radius:2px;
    font-family:inherit; font-size:10px;
    letter-spacing:0.10em; text-transform:uppercase;
    font-weight:700;
    transition:background 0.08s ease, border-color 0.08s ease;
  }
  #ui button:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  #ui button:disabled { color:#3d4756; border-color:#1e2836;
    cursor:not-allowed; }
  #ui button#refreshBtn {
    border-color:rgba(0,229,255,0.72); color:#b8ecff;
  }
  #ui button#refreshBtn:hover {
    border-color:rgba(140,225,255,1.0);
    background:rgba(0,229,255,0.10); color:#d8f4ff;
  }
  #ui button#addFilesBtn {
    border-color:rgba(120,220,255,0.72); color:#b8ecff;
  }
  #ui button#addFilesBtn:hover {
    border-color:rgba(140,225,255,1.00);
    background:rgba(120,220,255,0.10); color:#d8f4ff;
  }
  #ui button#openAllBtn {
    border-color:rgba(255,200,90,0.72); color:#ffd89b;
  }
  #ui button#openAllBtn:hover {
    border-color:rgba(255,200,90,1.00);
    background:rgba(255,200,90,0.10); color:#ffe6b8;
  }
  #ui button#applyBtn {
    border-color:rgba(140,225,160,0.72); color:#b8f0c4;
  }
  #ui button#applyBtn:hover {
    border-color:rgba(140,225,160,1.0);
    background:rgba(140,225,160,0.10); color:#d8ffe0;
  }
  #ui hr { border:0; border-top:1px solid #1e2836; margin:10px 0; }
  #ui .val {
    flex:0 0 44px; font-size:11px; color:#00e5ff;
    text-align:right; font-weight:700;
    font-variant-numeric:tabular-nums;
  }
  #status {
    margin-top:10px; padding-top:8px;
    border-top:1px solid #1e2836;
    font-size:10px; color:#7b8794;
    min-height:14px;
    font-variant-numeric:tabular-nums;
    overflow-wrap:anywhere; line-height:1.5;
  }
  #status.ok   { color:#00e5ff; }
  #status.bad  { color:#d48590; }
  #status.warn { color:#ffe600; }

  .itemList {
    display:flex; flex-direction:column; gap:3px;
    margin:6px 0 0; max-height:180px; overflow-y:auto;
  }
  .itemList::-webkit-scrollbar { width:6px; }
  .itemList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .itemList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .itemRow {
    display:flex; align-items:center; gap:7px;
    padding:3px 6px; border:1px solid #223040;
    border-radius:2px; cursor:pointer;
    font-size:10px; color:#a8b5c4;
    letter-spacing:0.02em;
  }
  .itemRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .itemRow.selected {
    border-color:rgba(0,229,255,0.72);
    color:#b8ecff; background:rgba(0,229,255,0.06);
  }
  .itemSwatch {
    width:9px; height:9px; border-radius:1px; flex:0 0 auto;
  }
  .itemText {
    flex:1 1 auto; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap;
  }
  .itemMeta {
    flex:0 0 auto; font-size:9px; color:#5a6774;
  }

  canvas { display:block; touch-action:none; background:#0a0e14; }

  #restoreBtn {
    position:absolute; top:12px; left:12px; z-index:10;
    display:none; padding:6px 12px; cursor:pointer;
    border:1px solid #1e2836; background:#0b1018;
    border-radius:3px; box-shadow:0 6px 28px rgba(0,0,0,0.65);
    font-family:inherit; font-size:10px; font-weight:700;
    letter-spacing:0.10em; text-transform:uppercase;
    color:#a8b5c4;
  }
  #restoreBtn:hover { border-color:#34445a; color:#dce6f2; }
  #restoreBtn.visible { display:block; }
</style>
</head>
<body>

<div id="ui">
  <h3>Text board
    <button id="collapseBtn" title="Collapse (H)"
      style="margin-left:auto; width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">−</button>
  </h3>

  <div class="row">
    <button id="refreshBtn" title="Ask the daemon for every text shape (R)">
      Refresh from Krita
    </button>
  </div>

  <div class="row">
    <button id="addFilesBtn"
            title="Pick .kra files to add to the board and open in Krita">
      + Add files…
    </button>
  </div>

  <div class="row">
    <button id="openAllBtn"
            title="Open every picked file that Krita does not already have open">
      Open all
    </button>
  </div>

  <div class="row">
    <button id="gridBtn" title="Rearrange cards into a grid (G)">Arrange grid</button>
    <button id="fitBtn"  title="Fit every card in view (F)">Fit</button>
  </div>

  <div class="row">
    <button id="resetBtn" title="Reset zoom and pan">Reset view</button>
    <button id="groupBtn" title="Recolour and regroup cards by their formatting signature">
      Group by format
    </button>
  </div>

  <hr>

  <div class="field">
    <label>Card width</label>
    <input type="range" id="cardWSlider"
           min="120" max="720" step="10" value="320">
    <span class="val" id="cardWVal">320</span>
  </div>

  <div class="field">
    <label>Word boxes</label>
    <input type="checkbox" id="wordBoxToggle" checked>
  </div>

  <hr>

  <div class="field">
    <label>Selected</label>
    <span id="selLabel" style="font-size:10px; color:#7b8794;">—</span>
  </div>

  <textarea id="editText" disabled placeholder="select a card"
            spellcheck="false"></textarea>

  <div class="field">
    <label>Font</label>
    <input type="text" id="editFont" disabled autocomplete="off"
           spellcheck="false">
    <label style="flex:0 0 42px;">Size</label>
    <input type="text" id="editSize" disabled autocomplete="off"
           spellcheck="false">
  </div>

  <div class="field">
    <label>Color</label>
    <input type="text" id="editColor" disabled autocomplete="off"
           spellcheck="false">
    <label style="flex:0 0 42px;">Align</label>
    <select id="editAlign" disabled>
      <option value="left">left</option>
      <option value="center">center</option>
      <option value="right">right</option>
    </select>
  </div>

  <div class="field">
    <label>Rot°</label>
    <input type="text" id="editRot" disabled autocomplete="off"
           spellcheck="false">
    <span style="font-size:9px; color:#3d4756;">+CW</span>
  </div>

  <div class="row">
    <button id="applyBtn" disabled>Apply changes</button>
  </div>

  <hr>

  <div class="field" style="margin-bottom:2px;">
    <label>Items</label>
    <span id="itemCount" style="font-size:10px; color:#5a6774;">0</span>
    <button id="clearBtn"
            style="flex:0 0 auto;"
            title="Empty the board (does not touch Krita).">Clear</button>
  </div>
  <div id="itemList" class="itemList"></div>

  <hr>

  <div class="row">
    <button id="saveSceneBtn">Save board</button>
    <button id="loadSceneBtn">Load board</button>
  </div>

  <div id="status">ready</div>
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
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
              t.tagName === "SELECT")) return;
    if (e.key === "h" || e.key === "H") {
      if (ui.classList.contains("collapsed")) restore();
      else                                     collapse();
    }
  });
})();

syncPanelSliders();
syncItemList();
_syncEditor();
resize();

refreshFromKrita();
"""
