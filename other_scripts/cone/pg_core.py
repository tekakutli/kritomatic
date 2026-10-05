"""
pg_core.py — canvas, layout, view, cone state, patch model.

Two coordinate systems and one surface parameterisation:

    view      orthographic world → screen, y flipped
    (phi, s)  the cone's lateral surface: phi angular, s axial
              s = 0 at the base ring, s = 1 at the apex

A patch is four numbers — [phi0, phi1] × [s0, s1] — a rectangle in
parameter space.  quadCorners is the single reader; every renderer
and hit test asks through it, so corner order is defined once.

A floating square is { quadId, u, v, scale } in its patch's local
frame; the frame derivation lives in pg_view_squares.
"""

CORE_JS = r"""
/* ==========================================================================
   CANVAS
   ========================================================================== */

const canvas = document.getElementById("c");
const ctx    = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

/* ==========================================================================
   LAYOUT
   ========================================================================== */

const layout = {
  coneH:     0,
  dividerY:  0,
  reservedY: 0,
  reservedH: 0,
};

/* ==========================================================================
   VIEW
   ========================================================================== */

const view = {
  scale: 20, tx: 0, ty: 0,
  zoom: 1, panX: 0, panY: 0,
};

function w2s(x, y) { return [x * view.scale + view.tx,
                             -y * view.scale + view.ty]; }
function s2w(sx, sy) { return [(sx - view.tx) / view.scale,
                               -(sy - view.ty) / view.scale]; }

/* ==========================================================================
   CONE
   ========================================================================== */

const cone = {
  ax: 0, ay: 0,
  depth: 15,
  halfAngle: Math.PI / 6,
  ringCount: 14,
  meridianCount: 24,
};

function clampApex() {
  const R = cone.depth * Math.tan(cone.halfAngle);
  const maxR = R * 0.98;
  const dr = Math.hypot(cone.ax, cone.ay);
  if (dr > maxR && dr > 1e-9) {
    const s = maxR / dr;
    cone.ax *= s;
    cone.ay *= s;
  }
}

function coneR() { return cone.depth * Math.tan(cone.halfAngle); }

function surfacePoint(phi, s) {
  const R = coneR();
  const r = (1 - s) * R;
  return [s * cone.ax + r * Math.cos(phi),
          s * cone.ay + r * Math.sin(phi)];
}

/* ==========================================================================
   PATCH MODEL
   ==========================================================================
   Every patch:  [phi0, phi1] × [s0, s1].  Corner order:

       0   (phi0, s1)   top-left    near apex
       1   (phi1, s1)   top-right   near apex
       2   (phi1, s0)   bottom-right near base
       3   (phi0, s0)   bottom-left  near base

   The lateral edges lie on meridians; every meridian ends at the
   apex, so both lateral edges point at the peak.  S_MAX < 1 keeps a
   patch from collapsing into the apex. */

const S_MIN = 0.00;
const S_MAX = 0.90;

const quads = [];
let nextQuadId = 1;
let selectedQuad = -1;

function quadCorners(q) {
  return [
    { phi: q.phi0, s: q.s1 },
    { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 },
    { phi: q.phi0, s: q.s0 },
  ];
}

function addQuad() {
  const id  = nextQuadId++;
  const idx = quads.length;
  const dPhi = 0.36;

  let phiCenter = Math.PI / 2 + idx * 0.70;
  phiCenter = ((phiCenter % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  if (phiCenter - dPhi < 0)           phiCenter = dPhi;
  if (phiCenter + dPhi > 2 * Math.PI) phiCenter = 2 * Math.PI - dPhi;

  quads.push({
    id, name: "Q" + id,
    phi0: phiCenter - dPhi,
    phi1: phiCenter + dPhi,
    s0: 0.22,
    s1: 0.72,
  });
  selectedQuad = quads.length - 1;
  syncQuadList();
  draw();
}

function deleteQuad(idx) {
  if (idx < 0 || idx >= quads.length) return;
  const removed = quads.splice(idx, 1)[0];
  if (typeof deleteSquaresForQuad === "function") {
    deleteSquaresForQuad(removed.id);
  }
  if (selectedQuad >= quads.length) selectedQuad = quads.length - 1;
  syncQuadList();
  draw();
}

/* ==========================================================================
   INVERSE PROJECTION — cursor world point → (phi, s) on the surface
   ==========================================================================
   For fixed s, the closest surface point to T is on the ring at that
   s, with squared distance expressed as a quadratic in s:

       A·s² + B·s + C = 0
       A = |apex|² − R²
       B = −2 · (apex·T − R²)
       C = |T|² − R²

   Two real roots: the one nearer the caller's current s wins, so a
   drag stays on one branch of the surface.  No real root: forty-step
   sample over [S_MIN, S_MAX]. */
function projectToConeSurface(wx, wy, currentS) {
  const Px = cone.ax, Py = cone.ay;
  const R  = coneR();
  const R2 = R * R;
  const p2 = Px * Px + Py * Py;
  const t2 = wx * wx + wy * wy;
  const pt = Px * wx + Py * wy;

  const A = p2 - R2;
  const B = -2 * (pt - R2);
  const C = t2 - R2;

  const roots = [];
  if (Math.abs(A) < 1e-6) {
    if (Math.abs(B) > 1e-9) roots.push(-C / B);
  } else {
    const disc = B * B - 4 * A * C;
    if (disc >= 0) {
      const sq = Math.sqrt(disc);
      roots.push((-B - sq) / (2 * A));
      roots.push((-B + sq) / (2 * A));
    }
  }

  let bestS = null, bestDelta = Infinity;
  for (const s of roots) {
    if (s < S_MIN - 0.01 || s > S_MAX + 0.01) continue;
    const c = Math.max(S_MIN, Math.min(S_MAX, s));
    const d = Math.abs(c - currentS);
    if (d < bestDelta) { bestDelta = d; bestS = c; }
  }

  if (bestS === null) {
    bestDelta = Infinity;
    for (let i = 0; i <= 40; i++) {
      const s = S_MIN + (S_MAX - S_MIN) * i / 40;
      const cx = s * Px, cy = s * Py;
      const r  = (1 - s) * R;
      const d  = Math.abs(Math.hypot(wx - cx, wy - cy) - r);
      if (d < bestDelta) { bestDelta = d; bestS = s; }
    }
    if (bestS === null) bestS = Math.max(S_MIN, Math.min(S_MAX, currentS));
  }

  const cx = bestS * Px, cy = bestS * Py;
  const dx = wx - cx, dy = wy - cy;
  const phi = (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9)
              ? 0 : Math.atan2(dy, dx);
  return { phi, s: bestS };
}

/* ==========================================================================
   TRANSIENT STATE
   ==========================================================================
   Five drag modes, mutually exclusive at any moment:

       dragApex        apex tilt / depth pull from the cone band
       dragQuadVertex  a patch's corner, either band
       dragSquare      a floating square from the cone band
       flatDrag        a patch's corner/edge/body from the flat band
       flatSquareDrag  a floating square from the flat band */

const state = {
  dragApex:       null,
  dragQuadVertex: null,
  dragSquare:     null,
  flatDrag:       null,
  flatSquareDrag: null,
  mouse: { sx: 0, sy: 0, inside: false },
};

/* ==========================================================================
   RESIZE / FIT
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

  layout.coneH     = Math.round(ch * 0.58);
  layout.dividerY  = layout.coneH;
  layout.reservedY = layout.coneH + 4;
  layout.reservedH = ch - layout.reservedY;

  fitView();
  draw();
}
window.addEventListener("resize", resize);

function fitView() {
  view.scale = 20 * view.zoom;
  view.tx = window.innerWidth / 2 + view.panX;
  view.ty = layout.coneH / 2 + view.panY;
}

function resetView() {
  view.zoom = 1; view.panX = 0; view.panY = 0;
  cone.ax = 0; cone.ay = 0;
  cone.depth = 15;
  cone.halfAngle = Math.PI / 6;
  cone.ringCount = 14;
  cone.meridianCount = 24;
  syncPanelSliders();
  fitView();
  draw();
}
"""
