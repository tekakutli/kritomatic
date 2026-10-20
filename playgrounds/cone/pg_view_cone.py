"""
pg_view_cone.py — the interior of the cone.

Layers, back to front:

    1.  radial depth gradient centred on the apex
    2.  meridians from apex to base ring, low-alpha cyan
    3.  rings on the axis, radius (1 − s) · R_world, opacity ∝ (1 − s)²
    4.  dashed magenta axis line when the apex is off-centre
    5.  base direction tick on the outer ring
    6.  small ink dot at the base centre
    7.  patch quads — dashed outlines, faint fills
    8.  floating shapes — solid outlines, corner/rotate handles
    9.  apex marker
   10.  corner caption
   11.  patch and shape name labels — drawn last

STAGE 1 OPTIMIZATION — BACKGROUND CACHE
=======================================
Layers 1–6 (gradient, meridians, rings, axis, tick, base dot) are
fully determined by the apex position, cone geometry sliders, and
the canvas transform.  They change only on apex drag, slider move,
or resize — never on square drag, panel edits, or hover.  They used
to re-render every frame: ~24 `createLinearGradient` calls and 15
`arc` strokes.

Layers 1–6 are now drawn into an offscreen canvas.  A per-frame key
comparison decides whether to blit the cache (GPU-fast) or rebuild
it.  In the common case — dragging a square, editing a field,
hovering — the key is stable and the whole layer stack renders as
one `ctx.drawImage`.

Cache key: (cw, ch, dpr, cone.ax, cone.ay, cone.depth,
            cone.halfAngle, cone.ringCount, cone.meridianCount,
            view.scale, view.tx, view.ty, layout.coneH)

Memory: at DPR 2 on a 2000×1200 CSS canvas, ~38 MB.  Freed when the
key changes (old canvas becomes garbage) and on window resize.

PATCH VERSUS SHAPE
==================
Both a patch and a shape are quadrilaterals in the patch's own (u, v)
frame, both are tinted by the patch's hue, and both carry a name
label.  What distinguishes them is the STROKE:

    patch   dashed outline, very faint fill
    shape   solid outline, thicker when selected, corner handles and
            a rotate handle when selected

The dashed stroke is the drafting idiom for "this is the surface, not
an object on it".
"""

