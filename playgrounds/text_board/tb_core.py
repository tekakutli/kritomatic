"""
tb_core.py — canvas, view, item model, formatting signature, wanted paths.

Layout model
------------
Documents are laid out side by side in a horizontal row, ordered by a
stable hash of the document path.  Each slot is as wide as its
document, expressed in board units.  A shape sits at
(slot.x + shape_bounds.x * scale, slot.y + shape_bounds.y * scale),
where shape_bounds is the axis-aligned document-space rectangle the
daemon reports for the shape.

The card's rectangle is exactly the shape's text area — no padding,
no rotation.  The text label is drawn as an indication on top and is
allowed to overflow.
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
  _scaleUserSet: false,
  _autoFitDone:  false,
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

const MIN_SHAPE_PX_W = 12;   // document pixels
const MIN_SHAPE_PX_H = 8;    // document pixels

/* ==========================================================================
   DOCUMENT SLOTS AND BOARD SCALE
   ========================================================================== */

let _boardScale = 0.25;

const _documentSlots = new Map();

const DOCUMENT_GAP = 60;   // board units between adjacent slots

function _slotHash(docPath) {
  let h = 0;
  for (let i = 0; i < docPath.length; i++) {
    h = ((h << 5) - h + docPath.charCodeAt(i)) | 0;
  }
  return h >>> 0;
}

function _computeBoardScale() {
  let widest = 0;
  for (const s of _documentSlots.values()) {
    if (s.docW > widest) widest = s.docW;
  }
  if (widest <= 0) return 0.25;
  return board.defaultWidth / widest;
}

function _documentOrigin(docPath, docW, docH) {
  if (!_documentSlots.has(docPath)) {
    const myHash = _slotHash(docPath);
    const existing = Array.from(_documentSlots.values());
    let index = 0;
    for (const s of existing) {
      if (s.hash < myHash) index++;
    }
    for (const s of existing) {
      if (s.index >= index) s.index++;
    }
    _documentSlots.set(docPath, {
      hash:  myHash,
      index: index,
      docW:  docW,
      docH:  docH,
      name:  _pathBasename(docPath),
    });
  } else {
    const slot = _documentSlots.get(docPath);
    slot.docW = docW;
    slot.docH = docH;
  }
  return _documentSlots.get(docPath);
}

function _slotRowWidth() {
  let total = 0;
  let count = 0;
  for (const s of _documentSlots.values()) {
    total += s.docW * _boardScale;
    count++;
  }
  if (count > 1) total += DOCUMENT_GAP * (count - 1);
  return total;
}

/* ==========================================================================
   DOCUMENT HIT-TEST AND COORDINATE MAPPING
   ========================================================================== */

function documentAtBoardPoint(bx, by) {
  const sorted = Array.from(_documentSlots.values())
    .sort((a, b) => a.index - b.index);

  const rowW = _slotRowWidth();
  const rowLeft = -rowW / 2;

  let x = rowLeft;
  for (const s of sorted) {
    const sx = x;
    const sy = -(s.docH * _boardScale) / 2;
    const sw = s.docW * _boardScale;
    const sh = s.docH * _boardScale;

    if (bx >= sx && bx <= sx + sw &&
        by >= sy && by <= sy + sh) {
      const dx = (bx - sx) / _boardScale;
      const dy = (by - sy) / _boardScale;
      return {
        slot: s,
        docX: dx,
        docY: dy,
        docW: s.docW,
        docH: s.docH,
      };
    }

    x += s.docW * _boardScale + DOCUMENT_GAP;
  }
  return null;
}

/* ==========================================================================
   COLOR HELPERS
   ========================================================================== */

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
   CARD GEOMETRY
   ==========================================================================
   The card is exactly the shape's document-space AABB.  No padding is
   applied: shape_bounds already comes from Krita's own boundingBox,
   transformed into document space.  The text label is drawn as an
   indication on top and is allowed to overflow the card. */

function _cardSizeFor(shapeBounds) {
  const wpx = Math.max(MIN_SHAPE_PX_W, shapeBounds.w || 1);
  const hpx = Math.max(MIN_SHAPE_PX_H, shapeBounds.h || 1);
  return {
    bw: wpx * _boardScale,
    bh: hpx * _boardScale,
  };
}

/* ==========================================================================
   ITEM CONSTRUCTION
   ========================================================================== */

function makeItemFromRecord(rec) {
  const sb  = rec.shape_bounds || { x:0, y:0, w:1, h:1 };
  const docW = rec.document_width  || 1;
  const docH = rec.document_height || 1;
  const slot = _documentOrigin(rec.document_path || "", docW, docH);
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

    wordBoxes:     rec.word_boxes    || [],
    shapeBounds:   sb,
    documentWidth:  docW,
    documentHeight: docH,

    bx: 0,
    by: 0,
    bw: size.bw,
    bh: size.bh,
    bz: board.nextZ++,

    colorRGB:  _colorFromSig(sig),
    formatSig: sig,
  };
}

/* ==========================================================================
   REBASE
   ========================================================================== */

function _rebaseAllItems() {
  const sorted = Array.from(_documentSlots.values())
    .sort((a, b) => a.index - b.index);

  const rowW = _slotRowWidth();
  const rowLeft = -rowW / 2;

  const slotX = new Map();
  const slotY = new Map();
  let x = rowLeft;
  for (const s of sorted) {
    slotX.set(s.index, x);
    slotY.set(s.index, -(s.docH * _boardScale) / 2);
    x += s.docW * _boardScale + DOCUMENT_GAP;
  }

  for (const it of board.items) {
    const slot = _documentSlots.get(it.documentPath);
    if (!slot) continue;
    const sx = slotX.get(slot.index) || 0;
    const sy = slotY.get(slot.index) || 0;
    it.bx = sx + it.shapeBounds.x * _boardScale;
    it.by = sy + it.shapeBounds.y * _boardScale;
    const size = _cardSizeFor(it.shapeBounds);
    it.bw = size.bw;
    it.bh = size.bh;
  }
}

/* ==========================================================================
   MERGE POLICY
   ========================================================================== */

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
      old.wordBoxes      = rec.word_boxes    || [];
      old.shapeBounds    = rec.shape_bounds  || old.shapeBounds;
      old.documentWidth  = rec.document_width  || old.documentWidth;
      old.documentHeight = rec.document_height || old.documentHeight;
      old.formatSig      = computeFormatSig(rec);
      old.colorRGB       = _colorFromSig(old.formatSig);
      next.push(old);
      byId.delete(id);
    } else {
      const dp = rec.document_path || "";
      if (!dp || dp.indexOf("untitled://") === 0) continue;
      const it = makeItemFromRecord(rec);
      next.push(it);
    }
  }

  board.items = next;

  let maxZ = 0;
  for (const it of board.items) if (it.bz > maxZ) maxZ = it.bz;
  board.nextZ = Math.max(board.nextZ, maxZ + 1);

  if (!board._scaleUserSet) {
    _boardScale = _computeBoardScale();
  }

  _rebaseAllItems();
  _markWantedPathsFromItems();
}

/* ==========================================================================
   WANTED PATHS
   ========================================================================== */

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

/* ==========================================================================
   CONTENT BOUNDS
   ==========================================================================
   The union of every document rectangle and every card, in board
   coordinates.  fitAll frames this so the user sees whole documents
   with their cards inside, not just a cluster of cards floating in
   space. */

function _contentBounds() {
  let minX =  Infinity, minY =  Infinity;
  let maxX = -Infinity, maxY = -Infinity;
  let any = false;

  const sorted = Array.from(_documentSlots.values())
    .sort((a, b) => a.index - b.index);
  const rowW = _slotRowWidth();
  let sx = -rowW / 2;
  for (const s of sorted) {
    const sy = -(s.docH * _boardScale) / 2;
    const ex = sx + s.docW * _boardScale;
    const ey = sy + s.docH * _boardScale;
    minX = Math.min(minX, sx);
    minY = Math.min(minY, sy);
    maxX = Math.max(maxX, ex);
    maxY = Math.max(maxY, ey);
    any = true;
    sx += s.docW * _boardScale + DOCUMENT_GAP;
  }

  for (const it of board.items) {
    minX = Math.min(minX, it.bx);
    minY = Math.min(minY, it.by);
    maxX = Math.max(maxX, it.bx + it.bw);
    maxY = Math.max(maxY, it.by + it.bh);
    any = true;
  }

  if (!any) return null;
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

function fitAll() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;
  const bounds = _contentBounds();

  if (!bounds) {
    view.zoom = 1;
    view.panX = cw / 2;
    view.panY = ch / 2;
    draw();
    return;
  }

  const pad = 60;
  const bw = bounds.w + pad * 2;
  const bh = bounds.h + pad * 2;

  const z = Math.min(cw / bw, ch / bh);
  view.zoom = Math.max(0.05, Math.min(8, z));

  const cx = bounds.x + bounds.w / 2;
  const cy = bounds.y + bounds.h / 2;
  view.panX = cw / 2 - cx * view.zoom;
  view.panY = ch / 2 - cy * view.zoom;

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
