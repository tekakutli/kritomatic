"""
pg_core.py — canvas, view, board state, file model.

A file on the board is:

    {
      path, normPath, name, thumb, imgUrl, img,
      aspect:     height / width of the ORIGINAL document,
      docWidth, docHeight,
      w, h:       board-space size of the unrotated card,
      x, y:       board-space position of the unrotated card's
                  top-left corner,
      rotation:   radians, around the card's centre,
      z:          stacking order,
      userSized:  false until the user resizes this card by dragging
                  a corner.  While false, refresh keeps h locked to
                  w · aspect, so a freshly-added card mirrors its
                  document's own proportions.  Set to true on the
                  first corner drag, after which the user's size
                  wins and refresh no longer touches h.
      color, open, active, modified, missing, kritaName,
    }
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
  files:        [],
  selectedIdx:  -1,
  gridGap:      24,
  defaultWidth: 320,
  nextZ:        1,
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

const CARD_HANDLE_R    = 4.5;
const CARD_HIT_R       = 11;
const ROTATE_HANDLE_R  = 6;
const ROTATE_HANDLE_HR = 10;
const ROTATE_HANDLE_OFFSET = 26;
const MIN_CARD_W       = 40;
const MIN_CARD_H       = 30;

const CORNER_SIGNS = [
  [-1, -1],
  [ 1, -1],
  [ 1,  1],
  [-1,  1],
];

function _rgba(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}
function _rgbaLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

function _assignColor(file) {
  if (file.color) return;
  let hash = 0;
  const s = file.path || file.name || "";
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) - hash + s.charCodeAt(i)) | 0;
  }
  const idx = Math.abs(hash) % board.palette.length;
  file.color = board.palette[idx];
}

function _basenameWithoutExt(path) {
  if (!path) return "(untitled)";
  const norm = String(path).replace(/\\/g, "/");
  const base = norm.substring(norm.lastIndexOf("/") + 1);
  const dot  = base.lastIndexOf(".");
  return dot > 0 ? base.substring(0, dot) : base;
}

/* ==========================================================================
   FILE STATE HELPERS
   ========================================================================== */

function fileState(f) {
  if (f.missing)   return "missing";
  if (f.open)      return "open";
  return "closed";
}

/* ==========================================================================
   RECTANGLE OVERLAP
   ========================================================================== */

function _rectsOverlap(x1, y1, w1, h1, x2, y2, w2, h2) {
  return !(x1 + w1 <= x2 || x2 + w2 <= x1 ||
           y1 + h1 <= y2 || y2 + h2 <= y1);
}

/* ==========================================================================
   ROTATION HELPERS
   ========================================================================== */

function _rotateOffset(dx, dy, angle) {
  const c = Math.cos(angle), s = Math.sin(angle);
  return [dx * c - dy * s, dx * s + dy * c];
}

function _cardCornersBoard(f) {
  const cx = f.x + f.w / 2;
  const cy = f.y + f.h / 2;
  const hw = f.w / 2, hh = f.h / 2;
  return CORNER_SIGNS.map(([sx, sy]) => {
    const [rx, ry] = _rotateOffset(sx * hw, sy * hh, f.rotation);
    return [cx + rx, cy + ry];
  });
}

function _fileCornerScreen(f, corner) {
  const [bx, by] = _cardCornersBoard(f)[corner];
  return b2s(bx, by);
}

function _rotateHandleScreen(f) {
  const cx = f.x + f.w / 2;
  const cy = f.y + f.h / 2;
  const screenOffset = (f.h * view.zoom) / 2 + ROTATE_HANDLE_OFFSET;
  const [csx, csy] = b2s(cx, cy);
  const rx =  screenOffset * Math.sin(f.rotation);
  const ry = -screenOffset * Math.cos(f.rotation);
  return [csx + rx, csy + ry];
}

function _cardAABB(f) {
  const corners = _cardCornersBoard(f);
  let minX = Infinity, minY = Infinity;
  let maxX = -Infinity, maxY = -Infinity;
  for (const [x, y] of corners) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

/* ==========================================================================
   Z ORDER
   ========================================================================== */

function _zSortedIndices() {
  const idx = [];
  for (let i = 0; i < board.files.length; i++) idx.push(i);
  idx.sort((a, b) =>
    ((board.files[a].z || 0) - (board.files[b].z || 0)));
  return idx;
}

function bringToTop(idx) {
  if (idx < 0 || idx >= board.files.length) return;
  board.files[idx].z = board.nextZ++;
}

/* ==========================================================================
   THUMBNAIL CACHE
   ========================================================================== */

function ensureThumbLoaded(file) {
  if (!file.thumb) return null;
  if (file.img && file.imgUrl === file.thumb) return file.img;
  const img = new Image();
  img.onload  = () => draw();
  img.onerror = () => draw();
  img.src = file.thumb;
  file.img = img;
  file.imgUrl = file.thumb;
  return img;
}

/* ==========================================================================
   PLACEMENT
   ========================================================================== */

function findFreeSlot() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;
  const w  = board.defaultWidth;
  const h  = board.defaultWidth;
  const [cx0, cy0] = s2b(cw / 2, ch / 2);
  const step = 22;
  const cascade = board.files.length % 12;
  const cx = cx0 + cascade * step;
  const cy = cy0 + cascade * step;
  return { x: cx - w / 2, y: cy - h / 2 };
}

/* ==========================================================================
   GRID / FIT / RESIZE
   ========================================================================== */

function arrangeGrid(columns) {
  if (board.files.length === 0) return;
  const gap = board.gridGap;
  if (!columns) columns = Math.max(1, Math.ceil(Math.sqrt(board.files.length)));

  let curX = 0, curY = 0, rowH = 0, col = 0;
  for (const f of board.files) {
    f.rotation = 0;
    f.w = board.defaultWidth;
    f.h = Math.round(f.w * (f.aspect || 1));
    f.userSized = false;
    if (col >= columns) {
      curX = 0;
      curY += rowH + gap;
      rowH = 0;
      col  = 0;
    }
    f.x = curX;
    f.y = curY;
    curX += f.w + gap;
    rowH = Math.max(rowH, f.h);
    col++;
  }
}

function fitAll() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  if (board.files.length === 0) {
    view.zoom = 1;
    view.panX = cw / 2;
    view.panY = ch / 2;
    draw();
    return;
  }

  let minX =  Infinity, minY =  Infinity;
  let maxX = -Infinity, maxY = -Infinity;
  for (const f of board.files) {
    const bb = _cardAABB(f);
    minX = Math.min(minX, bb.x);
    minY = Math.min(minY, bb.y);
    maxX = Math.max(maxX, bb.x + bb.w);
    maxY = Math.max(maxY, bb.y + bb.h);
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

function _boardPointInCard(f, bx, by) {
  const cx = f.x + f.w / 2;
  const cy = f.y + f.h / 2;
  const dx = bx - cx;
  const dy = by - cy;
  const c = Math.cos(-f.rotation), s = Math.sin(-f.rotation);
  const lx = dx * c - dy * s;
  const ly = dx * s + dy * c;
  return Math.abs(lx) <= f.w / 2 && Math.abs(ly) <= f.h / 2;
}

function fileHitTest(sx, sy) {
  const order = _zSortedIndices();
  const [bx, by] = s2b(sx, sy);
  for (let k = order.length - 1; k >= 0; k--) {
    const i = order[k];
    if (_boardPointInCard(board.files[i], bx, by)) return i;
  }
  return -1;
}

function fileCornerHitTest(sx, sy) {
  const order = _zSortedIndices();
  for (let k = order.length - 1; k >= 0; k--) {
    const i = order[k];
    const f = board.files[i];
    for (let c = 0; c < 4; c++) {
      const [cx, cy] = _fileCornerScreen(f, c);
      if (Math.hypot(sx - cx, sy - cy) <= CARD_HIT_R) {
        return { idx: i, corner: c };
      }
    }
  }
  return null;
}

function rotateHandleHitTest(sx, sy) {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length) return false;
  const f = board.files[board.selectedIdx];
  const [hx, hy] = _rotateHandleScreen(f);
  return Math.hypot(sx - hx, sy - hy) <= ROTATE_HANDLE_HR;
}

function _resizeCursorFor(corner) {
  return (corner === 0 || corner === 2) ? "nwse-resize" : "nesw-resize";
}

/* ==========================================================================
   RESIZE — rotation-aware
   ========================================================================== */

function resizeCardFromCorner(f, corner, bx, by,
                              anchorX, anchorY,
                              startAspect, preserveAspect) {
  const [dsx, dsy] = CORNER_SIGNS[corner];
  const ddx = bx - anchorX;
  const ddy = by - anchorY;
  const [lx, ly] = _rotateOffset(ddx, ddy, -f.rotation);

  let rw = dsx * lx;
  let rh = dsy * ly;
  rw = Math.max(MIN_CARD_W, rw);
  rh = Math.max(MIN_CARD_H, rh);

  let nw, nh;
  if (preserveAspect) {
    if (rw * startAspect >= rh) {
      nw = rw;
      nh = rw * startAspect;
    } else {
      nh = rh;
      nw = rh / startAspect;
    }
  } else {
    nw = rw;
    nh = rh;
  }

  const [axRot, ayRot] = _rotateOffset(-dsx * nw / 2, -dsy * nh / 2, f.rotation);
  const ncx = anchorX - axRot;
  const ncy = anchorY - ayRot;

  f.w = nw;
  f.h = nh;
  f.x = ncx - nw / 2;
  f.y = ncy - nh / 2;
}

/* ==========================================================================
   FILE MANAGEMENT
   ========================================================================== */

function addFileByPath(path) {
  if (!path) return -1;
  const norm = String(path);
  for (let i = 0; i < board.files.length; i++) {
    if (board.files[i].path === norm) return i;
  }
  const f = {
    path:       norm,
    normPath:   null,
    name:       _basenameWithoutExt(norm),
    thumb:      null,
    imgUrl:     null,
    img:        null,
    aspect:     1.0,
    docWidth:   0,
    docHeight:  0,
    w:          board.defaultWidth,
    h:          board.defaultWidth,
    x:          0,
    y:          0,
    rotation:   0,
    z:          board.nextZ++,
    userSized:  false,
    color:      null,
    open:       false,
    active:     false,
    modified:   false,
    missing:    false,
    kritaName:  null,
  };
  _assignColor(f);
  const slot = findFreeSlot();
  f.x = slot.x;
  f.y = slot.y;
  board.files.push(f);
  return board.files.length - 1;
}

function removeFile(idx) {
  if (idx < 0 || idx >= board.files.length) return;
  board.files.splice(idx, 1);
  if (board.selectedIdx === idx) board.selectedIdx = -1;
  else if (board.selectedIdx > idx) board.selectedIdx -= 1;
  syncFileList();
  draw();
}

/* ==========================================================================
   RECTANGLE AND POLYGON OVERLAP
   ==========================================================================
   _rectsOverlap is the cheap AABB test used for early filters.

   _polygonsOverlap is the actual test between two rotated cards: it
   returns true iff the two convex quads share any area.  A pair of
   rotated rects whose AABBs overlap but whose edges do not actually
   cross is not an overlap, and nothing should be pasted. */

function _rectsOverlap(x1, y1, w1, h1, x2, y2, w2, h2) {
  return !(x1 + w1 <= x2 || x2 + w2 <= x1 ||
           y1 + h1 <= y2 || y2 + h2 <= y1);
}

function _pointInPoly(p, poly) {
  let inside = false;
  const n = poly.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > p[1]) !== (yj > p[1])) &&
        (p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function _segmentsIntersect(a1, a2, b1, b2) {
  const d1x = a2[0] - a1[0], d1y = a2[1] - a1[1];
  const d2x = b2[0] - b1[0], d2y = b2[1] - b1[1];
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-12) return false;
  const t = ((b1[0] - a1[0]) * d2y - (b1[1] - a1[1]) * d2x) / denom;
  const u = ((b1[0] - a1[0]) * d1y - (b1[1] - a1[1]) * d1x) / denom;
  return t >= 0 && t <= 1 && u >= 0 && u <= 1;
}

function _polygonsOverlap(polyA, polyB) {
  for (const p of polyA) if (_pointInPoly(p, polyB)) return true;
  for (const p of polyB) if (_pointInPoly(p, polyA)) return true;
  const nA = polyA.length, nB = polyB.length;
  for (let i = 0; i < nA; i++) {
    const a1 = polyA[i], a2 = polyA[(i + 1) % nA];
    for (let j = 0; j < nB; j++) {
      const b1 = polyB[j], b2 = polyB[(j + 1) % nB];
      if (_segmentsIntersect(a1, a2, b1, b2)) return true;
    }
  }
  return false;
}

/* ==========================================================================
   PROJECTION TRANSFORM
   ==========================================================================
   Compute the affine map from the SOURCE document's pixel space to
   the TARGET document's pixel space, taking both cards' positions
   and rotations into account.

   The chain is:

       A_doc pixels
           → A card's local unrotated frame    (scale + offset)
           → board space                       (rotate by A, translate)
           → B card's local unrotated frame    (translate, rotate by -B)
           → B_doc pixels                      (scale + offset)

   Both the source and the target images are stretched on the board
   to fill their cards; if the card's aspect has been changed away
   from the document's, that stretch is baked into the transform.
   Whatever the user sees overlapping on the board is what ends up
   pasted into the target document.

   The result is a 2×3 affine map in column-vector form:

       [x_B]   [a c e] [x_A]
       [y_B] = [b d f] [y_A]
       [ 1 ]   [0 0 1] [ 1 ]

   The daemon builds a QTransform out of (a, b, c, d, e, f) and
   applies it to the source document's composite.  QTransform's
   constructor takes (m11, m12, m13, m21, m22, m23, m31, m32, m33);
   mapping to our (a..f) means m11=a, m12=b, m21=c, m22=d, m31=e,
   m32=f, and the rest zero/one. */

function _mat3mul(A, B) {
  // Column-vector 3×3 multiply: returns A · B.
  const C = [[0,0,0],[0,0,0],[0,0,0]];
  for (let i = 0; i < 3; i++) {
    for (let j = 0; j < 3; j++) {
      let s = 0;
      for (let k = 0; k < 3; k++) s += A[i][k] * B[k][j];
      C[i][j] = s;
    }
  }
  return C;
}

function _buildProjectionTransform(A, B) {
  const A_cx = A.x + A.w / 2;
  const A_cy = A.y + A.h / 2;
  const B_cx = B.x + B.w / 2;
  const B_cy = B.y + B.h / 2;
  const A_W = A.docWidth,  A_H = A.docHeight;
  const B_W = B.docWidth,  B_H = B.docHeight;
  const Aw = A.w, Ah = A.h;
  const Bw = B.w, Bh = B.h;
  const cosA = Math.cos(A.rotation), sinA = Math.sin(A.rotation);
  const cosB = Math.cos(B.rotation), sinB = Math.sin(B.rotation);

  // A_doc → A_local:  scale by (Aw/A_W, Ah/A_H), then translate by
  // (-Aw/2, -Ah/2), so A_doc (0,0) → A_local (-Aw/2, -Ah/2).
  const M1 = [
    [Aw / A_W,        0, -Aw / 2],
    [       0, Ah / A_H, -Ah / 2],
    [       0,        0,       1],
  ];

  // A_local → board:  rotate by A.rotation, then translate by
  // (A_cx, A_cy).
  const M2 = [
    [cosA, -sinA, A_cx],
    [sinA,  cosA, A_cy],
    [   0,     0,    1],
  ];

  // board → B_local:  translate by (-B_cx, -B_cy), then rotate by
  // -B.rotation.
  const M3 = [
    [ cosB, sinB, -cosB * B_cx - sinB * B_cy],
    [-sinB, cosB,  sinB * B_cx - cosB * B_cy],
    [    0,    0,                          1],
  ];

  // B_local → B_doc:  translate by (Bw/2, Bh/2), then scale by
  // (B_W/Bw, B_H/Bh).
  const M4 = [
    [B_W / Bw,        0, B_W / 2],
    [       0, B_H / Bh, B_H / 2],
    [       0,        0,       1],
  ];

  let M = _mat3mul(M4, M3);
  M = _mat3mul(M, M2);
  M = _mat3mul(M, M1);

  return {
    a: M[0][0], c: M[0][1], e: M[0][2],
    b: M[1][0], d: M[1][1], f: M[1][2],
  };
}

/* ==========================================================================
   OVERLAP REGIONS
   ==========================================================================
   Given the currently-selected file A, find every other file B whose
   card's rotated rectangle overlaps A's, and compute for each the
   affine transform that maps A's document pixels to B's.

   Overlap is decided on the actual rotated quads, not on their
   AABBs: two cards whose bounding boxes overlap but whose edges do
   not cross have no visual overlap and nothing should be pasted.

   Closed targets are counted (they cannot receive a paste) and
   reported; the caller surfaces the count in the status line. */

function computeProjectionRegions() {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length) {
    return { regions: [], skippedClosed: 0, reason: "no card selected" };
  }
  const A = board.files[board.selectedIdx];

  if (!A.open || !A.docWidth || !A.docHeight) {
    const bits = [];
    if (!A.open)
      bits.push("not open in Krita (press R)");
    if (A.open && (!A.docWidth || !A.docHeight))
      bits.push("document dimensions unknown (press R)");
    return {
      regions: [], skippedClosed: 0,
      reason: "selected card: " + bits.join(", ")
    };
  }

  const A_poly = _cardCornersBoard(A);
  const regions = [];
  let skippedClosed = 0;

  for (let i = 0; i < board.files.length; i++) {
    if (i === board.selectedIdx) continue;
    const B = board.files[i];
    const B_poly = _cardCornersBoard(B);

    if (!_polygonsOverlap(A_poly, B_poly)) continue;
    if (!B.open || !B.docWidth || !B.docHeight) {
      skippedClosed++;
      continue;
    }

    const t = _buildProjectionTransform(A, B);
    regions.push({
      name:            "projection of " + A.name,
      source_document: A.path,
      target_document: B.path,
      a: t.a, b: t.b, c: t.c, d: t.d, e: t.e, f: t.f,
    });
  }

  return { regions: regions, skippedClosed: skippedClosed, reason: "" };
}
"""
