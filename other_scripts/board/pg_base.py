"""
pg_base.py — palette, stylesheet, HTML head, bootstrap.

The panel has two Krita-facing sync actions (Refresh, Retrieve open),
one disk-facing add action (+ Add files…), one Krita-open action (Open
all), one content-transfer action (Project overlaps), two layout
actions, one view reset, a card-width slider, two per-card scrub
inputs (Scale, Rotation), a file list, and a save/load row.

The Scale and Rotation fields follow the selected card: they grey out
when nothing is selected, and reflect the selected card's own values
otherwise.  Both support scrub-drag; Rotation snaps to 15° with
Shift held.
"""

HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Board — files on a canvas</title>
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
    width:320px; box-sizing:border-box;
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
    flex:0 0 88px;
    font-size:9px; color:#5a6774;
    letter-spacing:0.14em; text-transform:uppercase;
    font-weight:700;
  }
  #ui .field input[type=range] {
    flex:1 1 auto; min-width:0;
    accent-color:#00e5ff;
  }
  #ui .field .val {
    flex:0 0 44px;
    font-size:11px; color:#00e5ff;
    text-align:right; font-weight:700;
    font-variant-numeric: tabular-nums;
  }

  /* Scrub input: a text field the user drags horizontally to change.
     Clicking once focuses it for typing; dragging sideways scrubs. */
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
  #ui button:disabled {
    color:#3d4756;
    border-color:#1e2836;
    cursor:not-allowed;
  }

  #ui button#refreshBtn {
    border-color: rgba(0, 229, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#refreshBtn:hover {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(0, 229, 255, 0.10);
    color: #d8f4ff;
  }

  #ui button#retrieveBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#retrieveBtn:hover {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }

  #ui button#openAllBtn {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#openAllBtn:hover {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
  }

  #ui button#projectBtn {
    border-color: rgba(255, 130, 210, 0.72);
    color: #ffb0e6;
  }
  #ui button#projectBtn:hover {
    border-color: rgba(255, 130, 210, 1.00);
    background: rgba(255, 130, 210, 0.10);
    color: #ffd0f0;
  }

  #ui hr { border:0; border-top:1px solid #1e2836; margin:10px 0; }

  .fileList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:220px; overflow-y:auto;
  }
  .fileList::-webkit-scrollbar { width:6px; }
  .fileList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .fileList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }

  .fileRow {
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
  .fileRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .fileRow.selected {
    border-color:rgba(0, 229, 255, 0.72);
    color:#b8ecff;
    background:rgba(0, 229, 255, 0.06);
  }
  .fileRow.closed  { opacity: 0.65; }
  .fileRow.missing {
    border-color: rgba(212, 133, 144, 0.55);
    color: #d48590;
    opacity: 0.75;
  }
  .fileRow.missing:hover {
    border-color: rgba(232, 160, 168, 0.85);
    color: #e8a0a8;
  }

  .fileSwatch {
    width:9px; height:9px;
    border-radius:1px;
    flex:0 0 auto;
  }
  .fileLabel {
    flex:1 1 auto; font-weight:700;
    font-variant-numeric: tabular-nums;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .fileState {
    flex:0 0 auto;
    font-size:11px; line-height:1;
    letter-spacing:0;
  }
  .fileState.open    { color:#00e5ff; }
  .fileState.closed  { color:#5a6774; }
  .fileState.missing { color:#d48590; }

  .fileAct {
    width:auto !important;
    padding:1px 7px !important;
    font-size:10px !important;
    line-height:1.3 !important;
    letter-spacing:0.06em !important;
  }
  .fileAct.open {
    border-color: rgba(255, 200, 90, 0.55) !important;
    color: #ffd89b !important;
  }
  .fileAct.open:hover {
    background: rgba(255, 200, 90, 0.10) !important;
    border-color: rgba(255, 200, 90, 0.95) !important;
    color: #ffe6b8 !important;
  }
  .fileDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
  }
  .fileDel:hover {
    background:rgba(212,133,144,0.10) !important;
    border-color:#7a3e4a !important;
    color:#e8a0a8 !important;
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
  <h3>Board
    <button id="collapseBtn" title="Collapse (H)"
      style="margin-left:auto; width:auto; padding:0 6px;
             height:20px; font-size:11px; line-height:1;">−</button>
  </h3>

  <div class="row">
    <button id="refreshBtn" title="Ask Krita which of the board's files are open (R)">
      Refresh from Krita
    </button>
  </div>

  <div class="row">
    <button id="addFilesBtn" title="Pick .kra (or other image) files to put on the board">
      + Add files…
    </button>
    <button id="retrieveBtn" title="Add every document currently open in Krita to the board">
      Retrieve open
    </button>
  </div>

  <div class="row">
    <button id="openAllBtn" title="Open every closed file in Krita (O)">
      Open all
    </button>
  </div>

  <div class="row">
    <button id="projectBtn" title="Copy the selected card's overlapping region into every overlapped Krita document as a new layer (P)">
      Project overlaps
    </button>
  </div>

  <div class="row">
    <button id="gridBtn"  title="Rearrange cards into a grid (G)">Arrange grid</button>
    <button id="fitBtn"   title="Fit every card in view (F)">Fit</button>
  </div>

  <div class="row">
    <button id="resetBtn" title="Reset zoom and pan to the origin">Reset view</button>
  </div>

  <hr>

  <div class="field">
    <label>Card width</label>
    <input type="range" id="cardWSlider"
           min="120" max="720" step="10" value="320"
           title="Reference width for the board.  Existing cards are
                  rescaled proportionally, so their relative sizes
                  are preserved.">
    <span class="val" id="cardWVal">320</span>
  </div>

  <div class="field">
    <label>Scale</label>
    <input type="text" id="cardScaleVal" class="scrubInput"
           value="—" autocomplete="off" spellcheck="false" disabled
           title="Selected card's scale relative to the reference width.
                  Drag sideways to scrub; hold Shift to snap.">
  </div>

  <div class="field">
    <label>Rotation</label>
    <input type="text" id="cardRotVal" class="scrubInput"
           value="—" autocomplete="off" spellcheck="false" disabled
           title="Selected card's rotation in degrees.  Drag sideways
                  to scrub; hold Shift to snap to 15°.">
  </div>

  <hr>

  <div class="field" style="margin-bottom:2px;">
    <label>Files</label>
    <button id="clearBtn"
            title="Empty the board.  Does not touch Krita."
            style="flex:0 0 auto;">Clear</button>
  </div>
  <div id="fileList" class="fileList"></div>

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
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
    if (e.key === "h" || e.key === "H") {
      if (ui.classList.contains("collapsed")) restore();
      else                                     collapse();
    }
  });
})();

syncPanelSliders();
syncFileList();
resize();

refreshFromKrita();
"""
