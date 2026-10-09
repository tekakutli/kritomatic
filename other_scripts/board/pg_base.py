"""
pg_base.py — palette, stylesheet, HTML head, bootstrap.

The panel carries a header with a help button and a collapse button,
three buttons (Refresh, Arrange, Fit), a save/load row, a card-width
slider, and a status line.  Every control carries a `title` attribute
so hovering shows what it does.

The panel's design is a direct descendant of the cone playground's:
same dark palette, same monospace face, same cyan accent, same
collapse-to-a-restore-button behaviour, same status line that grows
a warning colour when something goes wrong.
"""

HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Board — every open Krita document</title>
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
    width:300px; box-sizing:border-box;
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

  #ui button#refreshBtn {
    border-color: rgba(0, 229, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#refreshBtn:hover {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(0, 229, 255, 0.10);
    color: #d8f4ff;
  }

  #ui hr { border:0; border-top:1px solid #1e2836; margin:10px 0; }

  .docList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:200px; overflow-y:auto;
  }
  .docList::-webkit-scrollbar { width:6px; }
  .docList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .docList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .docRow {
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
  .docRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .docRow.selected {
    border-color:rgba(0, 229, 255, 0.72);
    color:#b8ecff;
    background:rgba(0, 229, 255, 0.06);
  }
  .docRow.active {
    box-shadow: inset 0 0 0 1px rgba(255, 200, 90, 0.35);
  }
  .docSwatch {
    width:9px; height:9px;
    border-radius:1px;
    flex:0 0 auto;
  }
  .docLabel {
    flex:1 1 auto; font-weight:700;
    font-variant-numeric: tabular-nums;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .docDot {
    width:6px; height:6px; border-radius:50%;
    background:#ffc966;
    flex:0 0 auto;
  }
  .docDot.hidden { display:none; }
  .docDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
  }
  .docDel:hover {
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
    <button id="refreshBtn" title="Pull every open document from Krita (R)">
      Refresh from Krita
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
           title="Board-space width of every card.  Heights follow
                  each document's own aspect ratio.">
    <span class="val" id="cardWVal">320</span>
  </div>

  <hr>

  <div class="field" style="margin-bottom:2px;">
    <label>Documents</label>
    <button id="clearBtn"
            title="Empty the board.  Does not close the documents in Krita."
            style="flex:0 0 auto;">Clear</button>
  </div>
  <div id="docList" class="docList"></div>

  <hr>

  <div class="row">
    <button id="saveSceneBtn">Save board</button>
    <button id="loadSceneBtn">Load board</button>
  </div>

  <div id="status">connecting to Krita…</div>
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
syncDocList();
resize();

/* First pull.  If the daemon is reachable the board populates itself;
   if not, the status line says so and the user can press R later. */
refreshFromKrita();
"""
