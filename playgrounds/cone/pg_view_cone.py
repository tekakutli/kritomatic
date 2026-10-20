"""
pg_view_cone.py — the interior of the cone.

ES module.  Layers, back to front: cached depth gradient + meridians
+ rings (from `_coneBg`), patches, shapes, apex marker, corner
caption, shape labels.

STAGE 1 OPTIMIZATION (preserved)
================================
Layers 1–6 are drawn into an offscreen canvas once per key change
(apex move, slider, resize) and blitted per frame.
"""

CONE_VIEW_JS = r"""
import {
  ctx, canvas, env, view, layout, cone, quads, floatSquares,
  coneState, w2s, surfacePoint, PATCH_SHAPE_EDIT_ENABLED,
} from "./pg_state.js";
import {
  patchHue, _huergb, _huergbLight,
} from "./pg_view_squares_core.js";
import { drawFloatSquares } from "./pg_view_squares_proj.js";
import { drawShapeLabelsCone } from "./pg_view_squares_flat.js";

/* ==========================================================================
   BACKGROUND CACHE
   ========================================================================== */

let _coneBgCache = null;

function _coneBgKey(cw, ch) {
  return [
    cw, ch, env.dpr,
    cone.ax, cone.ay,
    cone.depth, cone.halfAngle,
    cone.ringCount, cone.meridianCount,
    view.scale, view.tx, view.ty,
    layout.coneH,
  ].join("|");
}

function _buildConeBg(cw, ch) {
  const off = document.createElement("canvas");
  off.width  = Math.round(cw * env.dpr);
  off.height = Math.round(ch * env.dpr);

  const octx = off.getContext("2d");
  octx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);

  const R_world = cone.depth * Math.tan(cone.halfAngle);
  const [Bx, By] = w2s(0, 0);
  const [Ax, Ay] = w2s(cone.ax, cone.ay);
  const Rs = R_world * view.scale;

  const apexDist  = Math.hypot(Ax - Bx, Ay - By);
  const gradientR = Math.max(apexDist + Rs, 40);
  const bg = octx.createRadialGradient(Ax, Ay, 0, Ax, Ay, gradientR);
  bg.addColorStop(0.00, "#05080c");
  bg.addColorStop(0.32, "#070c12");
  bg.addColorStop(0.68, "#0a1018");
  bg.addColorStop(1.00, "#0e1622");
  octx.fillStyle = bg;
  octx.fillRect(0, 0, cw, ch);

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

  if (apexDist > 6) {
    octx.save();
    octx.setLineDash([5, 4]);
    octx.strokeStyle = "rgba(255, 43, 214, 0.45)";
    octx.lineWidth = 1.0;
    octx.beginPath(); octx.moveTo(Bx, By); octx.lineTo(Ax, Ay); octx.stroke();
    octx.restore();
  }

  if (Rs > 20) {
    octx.beginPath();
    octx.arc(Bx, By, Rs, -Math.PI / 2 - 0.15, -Math.PI / 2 + 0.15);
    octx.strokeStyle = "rgba(255, 43, 214, 0.9)";
    octx.lineWidth = 2.4; octx.stroke();
  }

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

export function drawConeView() {
  const cw = window.innerWidth;
  const ch = layout.coneH;

  ctx.drawImage(_coneBg(cw, ch), 0, 0, cw, ch);

  for (let i = 0; i < quads.length; i++) {
    if (i === coneState.selectedQuad) continue;
    drawQuadPatch(quads[i], false);
  }
  if (coneState.selectedQuad >= 0 &&
      coneState.selectedQuad < quads.length) {
    drawQuadPatch(quads[coneState.selectedQuad], true);
  }

  drawFloatSquares();

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

  drawShapeLabelsCone();
}

function drawQuadPatch(q, selected) {
  const hue = patchHue(q);
  const pts = [
    { phi: q.phi0, s: q.s1 }, { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 }, { phi: q.phi0, s: q.s0 },
  ].map(c => {
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
