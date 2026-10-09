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

Everything drawn inside the sheet — patches, their corner markers,
the floating shapes, their handles — is clipped to the parameter
rectangle.

CARDINAL DIRECTION MARKERS
==========================
The four letters E, N, W, S are pinned just above the base line
(s = 0), at the four φ values that correspond to the world +x, +y,
−x, −y directions of the cone view:

    E   φ = 0          world +x     (screen right in the cone view)
    N   φ = π/2        world +y     (screen up    in the cone view)
    W   φ = π          world −x     (screen left  in the cone view)
    S   φ = 3π/2       world −y     (screen down  in the cone view)

Their positions are fixed: the base ring is centred at the origin
with radius coneR(), and the four cardinal points sit at (R, 0),
(0, R), (−R, 0), (0, −R) respectively, independent of where the
apex is dragged.  The apex moves the meridians (which all converge
on the apex) but it does not move the base ring, so the markers stay
put as the cone is tilted.

The markers exist to answer a specific question: "I am looking at
the flat parameter sheet, but I know the shape I care about is
oriented a particular way in the cone view — which way is that on
the sheet?"  The meridian line at each of the four labelled φ
values runs straight up from the labelled base point to the apex,
and it is the line along which the corresponding visual direction
lives.  Any intermediate direction (NE, SSW, and so on) is read off
the same φ axis: the meridian at a given φ has world angle φ on
the base ring, which is its visual direction in the cone view.

SHAPES IN THE FLAT VIEW
=======================
A shape lives on the patch's plane, not on the cone surface, so it
has no intrinsic (phi, s) footprint.  When a patch is active, every
shape of that patch is projected into the flat view by the linear
correspondence (u, v) → (phi, s) that keeps the patch's own corners
at the patch's own phi/s bounds:

    phi = phiC + (u / uLen) · (phi1 − phi0)
    s   = sC   + (v / vLen) · (s1   − s0)

PATCH VERSUS SHAPE
==================
Both are quadrilaterals, both are tinted by the patch's hue, both
carry a name label.  The distinguishing feature is the STROKE:

    patch   dashed outline
    shape   solid outline (thicker when selected), corner handles,
            rotate handle

The dashed stroke reads as "this is the surface the shapes live on",
which is what the patch is now.  It matches the patch's rendering in
the cone view.

WRAPPED COPIES AND HANDLES
==========================
A shape whose patch has drifted past the seam draws multiple
integer-2π copies.  The polygon is drawn once per copy; the rotate
handle is drawn once per copy as well, using the same shift that the
polygon copy used.

PATCH DRAG — START-ANCHORED, WITH SHIFT-TO-APEX
================================================
A patch drag records the cursor's start position (phi, s), the
patch's own start bounds (phi0, phi1, s0, s1), and applies the
delta from drag start on every frame.  The drag handler therefore
never reads the patch's current position as a source of truth for
where it should be — the position is always recomputed from the
start, so a wrap cannot accumulate error.

With Shift held, the drag is constrained to the direction from the
patch's centre toward the apex.  On the cone's lateral surface that
is the meridian, so the constraint drops the φ component of the
delta and lets s move freely in both directions.

phi is unwrapped into (−π, π] on each frame so a drag across the
atan2 seam does not add or drop a full turn.  s is bounded to
[FLAT_S_MIN, FLAT_S_MAX] with the same push-on-contact behaviour
flatMoveBody uses: when the patch's edge hits a wall, the whole
interval slides and the far edge advances.

PATCH MOVES AND THE HORIZON
===========================
Moving a patch along s is not a rigid motion of everything on it.
The patch's own s0 and s1 shift, which moves the horizon in
normalized v̂ terms; a shape whose sq.v was inside the old band may
sit past the new one.  In the cone view this is handled by the
horizon clip; in the flat view the shape's sq.v is simply clamped to
the raw band so it does not fall off the sheet.

PLANE INTERSECTION
==================
A tilted shape's plane cuts its patch plane along a line; the
helpers in pg_view_squares.py (squarePlaneIntersectionFlat) return
the chord in (phi, s), and the drawing loop here renders it per
wrapped copy with the same faint dashed style the cone band uses.

LABELS
======
Patch and shape name labels are drawn at the end of drawFlatView,
inside the clip block, so a wrapped copy that has drifted past the
sheet boundary is trimmed along with everything else.

MIRROR
======
A patch whose `mirror` flag is true draws its shapes a second time
on the patch's mirror — the same patch shifted by π in φ.  The
mirror copies are drawn dimmed, without handles, and are not hit-
testable.  See pg_view_squares.py for the mirror helpers.

Interaction on this band:

    shape corner / rotate / body drag   edit that shape
    patch body drag                     translate the patch
                                        (wraps freely across phi;
                                        Shift held snaps to the
                                        centre→apex axis)
    empty space click                   deselect
"""


FLAT_VIEW_JS = r"""
/* ==========================================================================
   FLAT VIEW
   ========================================================================== */

