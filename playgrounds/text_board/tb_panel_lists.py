"""
tb_panel_lists.py — the item list, the cursor coordinate readout,
and the canvas-JSON export.

`syncItemList` rebuilds the panel's list of text-shape cards and ends
with `pgkitNotifyEntities()`.

`updateCursorReadout` / `clearCursorReadout` drive the panel's
document + coordinate readout.  They run on every mousemove (the
fast path calls them unconditionally when the mouse is over the
canvas) and are therefore the DOM hot path.

`exportCanvasJSON` dumps every object the canvas knows about — in
document pixels, board units, and screen pixels.  Used by the
"Export canvas JSON" button and the `action.exportCanvasJSON`
remote action.

DOM-WRITE GUARDS
================
`updateCursorReadout` used to reassign `.textContent` and `.style.color`
on every mousemove.  Both trigger a style recalc in the browser even
when the value is unchanged.  The new versions compare first:

    textContent is compared to the previous string, byte-for-byte.
    Colors are tracked in JS locals `_lastCursorDoc` / `_lastCursorPos`,
    because `.style.color` reads back as `"rgb(...)"` rather than the
    hex string that was written.

The color states are small enums ("dim", "brt", "dim2", "accent")
mapped to the same hex strings the pre-optimization code wrote.
"""

TB_PANEL_LISTS_JS = r"""
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
    pgkitNotifyEntities();
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

  pgkitNotifyEntities();
}

/* ==========================================================================
   CURSOR COORDINATE READOUT
   ========================================================================== */

const _CURSOR_COLORS = {
  dim:    "#7b8794",
  dim2:   "#5a6774",
  brt:    "#b8ecff",
  accent: "#00e5ff",
};

let _lastCursorDoc = null;
let _lastCursorPos = null;

function updateCursorReadout(sx, sy) {
  const docEl = document.getElementById("cursorDoc");
  const posEl = document.getElementById("cursorPos");
  if (!docEl || !posEl) return;

  const bx = (sx - view.panX) / view.zoom;
  const by = (sy - view.panY) / view.zoom;
  const hit = documentAtBoardPoint(bx, by);

  let docText, docColor, posText, posColor;

  if (!hit) {
    docText  = "\u2014";
    docColor = "dim";
    posText  = "\u2014";
    posColor = "dim2";
  } else {
    docText  = hit.slot.name || (hit.slot.docW + "\u00d7" + hit.slot.docH);
    docColor = "brt";

    let px = Math.floor(hit.docX);
    let py = Math.floor(hit.docY);
    if (px < 0) px = 0;
    if (py < 0) py = 0;
    if (px >= hit.docW) px = hit.docW - 1;
    if (py >= hit.docH) py = hit.docH - 1;

    posText  = px + ", " + py;
    posColor = "accent";
  }

  if (docEl.textContent !== docText) docEl.textContent = docText;
  if (posEl.textContent !== posText) posEl.textContent = posText;

  if (_lastCursorDoc !== docColor) {
    docEl.style.color = _CURSOR_COLORS[docColor];
    _lastCursorDoc = docColor;
  }
  if (_lastCursorPos !== posColor) {
    posEl.style.color = _CURSOR_COLORS[posColor];
    _lastCursorPos = posColor;
  }
}

function clearCursorReadout() {
  const docEl = document.getElementById("cursorDoc");
  const posEl = document.getElementById("cursorPos");
  if (docEl) {
    if (docEl.textContent !== "\u2014") docEl.textContent = "\u2014";
    if (_lastCursorDoc !== "dim") {
      docEl.style.color = _CURSOR_COLORS.dim;
      _lastCursorDoc = "dim";
    }
  }
  if (posEl) {
    if (posEl.textContent !== "\u2014") posEl.textContent = "\u2014";
    if (_lastCursorPos !== "dim2") {
      posEl.style.color = _CURSOR_COLORS.dim2;
      _lastCursorPos = "dim2";
    }
  }
}

/* ==========================================================================
   CANVAS JSON EXPORT
   ==========================================================================
   Dumps every object the canvas knows about, in every coordinate space
   it is known in: document pixels, board units, and screen pixels.
   For a shape whose placement looks wrong, comparing its doc coords
   against its board coords and its slot's board coords is what tells
   us which transform is responsible.

   Uses the cached `_docLayout()` rather than re-sorting. */

function exportCanvasJSON() {
  const data = {
    exportedAt: new Date().toISOString(),
    window: {
      innerWidth:       window.innerWidth,
      innerHeight:      window.innerHeight,
      devicePixelRatio: window.devicePixelRatio,
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

  for (const entry of _docLayout()) {
    const [scrX, scrY] = b2s(entry.bx, entry.by);
    data.documentSlots.push({
      index: entry.slot.index,
      name:  entry.slot.name || "",
      docW:  entry.docW,
      docH:  entry.docH,
      board: {
        x: entry.bx, y: entry.by, w: entry.bw, h: entry.bh,
        screenX: scrX, screenY: scrY,
        screenW: entry.bw * view.zoom,
        screenH: entry.bh * view.zoom,
      },
    });
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
"""
