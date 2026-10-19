"""
tb_core.py — canvas, view, item model, formatting signature, wanted paths.

ES module.  Owns all state that other modules read or write and
exports it.  Nothing here imports tb_dispatch, tb_view, or the panel;
those depend on this file, so the module graph stays acyclic.

STATE THAT CROSSES MODULE BOUNDARIES
====================================
Module `let` bindings are read-only from the import side (the
classic ES-module gotcha: `import { x } from "m"; x = 5` throws).
Any value another module needs to reassign therefore lives on the
`board` const object, whose properties are mutable:

    board.scale        replaces the old top-level `_boardScale` let
    board.items        replaced via `board.items = [...]` inside
                       mergeIncomingShapes (a property write, not a
                       binding write)
    board.selectedIdx  reassigned from the panel, the dispatch
                       handlers, and the remote select handler

`_setDraw(fn)` injects the `draw` implementation.  `resize()` and
`fitAll()` both call the injected `_drawRef`; without this
indirection, either function here would need to import `draw` from
`tb_dispatch.js`, which imports `tb_core.js` — a cycle.

    IMPORTANT: tb_core.js does NOT import `draw` statically, so
    a bare `if (typeof draw === "function") draw();` inside this
    module is a silent no-op — `typeof` on an undeclared identifier
    is `"undefined"` (it doesn't throw).  Any future edit that adds
    a `draw()` call here must go through `_drawRef`.

DOCUMENT SLOT LAYOUT
====================
`_docLayout()` is called from every frame (`_drawDocumentBounds`,
`_drawItemCard`), from every cursor hover (`documentAtBoardPoint`,
via `updateCursorReadout`), and from the panel's document-list
refresh.  Its result is memoized; `_invalidateDocLayout()` is the
only invalidation path and is called from `_rebaseAllItems` (after
a refresh or scale change) and from `_clearBoardAction` (which
empties `_documentSlots` directly).

ITEM MODEL
==========
See `makeItemFromRecord` for the exact shape.  Briefly:

    id              "<docPath>::<layerName>::<textIndex>"
    document*       Krita-side identity and dimensions
    layerName       Krita layer the text lives on
    textIndex       which text shape on that layer
    text, font*,    editable content and its presentation
    color, alignment
    rotationDeg
    wordBoxes       per-word quad corners in document space
    shapeBounds     axis-aligned bounds in document space
    x, y            anchor in document space
    bx, by, bw, bh  board-space rectangle (derived; recomputed by
                    `_rebaseAllItems` on every scale change)
    bz              z-order (higher is on top)
    colorRGB        derived from `formatSig` via `_colorFromSig`
    formatSig       a stable hash of (font, size, color, alignment)
                    used to group cards by formatting

FIT AND RESIZE
==============
`fitAll()` computes the view that frames every document slot and
every item.  It used to call `draw()` directly; that name is not
in scope in this module, so the call was a silent no-op, and the
new view state sat un-painted until the next user interaction.  It
now calls `_drawRef()`, which the boot module wires to `draw`
before any of this runs.

EXPORTS
=======
Canvas / DPR / ctx / view / board / palette / scale / document
slots / document lookups / layout / content-bounds / hit testing /
item selection / item construction / merge policy / wanted-paths /
grid arrangement / fit-to-content / resize / `_setDraw`.
"""

