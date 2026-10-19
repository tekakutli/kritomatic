"""
pg_core.py — canvas, view, board state, item model.

All exports.  `dpr` is a `let` mutated only by pg_core's `resize`;
importers read it (live binding).  `_scheduleDraw` is used only
inside pg_core.
"""

CORE_JS = r"""
export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");
export let dpr      = window.devicePixelRatio || 1;

export const view = { zoom: 1, panX: 0, panY: 0 };

export function b2s(bx, by) {
  return [bx * view.zoom + view.panX, by * view.zoom + view.panY];
}
export function s2b(sx, sy) {
  return [(sx - view.panX) / view.zoom, (sy - view.panY) / view.zoom];
}

export const board = {
  files:        [],
  selectedIdx:  -1,
  gridGap:      24,
  defaultWidth: 320,
  nextZ:        1,
  palette: [
    { r: 255, g: 200, b:  90 }, { r: 120, g: 220, b: 255 },
    { r: 255, g: 130, b: 210 }, { r: 140, g: 225, b: 160 },
    { r: 180, g: 155, b: 255 }, { r: 255, g: 165, b: 105 },
    { r: 110, g: 220, b: 210 }, { r: 220, g: 230, b: 120 },
  ],
};

export const CARD_HANDLE_R    = 4.5;
export const CARD_HIT_R       = 11;
export const ROTATE_HANDLE_R  = 6;
export const ROTATE_HANDLE_HR = 10;
export const ROTATE_HANDLE_OFFSET = 26;
export const MIN_CARD_W       = 40;
export const MIN_CARD_H       = 30;

export const CORNER_SIGNS = [[-1, -1], [1, -1], [1, 1], [-1, 1]];

export function _rgba(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}
export function _rgbaLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

function _assignColor(file) {
  if (file.color) return;
  let hash = 0;
  const s = file.path || file.name || "";
  for (let i = 0; i < s.length; i++) hash = ((hash << 5) - hash + s.charCodeAt(i)) | 0;
  file.color = board.palette[Math.abs(hash) % board.palette.length];
}

function _basenameWithoutExt(path) {
  if (!path) return "(untitled)";
  const norm = String(path).replace(/\\/g, "/");
  const base = norm.substring(norm.lastIndexOf("/") + 1);
  const dot  = base.lastIndexOf(".");
  return dot > 0 ? base.substring(0, dot) : base;
}

export function fileState(f) {
  if (f.missing)   return "missing";
  if (f.open)      return "open";
  return "closed";
}

export function _rectsOverlap(x1, y1, w1, h1, x2, y2, w2, h2) {
  return !(x1 + w1 <= x2 || x2 + w2 <= x1 || y1 + h1 <= y2 || y2 + h2 <= y1);
}

export function _rotateOffset(dx, dy, angle) {
  const c = Math.cos(angle), s = Math.sin(angle);
  return [dx * c - dy * s, dx * s + dy * c];
}

export function _cardCornersBoard(f) {
  const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
  const hw = f.w / 2, hh = f.h / 2;
  return CORNER_SIGNS.map(([sx, sy]) => {
    const [rx, ry] = _rotateOffset(sx * hw, sy * hh, f.rotation);
    return [cx + rx, cy + ry];
  });
}

export function _fileCornerScreen(f, corner) {
  const [bx, by] = _cardCornersBoard(f)[corner];
  return b2s(bx, by);
}

export function _rotateHandleScreen(f) {
  const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
  const screenOffset = (f.h * view.zoom) / 2 + ROTATE_HANDLE_OFFSET;
  const [csx, csy] = b2s(cx, cy);
  return [csx + screenOffset * Math.sin(f.rotation),
          csy - screenOffset * Math.cos(f.rotation)];
}

export function _cardAABB(f) {
  const corners = _cardCornersBoard(f);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [x, y] of corners) {
    if (x < minX) minX = x; if (y < minY) minY = y;
    if (x > maxX) maxX = x; if (y > maxY) maxY = y;
  }
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

export function _zSortedIndices() {
  const idx = [];
  for (let i = 0; i < board.files.length; i++) idx.push(i);
  idx.sort((a, b) => (board.files[a].z || 0) - (board.files[b].z || 0));
  return idx;
}

export function bringToTop(idx) {
  if (idx < 0 || idx >= board.files.length) return;
  board.files[idx].z = board.nextZ++;
}

let _drawScheduled = false;
export function _scheduleDraw() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => { _drawScheduled = false; _callDraw(); });
  } else {
    setTimeout(() => { _drawScheduled = false; _callDraw(); }, 16);
  }
}
let _drawRef = null;
export function _setDraw(fn) { _drawRef = fn; }
function _callDraw() { if (_drawRef) _drawRef(); }

export function ensureThumbLoaded(file) {
  if (!file.thumb) return null;
  if (file.img && file.imgUrl === file.thumb) return file.img;
  const img = new Image();
  img.onload  = _scheduleDraw;
  img.onerror = _scheduleDraw;
  img.src = file.thumb;
  file.img = img;
  file.imgUrl = file.thumb;
  return img;
}

export function findFreeSlot() {
  const cw = window.innerWidth, ch = window.innerHeight;
  const w = board.defaultWidth, h = board.defaultWidth;
  const [cx0, cy0] = s2b(cw / 2, ch / 2);
  const step = 22;
  const cascade = board.files.length % 12;
  const cx = cx0 + cascade * step;
  const cy = cy0 + cascade * step;
  return { x: cx - w / 2, y: cy - h / 2 };
}

export function arrangeGrid(columns) {
  if (board.files.length === 0) return;
  const gap = board.gridGap;
  if (!columns) columns = Math.max(1, Math.ceil(Math.sqrt(board.files.length)));

  let curX = 0, curY = 0, rowH = 0, col = 0;
  for (const f of board.files) {
    f.rotation = 0;
    f.w = board.defaultWidth;
    f.h = Math.round(f.w * (f.aspect || 1));
    f.userSized = false;
    if (col >= columns) { curX = 0; curY += rowH + gap; rowH = 0; col = 0; }
    f.x = curX; f.y = curY;
    curX += f.w + gap;
    rowH = Math.max(rowH, f.h);
    col++;
  }
}

export function fitAll() {
  const cw = window.innerWidth, ch = window.innerHeight;
  if (board.files.length === 0) {
    view.zoom = 1; view.panX = cw / 2; view.panY = ch / 2;
    _callDraw();
    return;
  }

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const f of board.files) {
    const bb = _cardAABB(f);
    minX = Math.min(minX, bb.x); minY = Math.min(minY, bb.y);
    maxX = Math.max(maxX, bb.x + bb.w); maxY = Math.max(maxY, bb.y + bb.h);
  }

  const pad = 60;
  const bw = (maxX - minX) + pad * 2;
  const bh = (maxY - minY) + pad * 2;
  const z = Math.min(cw / bw, ch / bh);
  view.zoom = Math.max(0.05, Math.min(8, z));
  view.panX = (cw - (maxX + minX) * view.zoom) / 2;
  view.panY = (ch - (maxY + minY) * view.zoom) / 2;
  _callDraw();
}

export function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  _callDraw();
}
window.addEventListener("resize", resize);

export function _boardPointInCard(f, bx, by) {
  const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
  const dx = bx - cx, dy = by - cy;
  const c = Math.cos(-f.rotation), s = Math.sin(-f.rotation);
  const lx = dx * c - dy * s, ly = dx * s + dy * c;
  return Math.abs(lx) <= f.w / 2 && Math.abs(ly) <= f.h / 2;
}

export function fileHitTest(sx, sy) {
  const order = _zSortedIndices();
  const [bx, by] = s2b(sx, sy);
  for (let k = order.length - 1; k >= 0; k--) {
    const i = order[k];
    if (_boardPointInCard(board.files[i], bx, by)) return i;
  }
  return -1;
}

export function fileCornerHitTest(sx, sy) {
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

export function rotateHandleHitTest(sx, sy) {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length) return false;
  const f = board.files[board.selectedIdx];
  const [hx, hy] = _rotateHandleScreen(f);
  return Math.hypot(sx - hx, sy - hy) <= ROTATE_HANDLE_HR;
}

export function _resizeCursorFor(corner) {
  return (corner === 0 || corner === 2) ? "nwse-resize" : "nesw-resize";
}

export function _hoverCursorAt(sx, sy) {
  if (rotateHandleHitTest(sx, sy)) return "grab";

  const [bx, by] = s2b(sx, sy);
  let bestCornerZ = -Infinity, bestCorner = -1, bestBodyZ = -Infinity;

  for (let i = 0; i < board.files.length; i++) {
    const f = board.files[i];
    const z = f.z || 0;
    const csx =  (f.x + f.w / 2) * view.zoom + view.panX;
    const csy = -(f.y + f.h / 2) * view.zoom + view.panY;
    const halfDiag = (Math.hypot(f.w, f.h) / 2) * view.zoom;
    if (Math.hypot(sx - csx, sy - csy) > halfDiag + CARD_HIT_R) continue;

    if (z > bestCornerZ) {
      for (let c = 0; c < 4; c++) {
        const [cx, cy] = _fileCornerScreen(f, c);
        if (Math.hypot(sx - cx, sy - cy) <= CARD_HIT_R) {
          bestCornerZ = z; bestCorner = c; break;
        }
      }
    }
    if (z > bestBodyZ && _boardPointInCard(f, bx, by)) bestBodyZ = z;
  }

  if (bestCorner >= 0) return _resizeCursorFor(bestCorner);
  if (bestBodyZ > -Infinity) return "grab";
  return "default";
}

export function resizeCardFromCorner(f, corner, bx, by,
                                     anchorX, anchorY, startAspect, preserveAspect) {
  const [dsx, dsy] = CORNER_SIGNS[corner];
  const ddx = bx - anchorX, ddy = by - anchorY;
  const [lx, ly] = _rotateOffset(ddx, ddy, -f.rotation);

  let rw = dsx * lx, rh = dsy * ly;
  rw = Math.max(MIN_CARD_W, rw); rh = Math.max(MIN_CARD_H, rh);

  let nw, nh;
  if (preserveAspect) {
    if (rw * startAspect >= rh) { nw = rw; nh = rw * startAspect; }
    else { nh = rh; nw = rh / startAspect; }
  } else { nw = rw; nh = rh; }

  const [axRot, ayRot] = _rotateOffset(-dsx * nw / 2, -dsy * nh / 2, f.rotation);
  const ncx = anchorX - axRot, ncy = anchorY - ayRot;
  f.w = nw; f.h = nh;
  f.x = ncx - nw / 2;
  f.y = ncy - nh / 2;
}

export function addFileByPath(path) {
  if (!path) return -1;
  const norm = String(path);
  for (let i = 0; i < board.files.length; i++) if (board.files[i].path === norm) return i;

  const f = {
    path: norm, normPath: null, name: _basenameWithoutExt(norm),
    thumb: null, imgUrl: null, img: null,
    aspect: 1.0, docWidth: 0, docHeight: 0,
    w: board.defaultWidth, h: board.defaultWidth,
    x: 0, y: 0, rotation: 0, z: board.nextZ++, userSized: false,
    color: null, open: false, active: false, modified: false,
    missing: false, kritaName: null,
  };
  _assignColor(f);
  const slot = findFreeSlot();
  f.x = slot.x; f.y = slot.y;
  board.files.push(f);
  return board.files.length - 1;
}

export function removeFile(idx) {
  if (idx < 0 || idx >= board.files.length) return;
  board.files.splice(idx, 1);
  if (board.selectedIdx === idx) board.selectedIdx = -1;
  else if (board.selectedIdx > idx) board.selectedIdx -= 1;
}

export function _pointInPoly(p, poly) {
  let inside = false;
  const n = poly.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > p[1]) !== (yj > p[1])) &&
        (p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

export function _segmentsIntersect(a1, a2, b1, b2) {
  const d1x = a2[0] - a1[0], d1y = a2[1] - a1[1];
  const d2x = b2[0] - b1[0], d2y = b2[1] - b1[1];
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-12) return false;
  const t = ((b1[0] - a1[0]) * d2y - (b1[1] - a1[1]) * d2x) / denom;
  const u = ((b1[0] - a1[0]) * d1y - (b1[1] - a1[1]) * d1x) / denom;
  return t >= 0 && t <= 1 && u >= 0 && u <= 1;
}

export function _polygonsOverlap(polyA, polyB) {
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

function _mat3mul(A, B) {
  const C = [[0,0,0],[0,0,0],[0,0,0]];
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
    let s = 0;
    for (let k = 0; k < 3; k++) s += A[i][k] * B[k][j];
    C[i][j] = s;
  }
  return C;
}

function _buildProjectionTransform(A, B) {
  const A_cx = A.x + A.w / 2, A_cy = A.y + A.h / 2;
  const B_cx = B.x + B.w / 2, B_cy = B.y + B.h / 2;
  const A_W = A.docWidth, A_H = A.docHeight;
  const B_W = B.docWidth, B_H = B.docHeight;
  const Aw = A.w, Ah = A.h, Bw = B.w, Bh = B.h;
  const cosA = Math.cos(A.rotation), sinA = Math.sin(A.rotation);
  const cosB = Math.cos(B.rotation), sinB = Math.sin(B.rotation);

  const M1 = [[Aw / A_W, 0, -Aw / 2], [0, Ah / A_H, -Ah / 2], [0, 0, 1]];
  const M2 = [[cosA, -sinA, A_cx], [sinA, cosA, A_cy], [0, 0, 1]];
  const M3 = [[cosB, sinB, -cosB * B_cx - sinB * B_cy],
              [-sinB, cosB, sinB * B_cx - cosB * B_cy],
              [0, 0, 1]];
  const M4 = [[B_W / Bw, 0, B_W / 2], [0, B_H / Bh, B_H / 2], [0, 0, 1]];

  let M = _mat3mul(M4, M3);
  M = _mat3mul(M, M2);
  M = _mat3mul(M, M1);
  return {
    a: M[0][0], c: M[0][1], e: M[0][2],
    b: M[1][0], d: M[1][1], f: M[1][2],
  };
}

export function computeProjectionRegions() {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length) {
    return { regions: [], skippedClosed: 0, reason: "no card selected" };
  }
  const A = board.files[board.selectedIdx];
  if (!A.open || !A.docWidth || !A.docHeight) {
    const bits = [];
    if (!A.open) bits.push("not open in Krita (press R)");
    if (A.open && (!A.docWidth || !A.docHeight)) bits.push("document dimensions unknown (press R)");
    return { regions: [], skippedClosed: 0, reason: "selected card: " + bits.join(", ") };
  }

  const A_poly = _cardCornersBoard(A);
  const regions = [];
  let skippedClosed = 0;

  for (let i = 0; i < board.files.length; i++) {
    if (i === board.selectedIdx) continue;
    const B = board.files[i];
    const B_poly = _cardCornersBoard(B);
    if (!_polygonsOverlap(A_poly, B_poly)) continue;
    if (!B.open || !B.docWidth || !B.docHeight) { skippedClosed++; continue; }

    const t = _buildProjectionTransform(A, B);
    regions.push({
      name: "projection of " + A.name,
      source_document: A.path,
      target_document: B.path,
      a: t.a, b: t.b, c: t.c, d: t.d, e: t.e, f: t.f,
    });
  }
  return { regions, skippedClosed, reason: "" };
}
"""
