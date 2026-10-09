"""
pg_core.py — canvas, view, board state, document model.

Board space is unbounded.  The view maps board coordinates to screen
coordinates with a uniform zoom and a translation:

    sx = bx * zoom + panX
    sy = by * zoom + panY

A document on the board is:

    {
      id:        stable identifier (the Krita document name),
      name:      display name (same as id in practice),
      thumb:     data-URL PNG, or null,
      imgUrl:    the data URL the cached Image object was built from,
      img:       the cached Image object (null until decoded),
      aspect:    height / width of the ORIGINAL document,
      w:         board-space width of the card,
      h:         board-space height (derived from w * aspect),
      x, y:      board-space position of the card's top-left corner,
      color:     assigned palette entry,
      modified:  Krita says the document has unsaved changes,
      active:    Krita says this is the currently-active document,
    }

The card's own aspect is what makes the thumbnail render without
distortion: the thumbnail is a scaled version of the same document
the aspect was read from, so drawing it into a w × w*aspect rect is
exactly fitting it.
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

const view = {
  zoom: 1,
  panX: 0,
  panY: 0,
};

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
  docs:         [],
  selectedIdx:  -1,
  gridGap:      24,
  defaultWidth: 320,
  palette: [
    { r: 255, g: 200, b:  90 },   // amber
    { r: 120, g: 220, b: 255 },   // cyan
    { r: 255, g: 130, b: 210 },   // magenta
    { r: 140, g: 225, b: 160 },   // green
    { r: 180, g: 155, b: 255 },   // violet
    { r: 255, g: 165, b: 105 },   // orange
    { r: 110, g: 220, b: 210 },   // teal
    { r: 220, g: 230, b: 120 },   // lime
  ],
};

function _rgba(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}

function _rgbaLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

function _assignColor(doc) {
  if (doc.color) return;
  // Colour is keyed by the document name so a doc keeps its hue across
  // refreshes; two docs that happen to hash to the same slot get
  // slightly different shades because the palette wraps.
  let hash = 0;
  for (let i = 0; i < doc.name.length; i++) {
    hash = ((hash << 5) - hash + doc.name.charCodeAt(i)) | 0;
  }
  const idx = Math.abs(hash) % board.palette.length;
  doc.color = board.palette[idx];
}

/* ==========================================================================
   THUMBNAIL CACHE
   ==========================================================================
   Each card's thumbnail is a data URL.  Decoding it once into an
   Image object and hanging that off the doc avoids re-decoding the
   PNG on every frame.  A doc whose thumb changes (a fresh pull) gets
   a new Image. */

function ensureThumbLoaded(doc) {
  if (!doc.thumb) return null;
  if (doc.img && doc.imgUrl === doc.thumb) return doc.img;
  const img = new Image();
  img.onload  = () => draw();
  img.onerror = () => draw();
  img.src = doc.thumb;
  doc.img = img;
  doc.imgUrl = doc.thumb;
  return img;
}

/* ==========================================================================
   PLACEMENT
   ==========================================================================
   Newly-arrived docs are placed to the right of the current rightmost
   card.  A more elaborate placement algorithm is not needed: the user
   can always rearrange by hand or press G to grid everything up. */

function findFreeSlot() {
  if (board.docs.length === 0) return { x: 0, y: 0 };
  const gap = board.gridGap;
  let maxRight = -Infinity;
  let minTop   =  Infinity;
  for (const d of board.docs) {
    maxRight = Math.max(maxRight, d.x + d.w);
    minTop   = Math.min(minTop,   d.y);
  }
  if (minTop === Infinity) minTop = 0;
  return { x: maxRight + gap, y: minTop };
}

/* ==========================================================================
   GRID ARRANGEMENT
   ========================================================================== */

function arrangeGrid(columns) {
  if (board.docs.length === 0) return;
  const gap = board.gridGap;
  if (!columns) columns = Math.max(1, Math.ceil(Math.sqrt(board.docs.length)));

  let curX = 0, curY = 0, rowH = 0, col = 0;
  for (const d of board.docs) {
    d.w = board.defaultWidth;
    d.h = Math.round(d.w * (d.aspect || 1));
    if (col >= columns) {
      curX = 0;
      curY += rowH + gap;
      rowH = 0;
      col  = 0;
    }
    d.x = curX;
    d.y = curY;
    curX += d.w + gap;
    rowH = Math.max(rowH, d.h);
    col++;
  }
}

/* ==========================================================================
   FIT ALL
   ========================================================================== */

function fitAll() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  if (board.docs.length === 0) {
    view.zoom = 1;
    view.panX = cw / 2;
    view.panY = ch / 2;
    draw();
    return;
  }

  let minX =  Infinity, minY =  Infinity;
  let maxX = -Infinity, maxY = -Infinity;
  for (const d of board.docs) {
    minX = Math.min(minX, d.x);
    minY = Math.min(minY, d.y);
    maxX = Math.max(maxX, d.x + d.w);
    maxY = Math.max(maxY, d.y + d.h);
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

/* ==========================================================================
   RESIZE
   ========================================================================== */

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
   HIT TEST
   ========================================================================== */

function docHitTest(sx, sy) {
  for (let i = board.docs.length - 1; i >= 0; i--) {
    const d = board.docs[i];
    const [x, y] = b2s(d.x, d.y);
    const w = d.w * view.zoom;
    const h = d.h * view.zoom;
    if (sx >= x && sx <= x + w && sy >= y && sy <= y + h) return i;
  }
  return -1;
}
"""