const FLAT_PHI_MIN  = 0;
const FLAT_PHI_MAX  = 2 * Math.PI;
const FLAT_PHI_SPAN = FLAT_PHI_MAX - FLAT_PHI_MIN;
const FLAT_S_MIN    = 0;
const FLAT_S_MAX    = 1;

const QUAD_MIN_SIZE_PHI = 0.06;
const QUAD_MIN_SIZE_S   = 0.04;

/* ==========================================================================
   PATCH DRAG SNAP STEPS
   ==========================================================================
   PATCH_SNAP_PHI — one fifteenth of a full turn, matching the default
   meridian spacing of 24 (2π / 24 = 15°).  A patch snapped with
   Shift advances in meridian-sized increments along phi.

   PATCH_SNAP_S   — one twentieth of the parameter rectangle's height.
   For a patch whose own s-extent is 0.5, this is one tenth of its
   own height; the eye reads the jump as "a small but visible step". */

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

function flatRect() {
  const W = window.innerWidth;
  const H = layout.reservedH;
  const MARGIN_X = 44;
  const TOP_PAD  = 26;
  const BOT_PAD  = 26;
  const maxW = W - 2 * MARGIN_X;
  const maxH = H - TOP_PAD - BOT_PAD - 4;

  const w = Math.min(maxW, maxH * 2.5);
  const h = Math.min(maxH, w / 2.5);
  const x0 = (W - w) / 2;
  const y0 = layout.reservedY + TOP_PAD + (maxH - h) / 2;
  return { x0, y0, w, h };
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

function drawFlatView() {
  const cw = window.innerWidth;
  ctx.fillStyle = "#060a10";
  ctx.fillRect(0, layout.reservedY, cw, layout.reservedH);

  const r = flatRect();

  /* ---- static chrome: grid, frame, captions --------------------- */
  ctx.save();

  const N_RINGS = 6;
  for (let k = 1; k < N_RINGS; k++) {
    const s = k / N_RINGS;
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, s);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, s);
    ctx.strokeStyle = "rgba(0, 229, 255, " + (0.03 + s * 0.09) + ")";
    ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(sx0, sy); ctx.lineTo(sx1, sy); ctx.stroke();
  }

  const N_MERID = 12;
  for (let k = 0; k <= N_MERID; k++) {
    const phi = FLAT_PHI_MIN +
                (FLAT_PHI_MAX - FLAT_PHI_MIN) * k / N_MERID;
    const [sx, sy0] = flatToScreen(phi, FLAT_S_MAX);
    const [,   sy1] = flatToScreen(phi, FLAT_S_MIN);
    ctx.strokeStyle = "rgba(0, 229, 255, 0.05)";
    ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(sx, sy0); ctx.lineTo(sx, sy1); ctx.stroke();
  }

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  {
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, 1);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, 1);
    ctx.strokeStyle = "rgba(255, 43, 214, 0.55)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(sx0, sy); ctx.lineTo(sx1, sy); ctx.stroke();
  }

  {
    const [sxL, sy0] = flatToScreen(FLAT_PHI_MIN, FLAT_S_MAX);
    const [,   sy1]  = flatToScreen(FLAT_PHI_MIN, FLAT_S_MIN);
    const [sxR]      = flatToScreen(FLAT_PHI_MAX, 0);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "rgba(255, 43, 214, 0.30)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sxL, sy0); ctx.lineTo(sxL, sy1);
    ctx.moveTo(sxR, sy0); ctx.lineTo(sxR, sy1);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.restore();

  /* captions */
  ctx.save();
  ctx.font = "700 9px 'JetBrains Mono', 'Fira Code', monospace";

  ctx.fillStyle = "#3d4756";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText("UNFOLDED CONE  \u00B7  PARAMETER SPACE",
               r.x0, r.y0 - 16);

  ctx.textAlign = "right"; ctx.fillStyle = "#5a6774";
  ctx.fillText("\u03C6 = 0", r.x0 - 6, r.y0);
  ctx.textAlign = "left";
  ctx.fillText("\u03C6 = 2\u03C0", r.x0 + r.w + 6, r.y0);

  ctx.textAlign = "left"; ctx.textBaseline = "bottom";
  ctx.fillStyle = "#3d4756";
  ctx.fillText("s = 0  \u00B7  base", r.x0, r.y0 + r.h + 14);

  ctx.textBaseline = "top";
  ctx.fillStyle = "rgba(255, 43, 214, 0.75)";
  ctx.fillText("s = 1  \u00B7  apex", r.x0, r.y0 + 4);
  ctx.restore();

  /* ---- patches and shapes, together, clipped to the sheet ------- */

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
        _withQuadPhiShifted(activeQ, Math.PI,
          () => _drawFlatSquareOne(sq, false, true));
      }
    }
  }

  /* Labels, drawn inside the clip so a wrapped copy that has drifted
     past the sheet boundary is trimmed along with everything else. */
  drawShapeLabelsFlat();

  /* Cardinal direction markers, drawn last so they sit above
     everything on the sheet.  See the module docstring,
     CARDINAL DIRECTION MARKERS. */
  drawFlatCardinalMarkers();

  ctx.restore();
}

