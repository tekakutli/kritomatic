"""
pg_view_flat.py — the parameter-space view of the cone's lateral
surface.

The cone's lateral surface is parameterised by (phi, s).  Drawing the
parameter rectangle flat gives the "2D view".  A patch — being
[phi0, phi1] × [s0, s1] in the model — shows up here as an actual
rectangle, and back on the cone as a curved trapezoid narrowing
toward the apex.

PHI IS A CIRCLE
===============
The phi axis has no boundary.  phi = 0 and phi = 2π are the same
physical line on the cone; the sheet was cut there to lay it flat.
A patch that drifts past the right edge of the sheet is the same
patch as one that re-enters from the left, and the drawing code
reflects that by drawing every integer-2π copy of a drifted patch
whose range overlaps the visible rectangle.

CARDINAL DIRECTION MARKERS
==========================
The four letters E, N, W, S are pinned just above the base line, at
the four φ values that correspond to the world +x, +y, −x, −y
directions of the cone view.  The apex can be dragged anywhere
within the cone's own reach limit, but it does not move the base
ring, so the pins never move.

STAGE 1 OPTIMIZATION — CHROME CACHE
===================================
The static chrome — grid, frame outline, captions, cardinal markers,
s1 line, phi boundary dashed marks — is fully determined by
(layout, flatRect, coneR).  It changes only on resize or on the
half-angle/depth slider.  It used to re-render every frame: a
half-dozen `strokeStyle`/`fillText`/`arc` per grid line, plus the
four cardinal pins.

The chrome is now drawn into an offscreen canvas.  Per-frame key
comparison decides whether to blit or rebuild.  During any drag
(apex, patch, square, scrub), the layout is stable, so the whole
chrome is one `ctx.drawImage`.

`flatRect()` is also memoized: it's called from `flatToScreen`,
`screenToFlat`, `_flatInsideRect`, and every projection that needs
a shape's standing scale — ~80–100 calls per frame.  Five `Math.min`
/ `Math.max` per call, cached on (innerWidth, innerHeight, layout).
"""

