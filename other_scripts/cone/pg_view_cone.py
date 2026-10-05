"""
pg_view_cone.py — the interior of the cone.

Layers, back to front:

    1.  radial depth gradient centred on the apex
    2.  meridians from apex to base ring, low-alpha cyan
    3.  rings on the axis, radius (1 − s) · R_world, opacity ∝ (1 − s)²
    4.  dashed magenta axis line when the apex is off-centre
    5.  base direction tick on the outer ring
    6.  small ink dot at the base centre
    7.  patch quads — the planes squares live on, drawn faintly
    8.  floating squares — bound to each patch's plane
    9.  apex marker
   10.  corner caption

Patches are the tool, not the subject: they are drawn faintly, and
their corner handles appear only when shape editing is enabled.
"""

CONE_VIEW_JS = r"""
function drawConeView() {
  const cw = window.innerWidth;
  const ch = layout.coneH;

  const R_world = cone.depth * Math.tan(cone.halfAngle);
  const [Bx, By] = w2s(0, 0);
  const [Ax, Ay] = w2s(cone.ax, cone.ay);
  const Rs = R_world * view.scale;

  /* depth gradient */
  const apexDist  = Math.hypot(Ax - Bx, Ay - By);
  const gradientR = Math.max(apexDist + Rs, 40);
  const bg = ctx.createRadialGradient(Ax, Ay, 0, Ax, Ay, gradientR);
  bg.addColorStop(0.00, "#05080c");
  bg.addColorStop(0.32, "#070c12");
  bg.addColorStop(0.68, "#0a1018");
  bg.addColorStop(1.00, "#0e1622");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, cw, ch);

  /* meridians */
  const M = cone.meridianCount;
  ctx.lineCap = "round";
  for (let i = 0; i < M; i++) {
    const phi = i * 2 * Math.PI / M;
    const [ex, ey] = w2s(R_world * Math.cos(phi),
                         R_world * Math.sin(phi));
    const lg = ctx.createLinearGradient(Ax, Ay, ex, ey);
    lg.addColorStop(0.00, "rgba(0, 229, 255, 0.00)");
    lg.addColorStop(0.10, "rgba(0, 229, 255, 0.09)");
    lg.addColorStop(0.60, "rgba(0, 229, 255, 0.20)");
    lg.addColorStop(1.00, "rgba(0, 229, 255, 0.40)");
    ctx.beginPath(); ctx.moveTo(Ax, Ay); ctx.lineTo(ex, ey);
    ctx.strokeStyle = lg; ctx.lineWidth = 0.9; ctx.stroke();
  }

  /* rings */
  const N = cone.ringCount;
  for (let k = 0; k <= N; k++) {
    const s = k / N;
    const [cx, cy] = w2s(s * cone.ax, s * cone.ay);
    const rs = (1 - s) * R_world * view.scale;
    if (rs < 0.5) continue;
    const u = 1 - s;
    const alpha = 0.10 + 0.55 * u * u;
    ctx.beginPath(); ctx.arc(cx, cy, rs, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(0, 229, 255, " + alpha.toFixed(3) + ")";
    ctx.lineWidth = (k === 0) ? 2.0 : 0.9;
    ctx.stroke();
  }

  /* axis */
  if (apexDist > 6) {
    ctx.save();
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = "rgba(255, 43, 214, 0.45)";
    ctx.lineWidth = 1.0;
    ctx.beginPath(); ctx.moveTo(Bx, By); ctx.lineTo(Ax, Ay); ctx.stroke();
    ctx.restore();
  }

  /* base direction tick */
  if (Rs > 20) {
    ctx.beginPath();
    ctx.arc(Bx, By, Rs, -Math.PI / 2 - 0.15, -Math.PI / 2 + 0.15);
    ctx.strokeStyle = "rgba(255, 43, 214, 0.9)";
    ctx.lineWidth = 2.4; ctx.stroke();
  }

  /* base centre dot */
  ctx.beginPath(); ctx.arc(Bx, By, 2.2, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(220, 230, 242, 0.55)"; ctx.fill();

  /* patches — unselected first, selected on top */
  for (let i = 0; i < quads.length; i++) {
    if (i === selectedQuad) continue;
    drawQuadPatch(quads[i], false);
  }
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    drawQuadPatch(quads[selectedQuad], true);
  }

  /* floating squares on the patches' planes */
  drawFloatSquares();

  /* apex marker */
  const haloR = 22;
  const halo = ctx.createRadialGradient(Ax, Ay, 0, Ax, Ay, haloR);
  halo.addColorStop(0.0, "rgba(255, 230, 0, 0.34)");
  halo.addColorStop(1.0, "rgba(255, 230, 0, 0.00)");
  ctx.fillStyle = halo;
  ctx.beginPath(); ctx.arc(Ax, Ay, haloR, 0, Math.PI * 2); ctx.fill();

  ctx.beginPath(); ctx.arc(Ax, Ay, 5.5, 0, Math.PI * 2);
  ctx.fillStyle = "#ffe600"; ctx.fill();
  ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 2; ctx.stroke();

  /* corner caption */
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
}

function drawQuadPatch(q, selected) {
  const pts = quadCorners(q).map(c => {
    const [wx, wy] = surfacePoint(c.phi, c.s);
    return w2s(wx, wy);
  });

  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = selected
    ? "rgba(255, 200, 90, 0.10)"
    : "rgba(255, 200, 90, 0.04)";
  ctx.fill();

  ctx.lineJoin = "round";
  ctx.strokeStyle = selected
    ? "rgba(255, 220, 130, 0.60)"
    : "rgba(255, 200, 90, 0.28)";
  ctx.lineWidth = selected ? 1.5 : 1.0;
  ctx.stroke();

  /* Corner markers are the visual affordance for a corner drag.
     They appear only when the drag path behind them is live. */
  if (!PATCH_SHAPE_EDIT_ENABLED) return;

  const rDot = selected ? 5.2 : 3.0;
  for (const [sx, sy] of pts) {
    ctx.beginPath(); ctx.arc(sx, sy, rDot, 0, Math.PI * 2);
    ctx.fillStyle = selected ? "#ffe680" : "rgba(255, 210, 100, 0.80)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.5; ctx.stroke();
  }
}
"""
