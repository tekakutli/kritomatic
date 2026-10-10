"""
tb_core.py — canvas, view, item model, formatting signature, wanted paths.

A text item on the board is:

    {
      id,           // document_path :: layer_name :: text_index
      documentPath, documentName,
      layerName, textIndex,
      text, fontFamily, fontSize, color, alignment,
      x, y,          // SVG anchor in document pixels
      rotationDeg,
      transform,     // 9-element list, or null
      wordBoxes,     // [{ word, x, y, w, h, corners }, ...]
      shapeBounds,   // { x, y, w, h } in document pixels
      documentWidth, documentHeight,

      bx, by,        // board-space position
      bw, bh,        // board-space size
      bz,            // stacking order

      colorRGB,      // { r, g, b } for border / list swatch
      formatSig,     // deterministic hash of the format parameters
    }

A wanted path is a .kra the user added to the board, open or not:

    { path, name, opened }
"""

CORE_JS = r"""
/* ==========================================================================
   CANVAS
   ========================================================================== */

const canvas = document.getElementById("c");
const ctx    = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

/* ==========================================================================
   VIEW
   ========================================================================== */

const view = { zoom: 1, panX: 0, panY: 0 };

function b2s(bx, by) {
  return [bx * view.zoom + view.panX,
          by * view.zoom + view.panY];
}
function s2b(sx, sy) {
  return [(sx - view.panX) / view.zoom,
          (sy - view.panY) / view.zoom];
}

/* ==========================================================================
   BOARD STATE
   ========================================================================== */

const board = {
  items:        [],
  selectedIdx:  -1,
  gridGap:      24,
  defaultWidth: 320,
  nextZ:        1,
  showWordBoxes: true,
  wantedPaths:  [],
  palette: [
    { r: 255, g: 200, b:  90 },
    { r: 120, g: 220, b: 255 },
    { r: 255, g: 130, b: 210 },
    { r: 140, g: 225, b: 160 },
    { r: 180, g: 155, b: 255 },
    { r: 255, g: 165, b: 105 },
    { r: 110, g: 220, b: 210 },
    { r: 220, g: 230, b: 120 },
  ],
};

const MIN_CARD_W = 60;
const MIN_CARD_H = 24;

function _rgba(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}
function _rgbaLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

/* ==========================================================================
   FORMATTING SIGNATURE
   ========================================================================== */

function computeFormatSig(item) {
  const s = [
    (item.fontFamily || "").toLowerCase(),
    String(item.fontSize || 0),
    (item.color || "").toLowerCase(),
    (item.alignment || ""),
  ].join("|");
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  }
  return String(h >>> 0);
}

function _colorFromSig(sig) {
  let h = 0;
  for (let i = 0; i < sig.length; i++) {
    h = ((h << 5) - h + sig.charCodeAt(i)) | 0;
  }
  return board.palette[Math.abs(h) % board.palette.length];
}

/* ==========================================================================
   ITEM IDENTITY
   ========================================================================== */

function itemId(rec) {
  return (rec.document_path || "") + "::" +
         (rec.layer_name    || "") + "::" +
         String(rec.text_index || 0);
}

function _findItemById(id) {
  for (let i = 0; i < board.items.length; i++) {
    if (board.items[i].id === id) return i;
  }
  return -1;
}

/* ==========================================================================
   ITEM CONSTRUCTION
   ========================================================================== */

function _cardSizeFor(shapeBounds) {
  const refW = board.defaultWidth;
  const refH = board.defaultWidth;
  const w = Math.max(1, shapeBounds.w || 1);
  const h = Math.max(1, shapeBounds.h || 1);
  const scale = Math.min(refW / w, refH / h);
  return {
    bw: Math.max(MIN_CARD_W, w * scale),
    bh: Math.max(MIN_CARD_H, h * scale),
  };
}

function makeItemFromRecord(rec) {
  const sb = rec.shape_bounds || { x:0, y:0, w:1, h:1 };
  const size = _cardSizeFor(sb);
  const sig  = computeFormatSig(rec);
  return {
    id:            itemId(rec),
    documentPath:  rec.document_path || "",
    documentName:  rec.document_name || "",
    layerName:     rec.layer_name    || "",
    textIndex:     rec.text_index    || 0,

    text:          rec.text          || "",
    fontFamily:    rec.font_family   || "sans-serif",
    fontSize:      rec.font_size     || 12,
    color:         rec.color         || "#000000",
    alignment:     rec.alignment     || "left",
    x:             rec.x             || 0,
    y:             rec.y             || 0,
    rotationDeg:   rec.rotation_deg  || 0,
    transform:     rec.transform     || null,

    wordBoxes:     rec.word_boxes    || [],
    shapeBounds:   sb,
    documentWidth:  rec.document_width  || 0,
    documentHeight: rec.document_height || 0,

    bx: 0, by: 0,
    bw: size.bw, bh: size.bh,
    bz: board.nextZ++,

    colorRGB:  _colorFromSig(sig),
    formatSig: sig,
  };
}

/* ==========================================================================
   MERGE POLICY
   ==========================================================================
   Identity is the item id.  Layout (bx, by, bw, bh, bz) is preserved
   when the item already exists; content and formatting come from the
   fresh record.  wantedPaths are marked opened/closed based on what
   documents show up. */

function mergeIncomingShapes(shapes) {
  const byId = new Map();
  for (const it of board.items) byId.set(it.id, it);

  const next = [];
  for (const rec of shapes) {
    const id  = itemId(rec);
    const old = byId.get(id);
    if (old) {
      old.documentPath   = rec.document_path || old.documentPath;
      old.documentName   = rec.document_name || old.documentName;
      old.text           = rec.text          || "";
      old.fontFamily     = rec.font_family   || old.fontFamily;
      old.fontSize       = rec.font_size     || old.fontSize;
      old.color          = rec.color         || old.color;
      old.alignment      = rec.alignment     || old.alignment;
      old.x              = rec.x             || 0;
      old.y              = rec.y             || 0;
      old.rotationDeg    = rec.rotation_deg  || 0;
      old.transform      = rec.transform     || null;
      old.wordBoxes      = rec.word_boxes    || [];
      old.shapeBounds    = rec.shape_bounds  || old.shapeBounds;
      old.documentWidth  = rec.document_width  || old.documentWidth;
      old.documentHeight = rec.document_height || old.documentHeight;
      old.formatSig      = computeFormatSig(rec);
      old.colorRGB       = _colorFromSig(old.formatSig);
      next.push(old);
      byId.delete(id);
    } else {
      const it = makeItemFromRecord(rec);
      const slot = findFreeSlot();
      it.bx = slot.x;
      it.by = slot.y;
      next.push(it);
    }
  }

  board.items = next;

  let maxZ = 0;
  for (const it of board.items) if (it.bz > maxZ) maxZ = it.bz;
  board.nextZ = Math.max(board.nextZ, maxZ + 1);

  _markWantedPathsFromItems();
}

/* ==========================================================================
   WANTED PATHS
   ==========================================================================
   Paths the user has explicitly added to the board, whether or not
   Krita has them open.  A path with no shapes is a picked-but-closed
   file; it stays in this list so Open all and the status line can see
   it, and turns "opened" as soon as a shape for it arrives. */

function _pathBasename(path) {
  if (!path) return "(untitled)";
  const norm = String(path).replace(/\\/g, "/");
  const base = norm.substring(norm.lastIndexOf("/") + 1);
  const dot  = base.lastIndexOf(".");
  return dot > 0 ? base.substring(0, dot) : base;
}

function addWantedPath(path) {
  if (!path) return;
  const norm = String(path);
  for (const p of board.wantedPaths) {
    if (p.path === norm) return;
  }
  board.wantedPaths.push({
    path:   norm,
    name:   _pathBasename(norm),
    opened: false,
  });
}

function _markWantedPathsFromItems() {
  for (const wp of board.wantedPaths) wp.opened = false;
  for (const it of board.items) {
    if (!it.documentPath) continue;
    for (const wp of board.wantedPaths) {
      if (wp.path === it.documentPath) wp.opened = true;
    }
  }
}

function closedWantedPaths() {
  return board.wantedPaths.filter(wp => !wp.opened);
}

/* ==========================================================================
   LAYOUT
   ========================================================================== */

function findFreeSlot() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;
  const [cx0, cy0] = s2b(cw / 2, ch / 2);
  const cascade = board.items.length % 12;
  const step = 22;
  return {
    x: cx0 + cascade * step - board.defaultWidth / 2,
    y: cy0 + cascade * step - board.defaultWidth / 4,
  };
}

function arrangeGrid(columns) {
  if (board.items.length === 0) return;
  const gap = board.gridGap;
  if (!columns) columns = Math.max(1, Math.ceil(Math.sqrt(board.items.length)));

  let curX = 0, curY = 0, rowH = 0, col = 0;
  for (const it of board.items) {
    if (col >= columns) {
      curX = 0;
      curY += rowH + gap;
      rowH = 0;
      col  = 0;
    }
    it.bx = curX;
    it.by = curY;
    curX += it.bw + gap;
    rowH = Math.max(rowH, it.bh);
    col++;
  }
}

function groupByFormat() {
  board.items.sort((a, b) => {
    if (a.formatSig !== b.formatSig)
      return a.formatSig < b.formatSig ? -1 : 1;
    if (a.documentName !== b.documentName)
      return a.documentName < b.documentName ? -1 : 1;
    if (a.layerName !== b.layerName)
      return a.layerName < b.layerName ? -1 : 1;
    return a.textIndex - b.textIndex;
  });
  arrangeGrid();
}

function fitAll() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  if (board.items.length === 0) {
    view.zoom = 1;
    view.panX = cw / 2;
    view.panY = ch / 2;
    draw();
    return;
  }

  let minX =  Infinity, minY =  Infinity;
  let maxX = -Infinity, maxY = -Infinity;
  for (const it of board.items) {
    minX = Math.min(minX, it.bx);
    minY = Math.min(minY, it.by);
    maxX = Math.max(maxX, it.bx + it.bw);
    maxY = Math.max(maxY, it.by + it.bh);
  }

  const pad = 60;
  const bw = (maxX - minX) + pad * 2;
  const bh = (maxY - minY) + pad * 2;

  const z = Math.min(cw / bw, ch / bh);
  view.zoom = Math.max(0.05, Math.min(8, z));
  view.panX = (cw - (maxX + minX) * view.zoom) / 2;
  view.panY = (ch - (maxY + minY) * view.zoom) / 2;
  draw();
}

function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
window.addEventListener("resize", resize);

/* ==========================================================================
   HIT TESTING
   ========================================================================== */

function _pointInItem(it, bx, by) {
  return bx >= it.bx && bx <= it.bx + it.bw &&
         by >= it.by && by <= it.by + it.bh;
}

function _zSortedIndices() {
  const idx = [];
  for (let i = 0; i < board.items.length; i++) idx.push(i);
  idx.sort((a, b) => (board.items[a].bz || 0) - (board.items[b].bz || 0));
  return idx;
}

function itemHitTest(sx, sy) {
  const order = _zSortedIndices();
  const [bx, by] = s2b(sx, sy);
  for (let k = order.length - 1; k >= 0; k--) {
    const i = order[k];
    if (_pointInItem(board.items[i], bx, by)) return i;
  }
  return -1;
}

function bringToTop(idx) {
  if (idx < 0 || idx >= board.items.length) return;
  board.items[idx].bz = board.nextZ++;
}

/* ==========================================================================
   SELECTED ITEM
   ========================================================================== */

function selectedItem() {
  if (board.selectedIdx < 0 ||
      board.selectedIdx >= board.items.length) return null;
  return board.items[board.selectedIdx];
}
"""