FLAT_VIEW_JS = r"""
/* ==========================================================================
   FLAT VIEW CONSTANTS
   ========================================================================== */

const FLAT_PHI_MIN  = 0;
const FLAT_PHI_MAX  = 2 * Math.PI;
const FLAT_PHI_SPAN = FLAT_PHI_MAX - FLAT_PHI_MIN;
const FLAT_S_MIN    = 0;
const FLAT_S_MAX    = 1;

const QUAD_MIN_SIZE_PHI = 0.06;
const QUAD_MIN_SIZE_S   = 0.04;

const PATCH_SNAP_PHI = Math.PI / 12;
const PATCH_SNAP_S   = 0.05;

/* ==========================================================================
   PHI-WRAP HELPERS
   ========================================================================== */

function _phiCopies(phi0, phi1) {
  const kMin = Math.floor((FLAT_PHI_MIN - phi1) / FLAT_PHI_SPAN);
  const kMax = Math.ceil ((FLAT_PHI_MAX - phi0) / FLAT_PHI_SPAN);
  const out = [];
  for (let k = kMin; k <= kMax; k++) out.push(k * FLAT_PHI_SPAN);
  return out;
}

function _phiShiftForPatch(phiCursor, phi0, phi1) {
  const phiMid = (phi0 + phi1) / 2;
  return Math.round((phiMid - phiCursor) / FLAT_PHI_SPAN) * FLAT_PHI_SPAN;
}

/* ==========================================================================
   flatRect — WITH MEMO
   ========================================================================== */

let _flatRectCache = null;

function flatRect() {
  const W = window.innerWidth;
  const H = layout.reservedH;
  const y0Base = layout.reservedY;

  if (_flatRectCache &&
      _flatRectCache.W === W && _flatRectCache.H === H &&
      _flatRectCache.y0Base === y0Base) {
    return _flatRectCache.r;
  }

  const MARGIN_X = 44;
  const TOP_PAD  = 26;
  const BOT_PAD  = 26;
  const maxW = W - 2 * MARGIN_X;
  const maxH = H - TOP_PAD - BOT_PAD - 4;

  const w = Math.min(maxW, maxH * 2.5);
  const h = Math.min(maxH, w / 2.5);
  const x0 = (W - w) / 2;
  const y0 = y0Base + TOP_PAD + (maxH - h) / 2;

  const r = { x0, y0, w, h };
  _flatRectCache = { W, H, y0Base, r };
  return r;
}

function flatToScreen(phi, s) {
  const r = flatRect();
  const u = (phi - FLAT_PHI_MIN) / (FLAT_PHI_MAX - FLAT_PHI_MIN);
  const v = s;
  return [r.x0 + u * r.w, r.y0 + (1 - v) * r.h];
}

function screenToFlat(sx, sy) {
  const r = flatRect();
  return {
    phi: FLAT_PHI_MIN +
         (sx - r.x0) / r.w * (FLAT_PHI_MAX - FLAT_PHI_MIN),
    s:   1 - (sy - r.y0) / r.h,
  };
}

function _flatInsideRect(sx, sy) {
  const r = flatRect();
  return sx >= r.x0 && sx <= r.x0 + r.w &&
         sy >= r.y0 && sy <= r.y0 + r.h;
}

/* ==========================================================================
   FLAT-VIEW CHROME CACHE
   ========================================================================== */

let _flatChromeCache = null;   // { key, canvas }

function _flatChromeKey(cw, ch) {
  const r = flatRect();
  return [
    cw, ch, dpr,
    layout.reservedY, layout.reservedH,
    r.x0, r.y0, r.w, r.h,
    coneR(),
  ].join("|");
}

function _buildFlatChrome(cw, ch) {
  const off = document.createElement("canvas");
  off.width  = Math.round(cw * dpr);
  off.height = Math.round(ch * dpr);
  const octx = off.getContext("2d");
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);

  octx.fillStyle = "#060a10";
  octx.fillRect(0, layout.reservedY, cw, layout.reservedH);

  const r = flatRect();

  // Grid: horizontal ring hints.
  octx.save();
  const N_RINGS = 6;
  for (let k = 1; k < N_RINGS; k++) {
    const s = k / N_RINGS;
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, s);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, s);
    octx.strokeStyle = "rgba(0, 229, 255, " + (0.03 + s * 0.09) + ")";
    octx.lineWidth = 0.7;
    octx.beginPath(); octx.moveTo(sx0, sy); octx.lineTo(sx1, sy); octx.stroke();
  }

  const N_MERID = 12;
  for (let k = 0; k <= N_MERID; k++) {
    const phi = FLAT_PHI_MIN +
                (FLAT_PHI_MAX - FLAT_PHI_MIN) * k / N_MERID;
    const [sx, sy0] = flatToScreen(phi, FLAT_S_MAX);
    const [,   sy1] = flatToScreen(phi, FLAT_S_MIN);
    octx.strokeStyle = "rgba(0, 229, 255, 0.05)";
    octx.lineWidth = 0.7;
    octx.beginPath(); octx.moveTo(sx, sy0); octx.lineTo(sx, sy1); octx.stroke();
  }

  octx.strokeStyle = "#1e2836";
  octx.lineWidth = 1;
  octx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  // s=1 line.
  {
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, 1);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, 1);
    octx.strokeStyle = "rgba(255, 43, 214, 0.55)";
    octx.lineWidth = 2;
    octx.beginPath(); octx.moveTo(sx0, sy); octx.lineTo(sx1, sy); octx.stroke();
  }

  // Dashed phi-boundary marks.
  {
    const [sxL, sy0] = flatToScreen(FLAT_PHI_MIN, FLAT_S_MAX);
    const [,   sy1]  = flatToScreen(FLAT_PHI_MIN, FLAT_S_MIN);
    const [sxR]      = flatToScreen(FLAT_PHI_MAX, 0);
    octx.setLineDash([4, 4]);
    octx.strokeStyle = "rgba(255, 43, 214, 0.30)";
    octx.lineWidth = 1;
    octx.beginPath();
    octx.moveTo(sxL, sy0); octx.lineTo(sxL, sy1);
    octx.moveTo(sxR, sy0); octx.lineTo(sxR, sy1);
    octx.stroke();
    octx.setLineDash([]);
  }
  octx.restore();

  // Captions.
  octx.save();
  octx.font = "700 9px 'JetBrains Mono', 'Fira Code', monospace";

  octx.fillStyle = "#3d4756";
  octx.textAlign = "left"; octx.textBaseline = "top";
  octx.fillText("UNFOLDED CONE  \u00B7  PARAMETER SPACE",
               r.x0, r.y0 - 16);

  octx.textAlign = "right"; octx.fillStyle = "#5a6774";
  octx.fillText("\u03C6 = 0", r.x0 - 6, r.y0);
  octx.textAlign = "left";
  octx.fillText("\u03C6 = 2\u03C0", r.x0 + r.w + 6, r.y0);

  octx.textAlign = "left"; octx.textBaseline = "bottom";
  octx.fillStyle = "#3d4756";
  octx.fillText("s = 0  \u00B7  base", r.x0, r.y0 + r.h + 14);

  octx.textBaseline = "top";
  octx.fillStyle = "rgba(255, 43, 214, 0.75)";
  octx.fillText("s = 1  \u00B7  apex", r.x0, r.y0 + 4);
  octx.restore();

  // Cardinal markers.
  const yBase = r.y0 + r.h;
  const R_PIN = 9;
  const yPin  = yBase - R_PIN - 3;
  const cards = [
    { key: "E", phi: 0               },
    { key: "N", phi: Math.PI / 2     },
    { key: "W", phi: Math.PI         },
    { key: "S", phi: 3 * Math.PI / 2 },
  ];
  octx.save();
  octx.font = "700 11px 'JetBrains Mono', 'Fira Code', monospace";
  octx.textAlign = "center";
  octx.textBaseline = "middle";
  for (const c of cards) {
    const [sx] = flatToScreen(c.phi, 0);

    octx.beginPath();
    octx.moveTo(sx, yBase);
    octx.lineTo(sx, yBase - 4);
    octx.strokeStyle = "rgba(255, 200, 90, 0.35)";
    octx.lineWidth = 1.0;
    octx.stroke();

    octx.beginPath();
    octx.arc(sx, yPin, R_PIN, 0, Math.PI * 2);
    octx.fillStyle = "rgba(11, 16, 24, 0.88)";
    octx.fill();
    octx.strokeStyle = "rgba(255, 200, 90, 0.85)";
    octx.lineWidth = 1.3;
    octx.stroke();

    octx.fillStyle = "rgba(255, 200, 90, 0.95)";
    octx.fillText(c.key, sx, yPin + 0.5);
  }
  octx.restore();

  return off;
}

function _flatChrome(cw, ch) {
  const key = _flatChromeKey(cw, ch);
  if (_flatChromeCache && _flatChromeCache.key === key) {
    return _flatChromeCache.canvas;
  }
  const canvas = _buildFlatChrome(cw, ch);
  _flatChromeCache = { key, canvas };
  return canvas;
}

/* ==========================================================================
   FLAT VIEW — DRAW
   ========================================================================== */

function drawFlatView() {
  const cw = window.innerWidth;

  // Static chrome: blit.
  ctx.drawImage(_flatChrome(cw, window.innerHeight), 0, 0, cw, window.innerHeight);

  const r = flatRect();

  // Dynamic content: patches and squares.
  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.w, r.h);
  ctx.clip();

  for (let i = 0; i < quads.length; i++) {
    if (i === selectedQuad) continue;
    drawFlatQuad(quads[i], false);
  }
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    drawFlatQuad(quads[selectedQuad], true);
  }

  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    const activeQ = quads[selectedQuad];
    for (let i = 0; i < floatSquares.length; i++) {
      const sq = floatSquares[i];
      if (sq.quadId !== activeQ.id) continue;
      const isSel = (i === selectedSquare);
      _drawFlatSquareOne(sq, isSel, false);
      if (activeQ.mirror) {
        _withSquareMirror(sq, () => _drawFlatSquareOne(sq, false, true));
      }
    }
  }

  drawShapeLabelsFlat();

  ctx.restore();
}

function drawFlatQuad(q, selected) {
  const shifts = _phiCopies(q.phi0, q.phi1);
  for (const shift of shifts) {
    drawFlatQuadShifted(q, selected, shift);
  }
}

function drawFlatQuadShifted(q, selected, shift) {
  const hue = patchHue(q);
  const pts = quadCorners(q).map(c =>
    flatToScreen(c.phi + shift, c.s));

  ctx.save();

  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = _huergb(hue, selected ? 0.07 : 0.03);
  ctx.fill();

  ctx.lineJoin = "round";
  ctx.setLineDash([6, 4]);
  ctx.strokeStyle = _huergb(hue, selected ? 0.75 : 0.42);
  ctx.lineWidth = selected ? 1.6 : 1.1;
  ctx.stroke();
  ctx.setLineDash([]);

  if (PATCH_SHAPE_EDIT_ENABLED) {
    const rDot = selected ? 5.0 : 2.8;
    for (const [sx, sy] of pts) {
      ctx.beginPath(); ctx.arc(sx, sy, rDot, 0, Math.PI * 2);
      ctx.fillStyle = selected ? _huergbLight(hue) : _huergb(hue, 0.75);
      ctx.fill();
      ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.4; ctx.stroke();
    }
  }

  ctx.restore();
}

function _drawFlatSquareOne(sq, isSel, isMirror) {
  const corners = squareFlatCorners(sq);
  if (!corners) return;

  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  if (!q) return;
  const hue = patchHue(q);

  const phis = corners.map(c => c[0]);
  const shifts = _phiCopies(Math.min(...phis), Math.max(...phis));

  const fillAlpha   = isMirror ? 0.06 : (isSel ? 0.30 : 0.16);
  const strokeAlpha = isMirror ? 0.55 : (isSel ? 1.00 : 0.78);
  const lineW       = isMirror ? 1.0  : (isSel ? 1.8  : 1.2);

  for (const shift of shifts) {
    const screen = corners.map(([phi, s]) =>
      flatToScreen(phi + shift, s));

    ctx.beginPath();
    ctx.moveTo(screen[0][0], screen[0][1]);
    for (let k = 1; k < 4; k++) {
      ctx.lineTo(screen[k][0], screen[k][1]);
    }
    ctx.closePath();
    ctx.fillStyle = _huergb(hue, fillAlpha);
    ctx.fill();

    ctx.lineJoin = "round";
    ctx.strokeStyle = _huergb(hue, strokeAlpha);
    ctx.lineWidth = lineW;
    ctx.stroke();

    if (sq.slope && Math.abs(sq.slope) > 1e-6) {
      const isect = squarePlaneIntersectionFlat(sq);
      if (isect) {
        const [p0, p1] = isect.map(([phi, s]) =>
          flatToScreen(phi + shift, s));
        ctx.save();
        ctx.setLineDash([4, 3]);
        ctx.strokeStyle = _huergb(hue,
          isMirror ? 0.32 : (isSel ? 0.65 : 0.42));
        ctx.lineWidth   = 1.0;
        ctx.beginPath();
        ctx.moveTo(p0[0], p0[1]);
        ctx.lineTo(p1[0], p1[1]);
        ctx.stroke();
        ctx.restore();
      }
    }

    if (!isSel || isMirror) continue;

    for (const [hx, hy] of screen) {
      ctx.beginPath();
      ctx.arc(hx, hy, 4.2, 0, Math.PI * 2);
      ctx.fillStyle = _huergbLight(hue);
      ctx.fill();
      ctx.strokeStyle = "#0a0e14";
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }

    const rh = squareFlatRotateHandleScreen(sq, shift);
    if (rh) {
      let topIdx = 0;
      for (let k = 1; k < screen.length; k++) {
        if (screen[k][1] < screen[topIdx][1]) topIdx = k;
      }
      ctx.beginPath();
      ctx.moveTo(screen[topIdx][0], screen[topIdx][1]);
      ctx.lineTo(rh[0], rh[1]);
      ctx.strokeStyle = _huergb(hue, 0.45);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.beginPath();
      ctx.arc(rh[0], rh[1], 6.0, 0, Math.PI * 2);
      ctx.fillStyle = _huergbLight(hue);
      ctx.fill();
      ctx.strokeStyle = "#0a0e14";
      ctx.lineWidth = 1.6;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(rh[0], rh[1], 3.0, Math.PI * 0.15, Math.PI * 1.85);
      ctx.strokeStyle = "#0a0e14";
      ctx.lineWidth = 1.2;
      ctx.stroke();
    }
  }
}

/* ==========================================================================
   HIT TESTING
   ========================================================================== */

const FLAT_HANDLE_R = 10;
const FLAT_EDGE_R   = 6;

function _flatPtSegDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const l2 = dx * dx + dy * dy;
  if (l2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function _flatPtInQuad(px, py, c) {
  let inside = false;
  const n = c.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = c[i][0], yi = c[i][1];
    const xj = c[j][0], yj = c[j][1];
    if (((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function flatHitTest(sx, sy) {
  if (!_flatInsideRect(sx, sy)) return null;

  const order = [];
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    order.push(selectedQuad);
  }
  for (let i = 0; i < quads.length; i++) {
    if (i !== selectedQuad) order.push(i);
  }

  for (const qi of order) {
    const q = quads[qi];
    const shifts = _phiCopies(q.phi0, q.phi1);

    for (const shift of shifts) {
      const corners = quadCorners(q).map(c =>
        flatToScreen(c.phi + shift, c.s));

      for (let ci = 0; ci < 4; ci++) {
        const [csx, csy] = corners[ci];
        if (Math.hypot(sx - csx, sy - csy) < FLAT_HANDLE_R) {
          return { kind: "corner", quadIdx: qi, cornerIdx: ci };
        }
      }

      const edges = [
        { name: "top",    a: corners[0], b: corners[1] },
        { name: "right",  a: corners[1], b: corners[2] },
        { name: "bottom", a: corners[2], b: corners[3] },
        { name: "left",   a: corners[3], b: corners[0] },
      ];
      let bestEdge = null, bestD = FLAT_EDGE_R;
      for (const e of edges) {
        const d = _flatPtSegDist(sx, sy, e.a[0], e.a[1], e.b[0], e.b[1]);
        if (d < bestD) { bestD = d; bestEdge = e.name; }
      }
      if (bestEdge) return { kind: "edge", quadIdx: qi, edge: bestEdge };

      if (_flatPtInQuad(sx, sy, corners)) {
        return { kind: "body", quadIdx: qi };
      }
    }
  }
  return null;
}

/* ==========================================================================
   SHAPE RE-CLAMP AFTER PATCH MUTATION
   ========================================================================== */

function _reclampShapesOnPatch(q) {
  if (!q) return;
  const qi = quadIdxById(q.id);
  if (qi < 0) return;
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    if (sq.quadId === q.id) {
      sq.v = clampCloneV(qi, sq.v);
    }
  }
}

/* ==========================================================================
   PATCH DRAG AND MUTATORS
   ========================================================================== */

function applyPatchDragFromStart(q, dp, curPhi, curS, constrain) {
  let rawPhi = curPhi - dp.startPhi;
  while (rawPhi >  Math.PI) rawPhi -= 2 * Math.PI;
  while (rawPhi < -Math.PI) rawPhi += 2 * Math.PI;
  const rawS = curS - dp.startS;

  let dPhi = rawPhi;
  const dS = rawS;

  if (constrain) {
    dPhi = 0;
  }

  q.phi0 = dp.startPhi0 + dPhi;
  q.phi1 = dp.startPhi1 + dPhi;

  let ns0 = dp.startS0 + dS;
  let ns1 = dp.startS1 + dS;
  if (ns0 < FLAT_S_MIN) { ns1 += FLAT_S_MIN - ns0; ns0 = FLAT_S_MIN; }
  if (ns1 > FLAT_S_MAX) { ns0 -= ns1 - FLAT_S_MAX; ns1 = FLAT_S_MAX; }
  if (ns0 < FLAT_S_MIN) ns0 = FLAT_S_MIN;
  q.s0 = ns0;
  q.s1 = ns1;

  _reclampShapesOnPatch(q);
}

function flatMoveCorner(q, cornerIdx, phi, s) {
  if (cornerIdx === 0) {
    q.phi0 = Math.max(FLAT_PHI_MIN,
                      Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
    q.s1   = Math.min(FLAT_S_MAX,
                      Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 1) {
    q.phi1 = Math.min(FLAT_PHI_MAX,
                      Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
    q.s1   = Math.min(FLAT_S_MAX,
                      Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 2) {
    q.phi1 = Math.min(FLAT_PHI_MAX,
                      Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
    q.s0   = Math.max(FLAT_S_MIN,
                      Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 3) {
    q.phi0 = Math.max(FLAT_PHI_MIN,
                      Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
    q.s0   = Math.max(FLAT_S_MIN,
                      Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  }
  _reclampShapesOnPatch(q);
}

function flatMoveEdge(q, edge, phi, s) {
  if (edge === "top") {
    q.s1 = Math.min(FLAT_S_MAX, Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (edge === "bottom") {
    q.s0 = Math.max(FLAT_S_MIN, Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  } else if (edge === "left") {
    q.phi0 = Math.max(FLAT_PHI_MIN,
                      Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
  } else if (edge === "right") {
    q.phi1 = Math.min(FLAT_PHI_MAX,
                      Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
  }
  _reclampShapesOnPatch(q);
}

function flatMoveBody(q, dPhi, dS) {
  q.phi0 += dPhi;
  q.phi1 += dPhi;

  let ns0 = q.s0 + dS, ns1 = q.s1 + dS;
  if (ns0 < FLAT_S_MIN) { ns1 += FLAT_S_MIN - ns0; ns0 = FLAT_S_MIN; }
  if (ns1 > FLAT_S_MAX) { ns0 -= ns1 - FLAT_S_MAX; ns1 = FLAT_S_MAX; }
  if (ns0 < FLAT_S_MIN) ns0 = FLAT_S_MIN;

  q.s0 = ns0; q.s1 = ns1;

  _reclampShapesOnPatch(q);
}
"""
