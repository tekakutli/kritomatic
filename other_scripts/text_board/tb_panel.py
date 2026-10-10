"""
tb_panel.py — panel bindings and item list.

The editor follows the selected card.  Fields grey out with nothing
selected and reflect the selected card's own values otherwise.  The
Apply button posts a patch containing only the fields that actually
changed.  Two buttons drive file loading: + Add files… (opens the
native picker) and Open all (opens every picked-but-closed path).
"""

PANEL_JS = r"""
/* ==========================================================================
   SLIDERS
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
   EDITOR
   ========================================================================== */

function _syncEditor() {
  const it = selectedItem();

  const textEl  = document.getElementById("editText");
  const fontEl  = document.getElementById("editFont");
  const sizeEl  = document.getElementById("editSize");
  const colorEl = document.getElementById("editColor");
  const alignEl = document.getElementById("editAlign");
  const rotEl   = document.getElementById("editRot");
  const applyEl = document.getElementById("applyBtn");
  const selLbl  = document.getElementById("selLabel");

  if (!it) {
    if (textEl)  { textEl.value = ""; textEl.disabled = true; }
    if (fontEl)  { fontEl.value = ""; fontEl.disabled = true; }
    if (sizeEl)  { sizeEl.value = ""; sizeEl.disabled = true; }
    if (colorEl) { colorEl.value = ""; colorEl.disabled = true; }
    if (alignEl) { alignEl.value = "left"; alignEl.disabled = true; }
    if (rotEl)   { rotEl.value = ""; rotEl.disabled = true; }
    if (applyEl) applyEl.disabled = true;
    if (selLbl)  selLbl.textContent = "—";
    return;
  }

  if (textEl) {
    textEl.disabled = false;
    if (document.activeElement !== textEl) textEl.value = it.text || "";
  }
  if (fontEl) {
    fontEl.disabled = false;
    if (document.activeElement !== fontEl)
      fontEl.value = it.fontFamily || "";
  }
  if (sizeEl) {
    sizeEl.disabled = false;
    if (document.activeElement !== sizeEl)
      sizeEl.value = String(it.fontSize || "");
  }
  if (colorEl) {
    colorEl.disabled = false;
    if (document.activeElement !== colorEl)
      colorEl.value = it.color || "";
  }
  if (alignEl) {
    alignEl.disabled = false;
    alignEl.value = it.alignment || "left";
  }
  if (rotEl) {
    rotEl.disabled = false;
    if (document.activeElement !== rotEl)
      rotEl.value = (it.rotationDeg || 0).toFixed(2);
  }
  if (applyEl) applyEl.disabled = false;

  if (selLbl) {
    selLbl.textContent = (it.layerName || "?") +
      " · idx " + it.textIndex;
  }
}

/* ==========================================================================
   ITEM LIST
   ========================================================================== */

function syncItemList() {
  const list = document.getElementById("itemList");
  const countEl = document.getElementById("itemCount");
  if (!list) return;
  list.innerHTML = "";
  if (countEl) countEl.textContent = String(board.items.length);

  if (board.items.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no text shapes yet — press Refresh from Krita";
    list.appendChild(e);
    return;
  }

  for (let i = 0; i < board.items.length; i++) {
    const it = board.items[i];
    const isSel = (i === board.selectedIdx);

    const row = document.createElement("div");
    row.className = "itemRow" + (isSel ? " selected" : "");

    const sw = document.createElement("span");
    sw.className = "itemSwatch";
    const hue = it.colorRGB || board.palette[0];
    sw.style.background = _rgba(hue, isSel ? 0.95 : 0.55);
    sw.style.border     = "1px solid " + _rgba(hue, isSel ? 1.0 : 0.85);
    row.appendChild(sw);

    const t = document.createElement("span");
    t.className = "itemText";
    t.textContent = (it.text || "(empty)").slice(0, 48);
    t.title = it.text || "";
    row.appendChild(t);

    const m = document.createElement("span");
    m.className = "itemMeta";
    m.textContent = it.layerName || "";
    m.title = it.documentName + " / " + it.layerName;
    row.appendChild(m);

    row.addEventListener("click", () => {
      board.selectedIdx = i;
      bringToTop(i);
      syncItemList();
      _syncEditor();
      draw();
    });

    row.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      activateItemDocument(it);
      flashStatus("activating " + (it.documentName || "document"), "ok");
    });

    list.appendChild(row);
  }
}

/* ==========================================================================
   STATUS
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
  const refreshBtn  = document.getElementById("refreshBtn");
  const addFilesBtn = document.getElementById("addFilesBtn");
  const openAllBtn  = document.getElementById("openAllBtn");
  const gridBtn     = document.getElementById("gridBtn");
  const fitBtn      = document.getElementById("fitBtn");
  const resetBtn    = document.getElementById("resetBtn");
  const groupBtn    = document.getElementById("groupBtn");
  const clearBtn    = document.getElementById("clearBtn");
  const saveBtn     = document.getElementById("saveSceneBtn");
  const loadBtn     = document.getElementById("loadSceneBtn");
  const applyBtn    = document.getElementById("applyBtn");
  const slider      = document.getElementById("cardWSlider");
  const wbToggle    = document.getElementById("wordBoxToggle");

  if (refreshBtn)  refreshBtn.addEventListener("click", refreshFromKrita);
  if (addFilesBtn) addFilesBtn.addEventListener("click", pickFilesViaServer);
  if (openAllBtn)  openAllBtn.addEventListener("click", openAllClosed);

  if (gridBtn) gridBtn.addEventListener("click", () => {
    arrangeGrid();
    draw();
    flashStatus("arranged " + board.items.length + " card(s)", "ok");
  });

  if (fitBtn) fitBtn.addEventListener("click", fitAll);

  if (resetBtn) resetBtn.addEventListener("click", () => {
    view.zoom = 1;
    view.panX = window.innerWidth  / 2;
    view.panY = window.innerHeight / 2;
    draw();
  });

  if (groupBtn) groupBtn.addEventListener("click", () => {
    groupByFormat();
    draw();
    flashStatus("grouped by format", "ok");
  });

  if (clearBtn) clearBtn.addEventListener("click", () => {
    if (board.items.length === 0) return;
    board.items = [];
    board.selectedIdx = -1;
    syncItemList();
    _syncEditor();
    draw();
    flashStatus("board cleared", "ok");
  });

  if (applyBtn) applyBtn.addEventListener("click", applySelectedChanges);

  if (saveBtn) saveBtn.addEventListener("click", saveBoardJSON);
  if (loadBtn) loadBtn.addEventListener("click", promptLoadBoard);

  if (wbToggle) wbToggle.addEventListener("change", () => {
    board.showWordBoxes = !!wbToggle.checked;
    draw();
  });

  if (slider) slider.addEventListener("input", () => {
    const v = parseInt(slider.value, 10);
    if (!isFinite(v) || v <= 0) return;
    const oldDefault = board.defaultWidth;
    if (oldDefault > 0) {
      const factor = v / oldDefault;
      for (const it of board.items) {
        it.bw *= factor;
        it.bh *= factor;
      }
    }
    board.defaultWidth = v;
    updatePanelLabels();
    draw();
  });
})();
"""