CORE_JS = r"""
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

/* ==========================================================================
   CANVAS
   ========================================================================== */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");
export let dpr      = window.devicePixelRatio || 1;

/* ==========================================================================
   VIEW
   ========================================================================== */

export const view = { zoom: 1, panX: 0, panY: 0 };

export function b2s(bx, by) {
  return [bx * view.zoom + view.panX,
          by * view.zoom + view.panY];
}
export function s2b(sx, sy) {
  return [(sx - view.panX) / view.zoom,
          (sy - view.panY) / view.zoom];
}

/* ==========================================================================
   BOARD STATE
   ==========================================================================
   board.scale replaces the old top-level `_boardScale` let.  Any
   module can assign `board.scale = X` because `board` is a const
   object, whereas a top-level `let _boardScale` cannot be assigned
   from another module. */

export const board = {
  items:        [],
  selectedIdx:  -1,
  scale:        0.25,
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

export const MIN_SHAPE_PX_W = 12;
export const MIN_SHAPE_PX_H = 8;

/* ==========================================================================
   DOCUMENT SLOTS
   ========================================================================== */

export const _documentSlots = new Map();
export const DOCUMENT_GAP   = 60;
let _docLayoutCache = null;

function _slotHash(docPath) {
  let h = 0;
  for (let i = 0; i < docPath.length; i++) {
    h = ((h << 5) - h + docPath.charCodeAt(i)) | 0;
  }
  return h >>> 0;
}

export function _computeBoardScale() {
  let widest = 0;
  for (const s of _documentSlots.values()) {
    if (s.docW > widest) widest = s.docW;
  }
  if (widest <= 0) return 0.25;
  return board.defaultWidth / widest;
}

export function _documentOrigin(docPath, docW, docH) {
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
      path:  docPath,
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
    total += s.docW * board.scale;
    count++;
  }
  if (count > 1) total += DOCUMENT_GAP * (count - 1);
  return total;
}

export function _invalidateDocLayout() {
  _docLayoutCache = null;
}

export function _docLayout() {
  if (_docLayoutCache !== null) return _docLayoutCache;

  const sorted = Array.from(_documentSlots.values())
    .sort((a, b) => a.index - b.index);

  const rowW = _slotRowWidth();
  let x = -rowW / 2;

  const layout = [];
  for (const s of sorted) {
    const bw = s.docW * board.scale;
    const bh = s.docH * board.scale;
    layout.push({
      slot: s,
      path: s.path,
      bx: x,
      by: -bh / 2,
      bw: bw,
      bh: bh,
      docW: s.docW,
      docH: s.docH,
    });
    x += bw + DOCUMENT_GAP;
  }

  _docLayoutCache = layout;
  return layout;
}

/* ==========================================================================
   DOCUMENT HIT-TEST AND COORDINATE MAPPING
   ========================================================================== */

export function documentAtBoardPoint(bx, by) {
  for (const entry of _docLayout()) {
    if (bx >= entry.bx && bx <= entry.bx + entry.bw &&
        by >= entry.by && by <= entry.by + entry.bh) {
      return {
        slot: entry.slot,
        docX: (bx - entry.bx) / board.scale,
        docY: (by - entry.by) / board.scale,
        docW: entry.docW,
        docH: entry.docH,
      };
    }
  }
  return null;
}

/* ==========================================================================
   COLOR HELPERS
   ========================================================================== */

export function _rgba(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}

export function _rgbaLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

/* ==========================================================================
   FORMATTING SIGNATURE
   ========================================================================== */

export function computeFormatSig(item) {
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

export function itemId(rec) {
  return (rec.document_path || "") + "::" +
         (rec.layer_name    || "") + "::" +
         String(rec.text_index || 0);
}

/* ==========================================================================
   CARD GEOMETRY
   ========================================================================== */

function _cardSizeFor(shapeBounds) {
  const wpx = Math.max(MIN_SHAPE_PX_W, shapeBounds.w || 1);
  const hpx = Math.max(MIN_SHAPE_PX_H, shapeBounds.h || 1);
  return {
    bw: wpx * board.scale,
    bh: hpx * board.scale,
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

    bx: 0, by: 0,
    bw: size.bw, bh: size.bh,
    bz: board.nextZ++,

    colorRGB:  _colorFromSig(sig),
    formatSig: sig,
  };
}

/* ==========================================================================
   REBASE
   ========================================================================== */

export function _rebaseAllItems() {
  _invalidateDocLayout();

  const sorted = Array.from(_documentSlots.values())
    .sort((a, b) => a.index - b.index);

  const rowW = _slotRowWidth();
  const rowLeft = -rowW / 2;

  const slotX = new Map();
  const slotY = new Map();
  let x = rowLeft;
  for (const s of sorted) {
    slotX.set(s.index, x);
    slotY.set(s.index, -(s.docH * board.scale) / 2);
    x += s.docW * board.scale + DOCUMENT_GAP;
  }

  for (const it of board.items) {
    const slot = _documentSlots.get(it.documentPath);
    if (!slot) continue;
    const sx = slotX.get(slot.index) || 0;
    const sy = slotY.get(slot.index) || 0;
    it.bx = sx + it.shapeBounds.x * board.scale;
    it.by = sy + it.shapeBounds.y * board.scale;
    const size = _cardSizeFor(it.shapeBounds);
    it.bw = size.bw;
    it.bh = size.bh;
  }
}

/* ==========================================================================
   MERGE POLICY
   ========================================================================== */

export function mergeIncomingShapes(shapes) {
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
    board.scale = _computeBoardScale();
  }

  _rebaseAllItems();
  _markWantedPathsFromItems();

  pgkitNotifyAll();
}

/* ==========================================================================
   WANTED PATHS
   ========================================================================== */

export function _pathBasename(path) {
  if (!path) return "(untitled)";
  const norm = String(path).replace(/\\/g, "/");
  const base = norm.substring(norm.lastIndexOf("/") + 1);
  const dot  = base.lastIndexOf(".");
  return dot > 0 ? base.substring(0, dot) : base;
}

export function addWantedPath(path) {
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

export function closedWantedPaths() {
  return board.wantedPaths.filter(wp => !wp.opened);
}

/* ==========================================================================
   LAYOUT
   ========================================================================== */

export function arrangeGrid(columns) {
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

export function groupByFormat() {
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
   Union of every document slot and every card rectangle, in board
   coordinates.  Used by `fitAll` and by `exportCanvasJSON` (imported
   by `tb_panel_lists.py`).  Returns null when the board has no
   documents and no items. */

export function _contentBounds() {
  let minX =  Infinity, minY =  Infinity;
  let maxX = -Infinity, maxY = -Infinity;
  let any = false;

  for (const entry of _docLayout()) {
    minX = Math.min(minX, entry.bx);
    minY = Math.min(minY, entry.by);
    maxX = Math.max(maxX, entry.bx + entry.bw);
    maxY = Math.max(maxY, entry.by + entry.bh);
    any = true;
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

/* ==========================================================================
   FIT TO CONTENT
   ==========================================================================
   `fitAll` is called from `refreshFromKrita` (after the first merge,
   when `board._autoFitDone` is false) and from the Fit and Reset
   view buttons.

   Both branches MUST paint through `_drawRef`, not a bare `draw`:
   `draw` is not a binding in this module, and `typeof draw` on an
   undeclared identifier silently returns `"undefined"` rather than
   throwing — so a `typeof draw === "function"` guard here is a
   no-op, and the freshly-computed view state never reaches the
   canvas until some unrelated event calls `draw` again. */

export function fitAll() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;
  const bounds = _contentBounds();

  if (!bounds) {
    view.zoom = 1;
    view.panX = cw / 2;
    view.panY = ch / 2;
    if (_drawRef) _drawRef();
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

  if (_drawRef) _drawRef();
}

/* ==========================================================================
   RESIZE
   ==========================================================================
   `_setDraw(fn)` is called by the boot module with `draw`.  A static
   import from tb_dispatch.js would create a cycle because
   tb_dispatch imports resize. */

let _drawRef = null;
export function _setDraw(fn) { _drawRef = fn; }

export function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (_drawRef) _drawRef();
}
window.addEventListener("resize", resize);

/* ==========================================================================
   HIT TESTING
   ========================================================================== */

export function _pointInItem(it, bx, by) {
  return bx >= it.bx && bx <= it.bx + it.bw &&
         by >= it.by && by <= it.by + it.bh;
}

export function _zSortedIndices() {
  const idx = [];
  for (let i = 0; i < board.items.length; i++) idx.push(i);
  idx.sort((a, b) => (board.items[a].bz || 0) - (board.items[b].bz || 0));
  return idx;
}

export function itemHitTest(sx, sy) {
  const order = _zSortedIndices();
  const [bx, by] = s2b(sx, sy);
  for (let k = order.length - 1; k >= 0; k--) {
    const i = order[k];
    if (_pointInItem(board.items[i], bx, by)) return i;
  }
  return -1;
}

export function _anyItemHitTest(sx, sy) {
  const bx = (sx - view.panX) / view.zoom;
  const by = (sy - view.panY) / view.zoom;
  for (let i = 0; i < board.items.length; i++) {
    if (_pointInItem(board.items[i], bx, by)) return true;
  }
  return false;
}

export function bringToTop(idx) {
  if (idx < 0 || idx >= board.items.length) return;
  board.items[idx].bz = board.nextZ++;
}

/* ==========================================================================
   SELECTED ITEM
   ========================================================================== */

export function selectedItem() {
  if (board.selectedIdx < 0 ||
      board.selectedIdx >= board.items.length) return null;
  return board.items[board.selectedIdx];
}
"""
