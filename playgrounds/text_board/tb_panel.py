"""
tb_panel.py — panel bindings and item list.

The editor follows the selected card.  Fields grey out with nothing
selected and reflect the selected card's own values otherwise.  The
Apply button posts a patch containing only the fields that actually
changed.  Two buttons drive file loading: + Add files… (opens the
native picker) and Open all (opens every picked-but-closed path).

A cursor readout below the item list shows the document under the
pointer and the pointer's document-space coordinates, following
Krita's convention (origin at top-left, growing right and down).

The card-width slider controls `_boardScale`.  Once moved,
`board._scaleUserSet` is true and further refreshes do not override
the user's choice.  Clearing the board resets both flags.
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
   CURSOR COORDINATE READOUT
   ==========================================================================
   Called on every mousemove with screen-local coordinates.  Maps the
   point to board space, finds the document that contains it, and
   reports the point in that document's own pixel space.  Krita's
   coordinates are integer pixels, origin at the top-left of the
   canvas, growing right and down. */

function updateCursorReadout(sx, sy) {
  const docEl = document.getElementById("cursorDoc");
  const posEl = document.getElementById("cursorPos");
  if (!docEl || !posEl) return;

  const [bx, by] = s2b(sx, sy);
  const hit = documentAtBoardPoint(bx, by);

  if (!hit) {
    docEl.textContent = "\u2014";
    docEl.style.color = "#7b8794";
    posEl.textContent = "\u2014";
    posEl.style.color = "#5a6774";
    return;
  }

  const name = hit.slot.name || (hit.slot.docW + "\u00d7" + hit.slot.docH);
  docEl.textContent = name;
  docEl.style.color = "#b8ecff";

  let px = Math.floor(hit.docX);
  let py = Math.floor(hit.docY);
  if (px < 0) px = 0;
  if (py < 0) py = 0;
  if (px >= hit.docW) px = hit.docW - 1;
  if (py >= hit.docH) py = hit.docH - 1;

  posEl.textContent = px + ", " + py;
  posEl.style.color = "#00e5ff";
}

function clearCursorReadout() {
  const docEl = document.getElementById("cursorDoc");
  const posEl = document.getElementById("cursorPos");
  if (docEl) { docEl.textContent = "\u2014"; docEl.style.color = "#7b8794"; }
  if (posEl) { posEl.textContent = "\u2014"; posEl.style.color = "#5a6774"; }
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
    if (selLbl)  selLbl.textContent = "\u2014";
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
      " \u00b7 idx " + it.textIndex;
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
   CANVAS JSON EXPORT
   ==========================================================================
   Dumps every object the canvas knows about, in every coordinate space
   it is known in: document pixels, board units, and screen pixels.
   For a shape whose placement looks wrong, comparing its doc coords
   against its board coords and its slot's board coords is what tells
   us which transform is responsible. */

function exportCanvasJSON() {
  const data = {
    exportedAt: new Date().toISOString(),
    window: {
      innerWidth:        window.innerWidth,
      innerHeight:       window.innerHeight,
      devicePixelRatio:  window.devicePixelRatio,
    },
    view: { zoom: view.zoom, panX: view.panX, panY: view.panY },
    board: {
      defaultWidth:  board.defaultWidth,
      boardScale:    _boardScale,
      gridGap:       board.gridGap,
      scaleUserSet:  board._scaleUserSet,
      autoFitDone:   board._autoFitDone,
      showWordBoxes: board.showWordBoxes,
      documentGap:   DOCUMENT_GAP,
    },
    contentBounds: _contentBounds(),
    documentSlots: [],
    items:         [],
  };

  const sorted = Array.from(_documentSlots.values())
    .sort((a, b) => a.index - b.index);
  const rowW = _slotRowWidth();
  let sx = -rowW / 2;
  for (const s of sorted) {
    const sy = -(s.docH * _boardScale) / 2;
    const sw = s.docW * _boardScale;
    const sh = s.docH * _boardScale;
    const [scrX, scrY] = b2s(sx, sy);
    data.documentSlots.push({
      index: s.index,
      name:  s.name || "",
      docW:  s.docW,
      docH:  s.docH,
      board: {
        x: sx, y: sy, w: sw, h: sh,
        screenX: scrX, screenY: scrY,
        screenW: sw * view.zoom,
        screenH: sh * view.zoom,
      },
    });
    sx += s.docW * _boardScale + DOCUMENT_GAP;
  }

  for (const it of board.items) {
    const [scrX, scrY] = b2s(it.bx, it.by);
    const sb = it.shapeBounds;
    const ancBx = it.bx + (it.x - sb.x) * _boardScale;
    const ancBy = it.by + (it.y - sb.y) * _boardScale;
    const [ancX, ancY] = b2s(ancBx, ancBy);
    data.items.push({
      id:           it.id,
      documentPath: it.documentPath,
      documentName: it.documentName,
      layerName:    it.layerName,
      textIndex:    it.textIndex,
      text:         it.text,
      fontFamily:   it.fontFamily,
      fontSize:     it.fontSize,
      color:        it.color,
      alignment:    it.alignment,
      rotationDeg:  it.rotationDeg,
      document: {
        x:              it.x,
        y:              it.y,
        shapeBounds:    it.shapeBounds,
        documentWidth:  it.documentWidth,
        documentHeight: it.documentHeight,
        wordBoxes:      (it.wordBoxes || []).length,
      },
      board: {
        x: it.bx, y: it.by, w: it.bw, h: it.bh,
        screenX: scrX, screenY: scrY,
        screenW: it.bw * view.zoom,
        screenH: it.bh * view.zoom,
        anchorScreenX: ancX,
        anchorScreenY: ancY,
      },
    });
  }

  const text = JSON.stringify(data, null, 2);
  const url  = "data:application/json;charset=utf-8," +
               encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = "canvas_dump.json";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  flashStatus("exported " + board.items.length + " item(s)", "ok");
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
  const exportJsonBtn = document.getElementById("exportJsonBtn");

  if (exportJsonBtn) exportJsonBtn.addEventListener("click", exportCanvasJSON);
  if (refreshBtn)  refreshBtn.addEventListener("click", refreshFromKrita);
  if (addFilesBtn) addFilesBtn.addEventListener("click", pickFilesViaServer);
  if (openAllBtn)  openAllBtn.addEventListener("click", openAllClosed);

  if (gridBtn) gridBtn.addEventListener("click", () => {
    arrangeGrid();
    draw();
    flashStatus("arranged " + board.items.length + " card(s)", "ok");
  });

  if (fitBtn) fitBtn.addEventListener("click", () => {
    board._autoFitDone = true;
    fitAll();
  });

  if (resetBtn) resetBtn.addEventListener("click", () => {
    board._autoFitDone = true;
    fitAll();
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
    _documentSlots.clear();
    board._scaleUserSet = false;
    board._autoFitDone  = false;
    syncItemList();
    _syncEditor();
    clearCursorReadout();
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
    board.defaultWidth  = v;
    board._scaleUserSet = true;
    _boardScale         = _computeBoardScale();
    _rebaseAllItems();
    updatePanelLabels();
    draw();
  });
})();
"""
