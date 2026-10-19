"""
tb_panel_lists.py — the item list, the cursor readout, and the
canvas-JSON export.

ES module.  Exports the three DOM/refresh functions the boot module
uses:

    syncItemList         rebuild the panel's list of text shapes
    updateCursorReadout  map the cursor to a document and coordinates
    clearCursorReadout   blank the readout when the mouse leaves
    exportCanvasJSON     download a JSON dump of every object

`syncItemList` ends with `pgkitNotifyEntities()` — the item list is
one of the two entity levels the remote panel consumes.

DOM-WRITE GUARDS
================
`updateCursorReadout` runs on every mousemove.  It caches the last
doc text, position text, and color-class name, and only reassigns
when the value actually changed.  Without the guards, the browser
performed a style recalc on every mouse pixel — measurable at high
hover frequencies.  The color is tracked as a short string key
("dim", "dim2", "brt", "accent") rather than read back from
`style.color`, because the latter is normalized by the browser to
"rgb(...)" form and never matches the hex string we wrote.
"""

TB_PANEL_LISTS_JS = r"""
import {
  board, b2s, view, _rgba, _documentSlots, _docLayout, _invalidateDocLayout,
  documentAtBoardPoint, bringToTop, _contentBounds, _pathBasename,
  DOCUMENT_GAP,
} from "./tb_core.js";
import { _syncEditor } from "./tb_panel_fields.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   ITEM LIST
   ========================================================================== */

export function syncItemList() {
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
      import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
    });

    row.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      import("./tb_krita.js").then(m => m.activateItemDocument(it)).catch(() => {});
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

export function updateCursorReadout(sx, sy) {
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

export function clearCursorReadout() {
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
   ========================================================================== */

export function exportCanvasJSON() {
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
      boardScale:    board.scale,
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
    const ancBx = it.bx + (it.x - sb.x) * board.scale;
    const ancBy = it.by + (it.y - sb.y) * board.scale;
    const [ancX, ancY] = b2s(ancBx, ancBy);
    data.items.push({
      id: it.id, documentPath: it.documentPath, documentName: it.documentName,
      layerName: it.layerName, textIndex: it.textIndex, text: it.text,
      fontFamily: it.fontFamily, fontSize: it.fontSize, color: it.color,
      alignment: it.alignment, rotationDeg: it.rotationDeg,
      document: {
        x: it.x, y: it.y, shapeBounds: it.shapeBounds,
        documentWidth: it.documentWidth, documentHeight: it.documentHeight,
        wordBoxes: (it.wordBoxes || []).length,
      },
      board: {
        x: it.bx, y: it.by, w: it.bw, h: it.bh,
        screenX: scrX, screenY: scrY,
        screenW: it.bw * view.zoom, screenH: it.bh * view.zoom,
        anchorScreenX: ancX, anchorScreenY: ancY,
      },
    });
  }

  const text = JSON.stringify(data, null, 2);
  const url  = "data:application/json;charset=utf-8," + encodeURIComponent(text);
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