/* ==========================================================================
   CARDINAL DIRECTION MARKERS
   ==========================================================================
   Small pins at the base line, at φ = 0, π/2, π, 3π/2.  Each pin is
   a dark disc with an amber ring and a single letter, E / N / W / S,
   sitting just above the s = 0 line.

   The four base points are fixed at (R, 0), (0, R), (−R, 0), (0, −R)
   in world coordinates, where R = coneR().  The apex can be dragged
   anywhere within the cone's own reach limit, but it does not move
   the base ring, so the pins never move.

   Drawn without a save/restore of its own — it inherits the clip
   and the coordinate system of drawFlatView. */

function drawFlatCardinalMarkers() {
  const r     = flatRect();
  const yBase = r.y0 + r.h;

  const R_PIN = 9;
  const yPin  = yBase - R_PIN - 3;

  const cards = [
    { key: "E", phi: 0               },
    { key: "N", phi: Math.PI / 2     },
    { key: "W", phi: Math.PI         },
    { key: "S", phi: 3 * Math.PI / 2 },
  ];

  ctx.save();
  ctx.font = "700 11px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  for (const c of cards) {
    const [sx] = flatToScreen(c.phi, 0);

    /* Short stem connecting the pin to the base line, so the pin
       reads as "anchored here on the s = 0 edge". */
    ctx.beginPath();
    ctx.moveTo(sx, yBase);
    ctx.lineTo(sx, yBase - 4);
    ctx.strokeStyle = "rgba(255, 200, 90, 0.35)";
    ctx.lineWidth = 1.0;
    ctx.stroke();

    /* Pin: dark fill so it reads over any underlying shape, amber
       ring and letter for the marker itself. */
    ctx.beginPath();
    ctx.arc(sx, yPin, R_PIN, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(11, 16, 24, 0.88)";
    ctx.fill();
    ctx.strokeStyle = "rgba(255, 200, 90, 0.85)";
    ctx.lineWidth = 1.3;
    ctx.stroke();

    ctx.fillStyle = "rgba(255, 200, 90, 0.95)";
    ctx.fillText(c.key, sx, yPin + 0.5);
  }

  ctx.restore();
}

function drawFlatQuad(q, selected) {
  const shifts = _phiCopies(q.phi0, q.phi1);
  for (const shift of shifts) {
    drawFlatQuadShifted(q, selected, shift);
  }
}

/* The patch is the plane a shape sits on.  It gets a DASHED outline,
   a very faint fill, and no corner or rotate handles.  Everything
   the eye reads as "an object on the sheet" — solid stroke, handles,
   a filled body — belongs to the shapes.

   When shape-editing is enabled the patch corners carry small dots
   drawn in the patch hue; those dots are the only handle-like
   feature a patch has, and they are dormant while the flag is off. */
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

/* ==========================================================================
   PER-SQUARE DRAWING IN THE FLAT BAND
   ==========================================================================
   Extracted so both the original and the mirror copy can be drawn
   through the same code.  The mirror copy is dimmed and carries no
   handles; the original carries handles when it is the selected
   square.  The wrapping by `_phiCopies` and the intersection hint
   are the same in both cases. */

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

    /* Intersection with the patch plane, when tilted.  Drawn
       per wrapped copy, same as the polygon and its handles. */
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

/* ---- Hit testing ------------------------------------------------ */

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

/* ---- Shape re-clamp after a patch mutation ---------------------- */

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

/* ---- Patch drag -------------------------------------------------
   Start-anchored, with the Shift-held constraint to the patch's
   centre→apex axis.

   On the cone's lateral surface, the direction from a patch's
   centre toward the apex is the meridian: φ stays put, only s
   moves.  Constraining the drag to that axis therefore drops the φ
   component of the delta and lets s move freely in both directions
   — the axis is fixed, the sign is not.

   Both the cone view's drag handler and the flat view's route
   through this same function, so a constrained patch drag behaves
   identically in both bands. */

function applyPatchDragFromStart(q, dp, curPhi, curS, constrain) {
  let rawPhi = curPhi - dp.startPhi;
  while (rawPhi >  Math.PI) rawPhi -= 2 * Math.PI;
  while (rawPhi < -Math.PI) rawPhi += 2 * Math.PI;
  const rawS = curS - dp.startS;

  let dPhi = rawPhi;
  const dS = rawS;

  /* Shift held: the drag is constrained to the direction from the
     patch's centre toward the apex.  On the cone surface that is
     the meridian — φ stays put, only s moves.  Both directions
     along the meridian are allowed; the axis is fixed, not the
     sign. */
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

/* ---- Drag application -------------------------------------------
   The three mutators below are the shape-editing paths for patches.
   They are unreferenced while PATCH_SHAPE_EDIT_ENABLED is false, but
   they remain fully functional. */

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

/* Incremental patch translation.  Kept as the low-level mutator and
   as the reference implementation for the clamp/wrap behaviour that
   applyPatchDragFromStart mirrors; the drag handlers now use the
   start-anchored version. */
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