CONE_VIEW_JS = r"""
/* ==========================================================================
   BACKGROUND CACHE
   ========================================================================== */

let _coneBgCache = null;   // { key: string, canvas: HTMLCanvasElement }

function _coneBgKey(cw, ch) {
  return [
    cw, ch, dpr,
    cone.ax, cone.ay,
    cone.depth, cone.halfAngle,
    cone.ringCount, cone.meridianCount,
    view.scale, view.tx, view.ty,
    layout.coneH,
  ].join("|");
}

function _buildConeBg(cw, ch) {
  const off = document.createElement("canvas");
  off.width  = Math.round(cw * dpr);
  off.height = Math.round(ch * dpr);

  // Save/restore the real ctx's transform so we don't disturb the
  // outer frame.  Everything below draws into a local alias.
  const octx = off.getContext("2d");
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const R_world = cone.depth * Math.tan(cone.halfAngle);
  const [Bx, By] = w2s(0, 0);
  const [Ax, Ay] = w2s(cone.ax, cone.ay);
  const Rs = R_world * view.scale;

  // 1. Depth gradient.
  const apexDist  = Math.hypot(Ax - Bx, Ay - By);
  const gradientR = Math.max(apexDist + Rs, 40);
  const bg = octx.createRadialGradient(Ax, Ay, 0, Ax, Ay, gradientR);
  bg.addColorStop(0.00, "#05080c");
  bg.addColorStop(0.32, "#070c12");
  bg.addColorStop(0.68, "#0a1018");
  bg.addColorStop(1.00, "#0e1622");
  octx.fillStyle = bg;
  octx.fillRect(0, 0, cw, ch);

  // 2. Meridians.
  const M = cone.meridianCount;
  octx.lineCap = "round";
  for (let i = 0; i < M; i++) {
    const phi = i * 2 * Math.PI / M;
    const [ex, ey] = w2s(R_world * Math.cos(phi),
                         R_world * Math.sin(phi));
    const lg = octx.createLinearGradient(Ax, Ay, ex, ey);
    lg.addColorStop(0.00, "rgba(0, 229, 255, 0.00)");
    lg.addColorStop(0.10, "rgba(0, 229, 255, 0.09)");
    lg.addColorStop(0.60, "rgba(0, 229, 255, 0.20)");
    lg.addColorStop(1.00, "rgba(0, 229, 255, 0.40)");
    octx.beginPath(); octx.moveTo(Ax, Ay); octx.lineTo(ex, ey);
    octx.strokeStyle = lg; octx.lineWidth = 0.9; octx.stroke();
  }

  // 3. Rings.
  const N = cone.ringCount;
  for (let k = 0; k <= N; k++) {
    const s = k / N;
    const [cx, cy] = w2s(s * cone.ax, s * cone.ay);
    const rs = (1 - s) * R_world * view.scale;
    if (rs < 0.5) continue;
    const u = 1 - s;
    const alpha = 0.10 + 0.55 * u * u;
    octx.beginPath(); octx.arc(cx, cy, rs, 0, Math.PI * 2);
    octx.strokeStyle = "rgba(0, 229, 255, " + alpha.toFixed(3) + ")";
    octx.lineWidth = (k === 0) ? 2.0 : 0.9;
    octx.stroke();
  }

  // 4. Dashed axis line when the apex is off-centre.
  if (apexDist > 6) {
    octx.save();
    octx.setLineDash([5, 4]);
    octx.strokeStyle = "rgba(255, 43, 214, 0.45)";
    octx.lineWidth = 1.0;
    octx.beginPath(); octx.moveTo(Bx, By); octx.lineTo(Ax, Ay); octx.stroke();
    octx.restore();
  }

  // 5. Base direction tick.
  if (Rs > 20) {
    octx.beginPath();
    octx.arc(Bx, By, Rs, -Math.PI / 2 - 0.15, -Math.PI / 2 + 0.15);
    octx.strokeStyle = "rgba(255, 43, 214, 0.9)";
    octx.lineWidth = 2.4; octx.stroke();
  }

  // 6. Base centre dot.
  octx.beginPath(); octx.arc(Bx, By, 2.2, 0, Math.PI * 2);
  octx.fillStyle = "rgba(220, 230, 242, 0.55)"; octx.fill();

  return off;
}

function _coneBg(cw, ch) {
  const key = _coneBgKey(cw, ch);
  if (_coneBgCache && _coneBgCache.key === key) return _coneBgCache.canvas;
  const canvas = _buildConeBg(cw, ch);
  _coneBgCache = { key, canvas };
  return canvas;
}

/* ==========================================================================
   CONE VIEW
   ========================================================================== */

function drawConeView() {
  const cw = window.innerWidth;
  const ch = layout.coneH;

  // Layers 1–6: cached background.
  ctx.drawImage(_coneBg(cw, ch), 0, 0, cw, ch);

  // Layer 7: patches.  Unselected first, selected on top.
  for (let i = 0; i < quads.length; i++) {
    if (i === selectedQuad) continue;
    drawQuadPatch(quads[i], false);
  }
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    drawQuadPatch(quads[selectedQuad], true);
  }

  // Layer 8: floating shapes.
  drawFloatSquares();

  // Layer 9: apex marker.
  const [Ax, Ay] = w2s(cone.ax, cone.ay);
  const haloR = 22;
  const halo = ctx.createRadialGradient(Ax, Ay, 0, Ax, Ay, haloR);
  halo.addColorStop(0.0, "rgba(255, 230, 0, 0.34)");
  halo.addColorStop(1.0, "rgba(255, 230, 0, 0.00)");
  ctx.fillStyle = halo;
  ctx.beginPath(); ctx.arc(Ax, Ay, haloR, 0, Math.PI * 2); ctx.fill();

  ctx.beginPath(); ctx.arc(Ax, Ay, 5.5, 0, Math.PI * 2);
  ctx.fillStyle = "#ffe600"; ctx.fill();
  ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 2; ctx.stroke();

  // Layer 10: corner caption.
  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  const halfDeg = Math.round(cone.halfAngle * 180 / Math.PI);
  const tiltDeg = Math.atan2(Math.hypot(cone.ax, cone.ay), cone.depth)
                  * 180 / Math.PI;
  ctx.fillText(
    "APEX (" + cone.ax.toFixed(1) + ", " + cone.ay.toFixed(1) + ")" +
    "   TILT " + tiltDeg.toFixed(1) + "\u00B0" +
    "   DEPTH " + cone.depth.toFixed(1) +
    "   HALF-ANGLE " + halfDeg + "\u00B0" +
    "   PATCHES " + quads.length +
    "   SQUARES " + floatSquares.length,
    14, 14);
  ctx.restore();

  // Layer 11: labels.
  drawShapeLabelsCone();
}

function drawQuadPatch(q, selected) {
  const hue = patchHue(q);
  const pts = quadCorners(q).map(c => {
    const [wx, wy] = surfacePoint(c.phi, c.s);
    return w2s(wx, wy);
  });

  ctx.save();

  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
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
"""
