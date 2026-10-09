"""
pg_panel.py — panel bindings.

Buttons
    Refresh from Krita    pull every open document and its thumbnail
    Arrange grid          re-place every card in a grid
    Fit                   fit every card in the viewport
    Reset view            zoom = 1, pan = centre
    Clear                 empty the board (does not touch Krita)
    Save board            download the board layout as JSON
    Load board            pick a JSON file and apply it

Slider
    Card width            board-space width of every card; heights
                          follow each doc's own aspect ratio.  Moving
                          the slider live-resizes every card and
                          re-renders.

List
    One row per document.  Colour swatch, name, modified dot, × to
    remove from the board.  Click a row to select; the row follows
    the board selection both ways.
"""

PANEL_JS = r"""
/* ==========================================================================
   SLIDER
   ========================================================================== */

function syncPanelSliders() {
  const s = document.getElementById("cardWSlider");
  if (s) s.value = board.defaultWidth;
  updatePanelLabels();
}

function updatePanelLabels() {
  const v = document.getElementById("cardWVal");
  if (v) v.textContent = String(board.defaultWidth);
}

/* ==========================================================================
   DOC LIST
   ========================================================================== */

function syncDocList() {
  const list = document.getElementById("docList");
  if (!list) return;
  list.innerHTML = "";

  if (board.docs.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no documents yet — press Refresh";
    list.appendChild(e);
    return;
  }

  for (let i = 0; i < board.docs.length; i++) {
    const d = board.docs[i];
    const isSel = (i === board.selectedIdx);

    const row = document.createElement("div");
    row.className = "docRow" + (isSel ? " selected" : "") + (d.active ? " active" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "docSwatch";
    const hue = d.color || board.palette[0];
    sw.style.background  = _rgba(hue, isSel ? 0.95 : 0.55);
    sw.style.border      = "1px solid " + _rgba(hue, isSel ? 1.0 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("span");
    lb.className = "docLabel";
    lb.textContent = d.name;
    row.appendChild(lb);

    const dot = document.createElement("span");
    dot.className = "docDot" + (d.modified ? "" : " hidden");
    dot.title = "unsaved changes in Krita";
    row.appendChild(dot);

    const del = document.createElement("button");
    del.className = "docDel";
    del.textContent = "×";
    del.title = "Remove from the board (does not close it in Krita)";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      removeDoc(i);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      board.selectedIdx = i;
      syncDocList();
      draw();
    });

    list.appendChild(row);
  }
}

/* ==========================================================================
   DOC OPERATIONS
   ========================================================================== */

function removeDoc(idx) {
  if (idx < 0 || idx >= board.docs.length) return;
  board.docs.splice(idx, 1);
  if (board.selectedIdx === idx) board.selectedIdx = -1;
  else if (board.selectedIdx > idx) board.selectedIdx -= 1;
  syncDocList();
  draw();
}

/* ==========================================================================
   STATUS FLASH
   ========================================================================== */

let _statusTimer = null;
function flashStatus(msg, cls) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.className = cls || "";
  if (_statusTimer) clearTimeout(_statusTimer);
  _statusTimer = setTimeout(() => {
    _statusTimer = null;
    updateStatus();
  }, 1800);
}

/* ==========================================================================
   BINDINGS
   ========================================================================== */

(function installPanel() {
  const refreshBtn = document.getElementById("refreshBtn");
  const gridBtn    = document.getElementById("gridBtn");
  const fitBtn     = document.getElementById("fitBtn");
  const resetBtn   = document.getElementById("resetBtn");
  const clearBtn   = document.getElementById("clearBtn");
  const saveBtn    = document.getElementById("saveSceneBtn");
  const loadBtn    = document.getElementById("loadSceneBtn");
  const slider     = document.getElementById("cardWSlider");

  if (refreshBtn) refreshBtn.addEventListener("click", refreshFromKrita);

  if (gridBtn) gridBtn.addEventListener("click", () => {
    arrangeGrid();
    draw();
    flashStatus("arranged " + board.docs.length + " cards", "ok");
  });

  if (fitBtn) fitBtn.addEventListener("click", fitAll);

  if (resetBtn) resetBtn.addEventListener("click", () => {
    view.zoom = 1;
    view.panX = window.innerWidth  / 2;
    view.panY = window.innerHeight / 2;
    draw();
  });

  if (clearBtn) clearBtn.addEventListener("click", () => {
    if (board.docs.length === 0) return;
    board.docs = [];
    board.selectedIdx = -1;
    syncDocList();
    draw();
    flashStatus("board cleared", "ok");
  });

  if (saveBtn) saveBtn.addEventListener("click", saveBoardJSON);
  if (loadBtn) loadBtn.addEventListener("click", promptLoadBoard);

  if (slider) slider.addEventListener("input", () => {
    const v = parseInt(slider.value, 10);
    if (!isFinite(v) || v <= 0) return;
    board.defaultWidth = v;
    for (const d of board.docs) {
      d.w = v;
      d.h = Math.round(v * (d.aspect || 1));
    }
    updatePanelLabels();
    draw();
  });
})();
"""
