"""
bx_png.py — print-friendly black-and-white cable-run / box exporter.

The output of this module is a PNG meant to be printed.  Nothing on it
is gray: every distinction that the on-screen view encodes with a
value is re-encoded here as a hatch, a stipple, or a crosshatch, so
the drawing reads the same way from a laser printer as it does from a
screen.

The visual vocabulary:

    wall material      dot stipple on a diagonal grid
    step surface       forward-slash diagonal hatch
    overlapping boxes  crosshatch (both diagonals)
    invalid boxes      backslash diagonal hatch
    valid box interior pure white
    all ink            #000000
    all paper          #ffffff

Every wall, step, and box outline is a solid or dashed black stroke;
no element is ever drawn by a fill value alone.

The four textures are deliberately DIFFERENT KINDS of mark, not the
same mark at different pitches.  A reader can classify any small
patch of the drawing without measuring anything: dots are material,
one forward diagonal is a surface, two crossing diagonals are a
conflict, a backslash is off-limits.

UNIFIED LABEL SYSTEM
====================
Everything that carries text on the drawing — inside box names,
outside box names, the W and H dimension numbers next to a box — is
a *label*.  Every label is in one list.  Every label has:

    rect         the AABB its text occupies, mutable by relaxation
    anchor       a list of points it points at
    movable      false for labels that stay put

Every label draws a dashed leader from its rect's edge to the nearest
point on its anchor.

Relaxation
----------
All labels, plus the box bodies as invisible fixed obstacles, are
pushed apart pairwise: overlapping pairs are separated along their
axis of smaller overlap.  Movable labels move; fixed ones do not.

Name orientation
----------------
Every box name reads along the box's longest VISUAL side.  "Longest"
is measured in screen pixels by comparing the two edges the box's
screen-projected corners define.

RETURN-YOUR-ASYNC-WORK
======================
`renderFloorPlanPNG` returns a Promise.  The body of the function up
to and including the final `cv.toBlob(...)` call is synchronous
canvas drawing; `toBlob` fires its callback in a task (per spec, not
a microtask), so the actual PNG encode and the download trigger are
asynchronous.  Wrapping the whole function in a Promise means:

  • A synchronous failure anywhere in the drawing pipeline (bad
    geometry, missing canvas method, etc.) becomes a rejection
    rather than a silent no-op — the caller's `.then` / `await`
    sees it.

  • The `!blob` path — the encode failed — resolves `false`.  That
    failure has already been surfaced via `flashStatus`, so it
    should not also be reported as an error to the caller (a script
    that runs `action.printPNG` should not abort on the visual-
    feedback path; it should abort on `stop_on_error`, and a
    `false` result is not that).

  • A successful encode resolves `true` after the download has been
    triggered — so a script that does `action.printPNG` then
    `action.clearAll` cannot capture post-clear state in the PNG.

This is the convention every playground action follows: `run` must
return a Promise that resolves when the action's visible effect is
complete.  See `bx_panel_bindings.py`'s "RETURN-YOUR-ASYNC-WORK"
note for the parallel on the wrapper side.
"""

PNG_JS = """

import {
  board, view, state,
  b2s, boxCorners, boxIsInvalid, pointInPolygon, polygonsOverlap,
} from "./bx_core.js";
import { GEOMETRY } from "./bx_geometry.js";
import { T } from "./bx_i18n.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   PRINT PATTERNS
   ==========================================================================
   Four cached pattern canvases.  Each class gets a different KIND of
   mark; pitches are looser than the on-screen hatches. */

const _PNG_PATTERNS = Object.create(null);

function _pngPattern(kind) {
  if (_PNG_PATTERNS[kind]) return _PNG_PATTERNS[kind];
  const make = (size, draw) => {
    const pc = document.createElement("canvas");
    pc.width = pc.height = size;
    const p = pc.getContext("2d");
    p.strokeStyle = "#000000";
    p.fillStyle   = "#000000";
    p.lineCap     = "round";
    draw(p, size);
    return pc;
  };

  const defs = {
    wallStipple: [14, (p, s) => {
      p.beginPath(); p.arc(s * 0.25, s * 0.25, 0.8, 0, Math.PI * 2); p.fill();
      p.beginPath(); p.arc(s * 0.75, s * 0.75, 0.8, 0, Math.PI * 2); p.fill();
    }],
    stepHatch: [20, (p, s) => {
      p.lineWidth = 0.7;
      p.beginPath();
      p.moveTo(-1, s + 1);     p.lineTo(s + 1, -1);
      p.moveTo(-1, 1);         p.lineTo(1, -1);
      p.moveTo(s - 1, s + 1);  p.lineTo(s + 1, s - 1);
      p.stroke();
    }],
    boxOverlap: [16, (p, s) => {
      p.lineWidth = 0.55;
      p.beginPath();
      p.moveTo(-1, s + 1); p.lineTo(s + 1, -1);
      p.moveTo(-1, -1);    p.lineTo(s + 1, s + 1);
      p.stroke();
    }],
    voidHatch: [24, (p, s) => {
      p.lineWidth = 0.6;
      p.beginPath();
      p.moveTo(s + 1, -1); p.lineTo(-1, s + 1);
      p.stroke();
    }],
  };
  const [size, draw] = defs[kind];
  _PNG_PATTERNS[kind] = make(size, draw);
  return _PNG_PATTERNS[kind];
}

/* ==========================================================================
   LABEL HELPERS
   ========================================================================== */

function labelAABB(cx, cy, w, h, theta) {
  const c = Math.cos(theta), s = Math.sin(theta);
  const hw = w / 2, hh = h / 2;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const corners = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]];
  for (const [dx, dy] of corners) {
    const px = cx + c * dx - s * dy;
    const py = cy + s * dx + c * dy;
    if (px < x0) x0 = px; if (px > x1) x1 = px;
    if (py < y0) y0 = py; if (py > y1) y1 = py;
  }
  return { x0, y0, x1, y1 };
}

function nameTextAngleDeg(boxScreenCorners) {
  const c0 = boxScreenCorners[0];
  const c1 = boxScreenCorners[1];
  const c2 = boxScreenCorners[2];
  const wEdge = Math.hypot(c1[0] - c0[0], c1[1] - c0[1]);
  const hEdge = Math.hypot(c2[0] - c1[0], c2[1] - c1[1]);

  let ang;
  if (wEdge >= hEdge) {
    ang = Math.atan2(c1[1] - c0[1], c1[0] - c0[0]);
  } else {
    ang = Math.atan2(c2[1] - c1[1], c2[0] - c1[0]);
  }

  if (ang >  Math.PI / 2) ang -= Math.PI;
  if (ang < -Math.PI / 2) ang += Math.PI;
  return ang * 180 / Math.PI;
}

function relaxLabels(labels, pad) {
  const MAX_PASSES = 40;
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    let moved = false;
    for (let i = 0; i < labels.length; i++) {
      const A = labels[i];
      const a = A.rect;
      for (let j = i + 1; j < labels.length; j++) {
        const B = labels[j];
        const b = B.rect;

        const overlapX = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
        const overlapY = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
        if (overlapX <= 0 || overlapY <= 0) continue;

        const aMov = A.movable !== false;
        const bMov = B.movable !== false;
        if (!aMov && !bMov) continue;

        const shareA = (aMov && bMov) ? 0.5 : (aMov ? 1 : 0);
        const shareB = (aMov && bMov) ? 0.5 : (bMov ? 1 : 0);

        if (overlapX < overlapY) {
          const push = (overlapX + pad) / 2;
          const dir = ((a.x0 + a.x1) < (b.x0 + b.x1)) ? -1 : 1;
          a.x0 += dir * push * (shareA * 2);
          a.x1 += dir * push * (shareA * 2);
          b.x0 -= dir * push * (shareB * 2);
          b.x1 -= dir * push * (shareB * 2);
        } else {
          const push = (overlapY + pad) / 2;
          const dir = ((a.y0 + a.y1) < (b.y0 + b.y1)) ? -1 : 1;
          a.y0 += dir * push * (shareA * 2);
          a.y1 += dir * push * (shareA * 2);
          b.y0 -= dir * push * (shareB * 2);
          b.y1 -= dir * push * (shareB * 2);
        }
        moved = true;
      }
    }
    if (!moved) break;
  }
}

function nearestPointOnTarget(cx, cy, target) {
  if (!target || target.length === 0) return null;
  if (target.length === 1) return { x: target[0][0], y: target[0][1] };
  let bestX = 0, bestY = 0, bestD = Infinity;
  const n = target.length;
  for (let i = 0; i < n; i++) {
    const A = target[i];
    const B = target[(i + 1) % n];
    const dx = B[0] - A[0], dy = B[1] - A[1];
    const L2 = dx * dx + dy * dy;
    let px, py, d;
    if (L2 < 1e-9) {
      px = A[0]; py = A[1];
      d = Math.hypot(cx - px, cy - py);
    } else {
      let t = ((cx - A[0]) * dx + (cy - A[1]) * dy) / L2;
      t = Math.max(0, Math.min(1, t));
      px = A[0] + t * dx; py = A[1] + t * dy;
      d = Math.hypot(cx - px, cy - py);
    }
    if (d < bestD) { bestD = d; bestX = px; bestY = py; }
  }
  return { x: bestX, y: bestY };
}

function drawLabelLeader(g, rect, target, fontSize, pngRes) {
  const cx = (rect.x0 + rect.x1) / 2;
  const cy = (rect.y0 + rect.y1) / 2;
  const dx = target.x - cx, dy = target.y - cy;
  const L = Math.hypot(dx, dy);
  if (L < 3) return;

  const ux = dx / L, uy = dy / L;
  const hw = (rect.x1 - rect.x0) / 2;
  const hh = (rect.y1 - rect.y0) / 2;
  let tRect = Infinity;
  if (Math.abs(ux) > 1e-9) tRect = Math.min(tRect, hw / Math.abs(ux));
  if (Math.abs(uy) > 1e-9) tRect = Math.min(tRect, hh / Math.abs(uy));
  if (!isFinite(tRect)) return;
  const sx = cx + ux * tRect;
  const sy = cy + uy * tRect;

  const ah = Math.min(Math.max(2, fontSize * 0.55), L * 0.5);
  const ahw = ah * 0.55;
  const baseX = target.x - ux * ah;
  const baseY = target.y - uy * ah;

  g.save();
  g.strokeStyle = "#000000";
  g.lineWidth = Math.max(1, 0.5 * pngRes);
  g.setLineDash([3 * pngRes, 3 * pngRes]);
  g.beginPath();
  g.moveTo(sx, sy);
  g.lineTo(baseX, baseY);
  g.stroke();
  g.setLineDash([]);

  const px = -uy, py = ux;
  g.beginPath();
  g.moveTo(target.x, target.y);
  g.lineTo(baseX + px * ahw, baseY + py * ahw);
  g.lineTo(baseX - px * ahw, baseY - py * ahw);
  g.closePath();
  g.fillStyle = "#000000";
  g.fill();
  g.restore();
}

function drawDimLineBody(g, A, B, ahSize, lw) {
  const dx = B[0] - A[0], dy = B[1] - A[1];
  const len = Math.hypot(dx, dy);
  if (len < 1) return;
  const ux = dx / len, uy = dy / len;
  const px = -uy, py = ux;

  g.strokeStyle = "#000000";
  g.lineWidth = lw;
  g.beginPath();
  g.moveTo(A[0], A[1]);
  g.lineTo(B[0], B[1]);
  g.stroke();

  const ah  = ahSize;
  const ahw = ahSize * 0.5;

  g.fillStyle = "#000000";
  g.beginPath();
  g.moveTo(A[0], A[1]);
  g.lineTo(A[0] + ux * ah + px * ahw, A[1] + uy * ah + py * ahw);
  g.lineTo(A[0] + ux * ah - px * ahw, A[1] + uy * ah - py * ahw);
  g.closePath();
  g.fill();

  g.beginPath();
  g.moveTo(B[0], B[1]);
  g.lineTo(B[0] - ux * ah + px * ahw, B[1] - uy * ah + py * ahw);
  g.lineTo(B[0] - ux * ah - px * ahw, B[1] - uy * ah - py * ahw);
  g.closePath();
  g.fill();
}

/* ==========================================================================
   MAIN RENDERER
   ==========================================================================
   Returns a Promise.  Everything up to and including `cv.toBlob` is
   synchronous; `toBlob` fires its callback in a task, so the function
   is not truly complete until that callback runs.  The Promise resolves:

     true      the PNG was encoded and the download was triggered

     false     `cv.toBlob` handed back null (encode failed).  The
               failure has already been surfaced via `flashStatus`;
               a caller that wants to abort on it should check the
               resolved value.

   Any synchronous throw inside the drawing pipeline rejects the
   Promise, which the caller sees as a normal rejection. */

export function renderFloorPlanPNG() {
  return new Promise((resolve) => {
    const PNG_RES   = 4;
    const PX_PER_MM = 0.05 * PNG_RES;
    const MARGIN_PX = 10   * PNG_RES;

    const PEN_PLAN = 0.7 * 0.5 * PNG_RES;
    const PEN_STEP = 0.5 * 0.5 * PNG_RES;
    const PEN_BOX  = 1.8 * 0.5 * PNG_RES;

    const MIN_FONT = 4 * PNG_RES;

    const b = GEOMETRY.bounds;
    const mmW = b.maxX - b.minX;
    const mmH = b.maxY - b.minY;

    const W = Math.max(1, Math.round(mmW * PX_PER_MM + 2 * MARGIN_PX));
    const H = Math.max(1, Math.round(mmH * PX_PER_MM + 2 * MARGIN_PX));

    const cv = document.createElement("canvas");
    cv.width = W;
    cv.height = H;
    const g = cv.getContext("2d");

    g.fillStyle = "#ffffff";
    g.fillRect(0, 0, W, H);

    const P = (x, y) => [
      (x - b.minX) * PX_PER_MM + MARGIN_PX,
      (b.maxY - y) * PX_PER_MM + MARGIN_PX,
    ];

    const _patStipple = g.createPattern(_pngPattern("wallStipple"), "repeat");
    const _patStep    = g.createPattern(_pngPattern("stepHatch"),   "repeat");
    const _patOverlap = g.createPattern(_pngPattern("boxOverlap"),  "repeat");
    const _patVoid    = g.createPattern(_pngPattern("voidHatch"),   "repeat");

    const traceFace = (face) => {
      g.beginPath();
      const o = face.outer;
      if (!o.length) return;
      let [px, py] = P(o[0][0], o[0][1]);
      g.moveTo(px, py);
      for (let i = 1; i < o.length; i++) {
        [px, py] = P(o[i][0], o[i][1]);
        g.lineTo(px, py);
      }
      g.closePath();
      for (const h of face.holes) {
        if (!h.length) continue;
        [px, py] = P(h[0][0], h[0][1]);
        g.moveTo(px, py);
        for (let i = 1; i < h.length; i++) {
          [px, py] = P(h[i][0], h[i][1]);
          g.lineTo(px, py);
        }
        g.closePath();
      }
    };

    /* ---------- 1. STEPS — forward-slash hatch ----------------------- */
    for (const face of GEOMETRY.stepFaces) {
      traceFace(face);
      g.fillStyle = _patStep;
      g.fill("evenodd");
      g.strokeStyle = "#000000";
      g.lineWidth = PEN_STEP;
      g.setLineDash([7 * PNG_RES, 5 * PNG_RES]);
      g.stroke();
      g.setLineDash([]);
    }

    /* ---------- 2. WALLS — dot stipple ------------------------------- */
    for (const face of GEOMETRY.faces) {
      traceFace(face);
      g.fillStyle = _patStipple;
      g.fill("evenodd");
      g.strokeStyle = "#000000";
      g.lineWidth = PEN_PLAN;
      g.stroke();
    }

    /* ---------- 3. BOXES — three classes, three visual rules --------- */
    const validFlags = board.items.map(bx => !boxIsInvalid(bx));
    const overlapFlags = (() => {
      const n = board.items.length;
      const flags = new Array(n).fill(false);
      const corners = board.items.map(b =>
        boxCorners(b.x, b.y, b.w, b.h, b.rot));
      for (let i = 0; i < n; i++) {
        if (!validFlags[i]) continue;
        for (let j = i + 1; j < n; j++) {
          if (!validFlags[j]) continue;
          if (polygonsOverlap(corners[i], corners[j])) {
            flags[i] = true;
            flags[j] = true;
          }
        }
      }
      return flags;
    })();

    for (let i = 0; i < board.items.length; i++) {
      const bx = board.items[i];
      const valid = validFlags[i];
      const ov    = overlapFlags[i];

      let fillStyle, dashed = false;
      if (!valid)      { fillStyle = _patVoid;    dashed = true;  }
      else if (ov)     { fillStyle = _patOverlap; dashed = false; }
      else             { fillStyle = "#ffffff";   dashed = false; }

      const sc = boxCorners(bx.x, bx.y, bx.w, bx.h, bx.rot)
                   .map(([x, y]) => P(x, y));

      g.beginPath();
      g.moveTo(sc[0][0], sc[0][1]);
      for (let k = 1; k < 4; k++) g.lineTo(sc[k][0], sc[k][1]);
      g.closePath();
      g.fillStyle = fillStyle;
      g.fill();
      if (dashed) g.setLineDash([7 * PNG_RES, 5 * PNG_RES]);
      g.strokeStyle = "#000000";
      g.lineWidth = PEN_BOX;
      g.stroke();
      g.setLineDash([]);
    }

    /* ---------- 4. LABELS --------------------------------------------
       One collection, one relaxation, one draw pass. */

    const boxPolysProj = board.items.map(bx =>
      boxCorners(bx.x, bx.y, bx.w, bx.h, bx.rot).map(([x, y]) => P(x, y)));

    const boxRects = boxPolysProj.map(sc => ({
      x0: Math.min(sc[0][0], sc[1][0], sc[2][0], sc[3][0]),
      y0: Math.min(sc[0][1], sc[1][1], sc[2][1], sc[3][1]),
      x1: Math.max(sc[0][0], sc[1][0], sc[2][0], sc[3][0]),
      y1: Math.max(sc[0][1], sc[1][1], sc[2][1], sc[3][1]),
    }));

    const wallFacesProj = GEOMETRY.faces.map(face => ({
      outer: face.outer.map(([x, y]) => P(x, y)),
      holes: face.holes.map(h => h.map(([x, y]) => P(x, y))),
    }));

    const allLabels = [];

    /* Box bodies as invisible fixed obstacles. */
    for (let i = 0; i < boxRects.length; i++) {
      allLabels.push({
        kind: "box-body",
        rect: { ...boxRects[i] },
        anchor: null,
        movable: false,
        draw: false,
      });
    }

    /* ---- 4a. inside/outside decision ---- */
    const insideFlags = new Array(board.items.length).fill(false);
    const insideFS    = new Array(board.items.length).fill(0);

    for (let i = 0; i < board.items.length; i++) {
      const bx = board.items[i];
      const hasName = !!(bx.name && bx.name.length);
      const nameStr = bx.name || "";
      const wStr = String(Math.round(bx.w));
      const hStr = String(Math.round(bx.h));
      const sc = boxPolysProj[i];

      const edgeW = Math.hypot(sc[1][0] - sc[0][0], sc[1][1] - sc[0][1]);
      const edgeH = Math.hypot(sc[2][0] - sc[1][0], sc[2][1] - sc[1][1]);
      const longEdge  = Math.max(edgeW, edgeH);
      const shortEdge = Math.min(edgeW, edgeH);

      let fs = Math.min(14 * PNG_RES, shortEdge * 0.16, longEdge * 0.12);
      if (fs < MIN_FONT) continue;
      for (let tries = 0; tries < 15; tries++) {
        const dimFS = fs * 0.82;
        g.font = `600 ${fs}px 'JetBrains Mono', monospace`;
        const nameW = hasName ? g.measureText(nameStr).width : 0;
        g.font = `500 ${dimFS}px 'JetBrains Mono', monospace`;
        const wW = g.measureText(wStr).width;
        const hW = g.measureText(hStr).width;
        const margin = 2.2 * dimFS;
        const ok =
          (nameW + margin <= longEdge) &&
          (fs + margin <= shortEdge) &&
          (wW <= edgeW * 0.85) &&
          (hW <= edgeH * 0.85);
        if (ok) { insideFlags[i] = true; insideFS[i] = fs; break; }
        fs *= 0.9;
        if (fs < MIN_FONT) break;
      }
    }

    /* ---- 4b. collect inside labels ---- */
    for (let i = 0; i < board.items.length; i++) {
      if (!insideFlags[i]) continue;
      const bx = board.items[i];
      const nameStr = bx.name || "";
      const wStr = String(Math.round(bx.w));
      const hStr = String(Math.round(bx.h));
      const sc = boxPolysProj[i];
      const fs = insideFS[i];
      const dimFS = fs * 0.82;

      if (nameStr) {
        const [cxs, cys] = P(bx.x, bx.y);
        const nameDeg = nameTextAngleDeg(sc);
        const nameRad = nameDeg * Math.PI / 180;

        g.font = `600 ${fs.toFixed(1)}px 'JetBrains Mono', monospace`;
        const nameW = g.measureText(nameStr).width;
        const plateW = nameW + fs * 0.3;
        const plateH = fs * 1.24;

        allLabels.push({
          kind: "inside-name",
          rect: labelAABB(cxs, cys, plateW, plateH, nameRad),
          anchor: sc,
          movable: false,
          text: nameStr,
          fontSize: fs,
          rotationDeg: nameDeg,
          plateW, plateH,
        });
      }

      /* Inside W dimension number */
      {
        const mx = (sc[0][0] + sc[1][0]) / 2;
        const my = (sc[0][1] + sc[1][1]) / 2;
        const ox = (sc[2][0] + sc[3][0]) / 2 - mx;
        const oy = (sc[2][1] + sc[3][1]) / 2 - my;
        const ol = Math.hypot(ox, oy) || 1;
        const off = dimFS * 0.95;
        const px = mx + ox / ol * off;
        const py = my + oy / ol * off;

        let ang = Math.atan2(sc[1][1] - sc[0][1], sc[1][0] - sc[0][0]);
        if (ang >  Math.PI / 2) ang -= Math.PI;
        if (ang < -Math.PI / 2) ang += Math.PI;

        g.font = `500 ${dimFS.toFixed(1)}px 'JetBrains Mono', monospace`;
        const tw = g.measureText(wStr).width;
        const plateW = tw + dimFS * 0.3;
        const plateH = dimFS * 1.24;

        allLabels.push({
          kind: "inside-dim",
          rect: labelAABB(px, py, plateW, plateH, ang),
          anchor: [[mx, my]],
          movable: false,
          text: wStr,
          fontSize: dimFS,
          rotationDeg: ang * 180 / Math.PI,
          plateW, plateH,
        });
      }

      /* Inside H dimension number */
      {
        const mx = (sc[1][0] + sc[2][0]) / 2;
        const my = (sc[1][1] + sc[2][1]) / 2;
        const ox = (sc[3][0] + sc[0][0]) / 2 - mx;
        const oy = (sc[3][1] + sc[0][1]) / 2 - my;
        const ol = Math.hypot(ox, oy) || 1;
        const off = dimFS * 0.95;
        const px = mx + ox / ol * off;
        const py = my + oy / ol * off;

        let ang = Math.atan2(sc[2][1] - sc[1][1], sc[2][0] - sc[1][0]);
        if (ang >  Math.PI / 2) ang -= Math.PI;
        if (ang < -Math.PI / 2) ang += Math.PI;

        g.font = `500 ${dimFS.toFixed(1)}px 'JetBrains Mono', monospace`;
        const tw = g.measureText(hStr).width;
        const plateW = tw + dimFS * 0.3;
        const plateH = dimFS * 1.24;

        allLabels.push({
          kind: "inside-dim",
          rect: labelAABB(px, py, plateW, plateH, ang),
          anchor: [[mx, my]],
          movable: false,
          text: hStr,
          fontSize: dimFS,
          rotationDeg: ang * 180 / Math.PI,
          plateW, plateH,
        });
      }
    }

    /* ---- 4c. collect outside labels (movable) ---- */
    const rectsOverlap = (a, b) =>
      !(a.x1 < b.x0 || a.x0 > b.x1 || a.y1 < b.y0 || a.y0 > b.y1);

    const rectToPoly = (r) =>
      [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];

    function countHits(r, skipBoxIdx) {
      let n = 0;
      for (const L of allLabels) {
        if (L.kind === "box-body" && L.rect === boxRects[skipBoxIdx]) continue;
        if (rectsOverlap(r, L.rect)) n += 1;
      }
      const p = rectToPoly(r);
      for (const face of wallFacesProj) {
        if (polygonsOverlap(p, face.outer)) n += 1;
      }
      return n;
    }

    const rectInsidePoly = (r, poly) => {
      const cs = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];
      for (const c of cs) if (!pointInPolygon(c, poly)) return false;
      return true;
    };

    for (let i = 0; i < board.items.length; i++) {
      if (insideFlags[i]) continue;
      const bx = board.items[i];
      const hasName = !!(bx.name && bx.name.length);
      const nameStr = bx.name || "";
      const wStr = String(Math.round(bx.w));
      const hStr = String(Math.round(bx.h));
      const sc = boxPolysProj[i];

      const outFS = 5 * PNG_RES;
      const numFS = outFS * 0.9;

      const BL = sc[0], BR = sc[1], TR = sc[2];
      const cx0 = (sc[0][0] + sc[1][0] + sc[2][0] + sc[3][0]) / 4;
      const cy0 = (sc[0][1] + sc[1][1] + sc[2][1] + sc[3][1]) / 4;

      const wdx = BR[0] - BL[0], wdy = BR[1] - BL[1];
      const wlen = Math.hypot(wdx, wdy) || 1;
      let wnx = -wdy / wlen, wny = wdx / wlen;
      const wmx = (BL[0] + BR[0]) / 2, wmy = (BL[1] + BR[1]) / 2;
      if ((cx0 - wmx) * wnx + (cy0 - wmy) * wny > 0) { wnx = -wnx; wny = -wny; }

      const hdx = TR[0] - BR[0], hdy = TR[1] - BR[1];
      const hlen = Math.hypot(hdx, hdy) || 1;
      let hnx = -hdy / hlen, hny = hdx / hlen;
      const hmx = (BR[0] + TR[0]) / 2, hmy = (BR[1] + TR[1]) / 2;
      if ((cx0 - hmx) * hnx + (cy0 - hmy) * hny > 0) { hnx = -hnx; hny = -hny; }

      const off = numFS * 1.8;

      const wA = [BL[0] + wnx * off, BL[1] + wny * off];
      const wB = [BR[0] + wnx * off, BR[1] + wny * off];
      const hA = [BR[0] + hnx * off, BR[1] + hny * off];
      const hB = [TR[0] + hnx * off, TR[1] + hny * off];

      const extLW = Math.max(1, 0.35 * PNG_RES);
      const dimLW = Math.max(1, 0.6 * PNG_RES);

      g.strokeStyle = "#000000";
      g.lineWidth = extLW;
      g.beginPath();
      g.moveTo(BL[0], BL[1]); g.lineTo(wA[0], wA[1]);
      g.moveTo(BR[0], BR[1]); g.lineTo(wB[0], wB[1]);
      g.moveTo(BR[0], BR[1]); g.lineTo(hA[0], hA[1]);
      g.moveTo(TR[0], TR[1]); g.lineTo(hB[0], hB[1]);
      g.stroke();

      drawDimLineBody(g, wA, wB, numFS * 0.7, dimLW);
      drawDimLineBody(g, hA, hB, numFS * 0.7, dimLW);

      /* W and H dimension numbers. */
      {
        g.font = `500 ${numFS.toFixed(1)}px 'JetBrains Mono', monospace`;
        const wTw = g.measureText(wStr).width;
        const hTw = g.measureText(hStr).width;

        let wAng = Math.atan2(wB[1] - wA[1], wB[0] - wA[0]);
        if (wAng >  Math.PI / 2) wAng -= Math.PI;
        if (wAng < -Math.PI / 2) wAng += Math.PI;
        let hAng = Math.atan2(hB[1] - hA[1], hB[0] - hA[0]);
        if (hAng >  Math.PI / 2) hAng -= Math.PI;
        if (hAng < -Math.PI / 2) hAng += Math.PI;

        const wMid = [(wA[0] + wB[0]) / 2, (wA[1] + wB[1]) / 2];
        const hMid = [(hA[0] + hB[0]) / 2, (hA[1] + hB[1]) / 2];

        const wPW = wTw + numFS * 0.4;
        const wPH = numFS * 1.24;
        const hPW = hTw + numFS * 0.4;
        const hPH = numFS * 1.24;

        allLabels.push({
          kind: "outside-dim",
          rect: labelAABB(wMid[0], wMid[1], wPW, wPH, wAng),
          anchor: [[wA[0], wA[1]], [wB[0], wB[1]]],
          movable: true,
          text: wStr,
          fontSize: numFS,
          rotationDeg: wAng * 180 / Math.PI,
          plateW: wPW, plateH: wPH,
        });

        allLabels.push({
          kind: "outside-dim",
          rect: labelAABB(hMid[0], hMid[1], hPW, hPH, hAng),
          anchor: [[hA[0], hA[1]], [hB[0], hB[1]]],
          movable: true,
          text: hStr,
          fontSize: numFS,
          rotationDeg: hAng * 180 / Math.PI,
          plateW: hPW, plateH: hPH,
        });
      }

      if (!hasName) continue;

      const nameDeg = nameTextAngleDeg(sc);
      const nameRad = nameDeg * Math.PI / 180;
      const absC = Math.abs(Math.cos(nameRad));
      const absS = Math.abs(Math.sin(nameRad));

      g.font = `600 ${outFS.toFixed(1)}px 'JetBrains Mono', monospace`;
      const textW = g.measureText(nameStr).width;
      const textH = outFS * 1.15;
      const plateW = textW + outFS * 0.3;
      const plateH = textH;
      const aabbW = plateW * absC + plateH * absS;
      const aabbH = plateW * absS + plateH * absC;

      const minX = Math.min(sc[0][0], sc[1][0], sc[2][0], sc[3][0]);
      const maxX = Math.max(sc[0][0], sc[1][0], sc[2][0], sc[3][0]);
      const minY = Math.min(sc[0][1], sc[1][1], sc[2][1], sc[3][1]);
      const maxY = Math.max(sc[0][1], sc[1][1], sc[2][1], sc[3][1]);
      const ncx0 = (minX + maxX) / 2;
      const ncy0 = (minY + maxY) / 2;
      const gap = outFS * 0.8;

      let chosenName = null;

      const insideCand = {
        x0: ncx0 - aabbW / 2, y0: ncy0 - aabbH / 2,
        x1: ncx0 + aabbW / 2, y1: ncy0 + aabbH / 2,
      };
      if (rectInsidePoly(insideCand, sc) && countHits(insideCand, i) === 0) {
        chosenName = insideCand;
      } else {
        const cands = [
          { x0: ncx0 - aabbW / 2, y0: minY - gap - aabbH,
            x1: ncx0 + aabbW / 2, y1: minY - gap },
          { x0: ncx0 - aabbW / 2, y0: maxY + gap,
            x1: ncx0 + aabbW / 2, y1: maxY + gap + aabbH },
          { x0: maxX + gap, y0: ncy0 - aabbH / 2,
            x1: maxX + gap + aabbW, y1: ncy0 + aabbH / 2 },
          { x0: minX - gap - aabbW, y0: ncy0 - aabbH / 2,
            x1: minX - gap, y1: ncy0 + aabbH / 2 },
          { x0: ncx0 - aabbW / 2, y0: minY - 3 * gap - aabbH,
            x1: ncx0 + aabbW / 2, y1: minY - 3 * gap },
          { x0: ncx0 - aabbW / 2, y0: maxY + 3 * gap,
            x1: ncx0 + aabbW / 2, y1: maxY + 3 * gap + aabbH },
          { x0: maxX + 3 * gap, y0: ncy0 - aabbH / 2,
            x1: maxX + 3 * gap + aabbW, y1: ncy0 + aabbH / 2 },
          { x0: minX - 3 * gap - aabbW, y0: ncy0 - aabbH / 2,
            x1: minX - 3 * gap, y1: ncy0 + aabbH / 2 },
        ];

        let fallbackName = null, bestHits = Infinity;
        for (const c of cands) {
          const ccx = (c.x0 + c.x1) / 2, ccy = (c.y0 + c.y1) / 2;
          if (ccx < 0 || ccx > W || ccy < 0 || ccy > H) continue;
          const hits = countHits(c, i);
          if (hits === 0) { chosenName = c; break; }
          if (hits < bestHits) { bestHits = hits; fallbackName = c; }
        }
        if (!chosenName) chosenName = fallbackName || cands[0];
      }

      allLabels.push({
        kind: "outside-name",
        rect: chosenName,
        anchor: sc,
        movable: true,
        text: nameStr,
        fontSize: outFS,
        rotationDeg: nameDeg,
        plateW, plateH,
      });
    }

    /* ---- 4d. relax ---- */
    relaxLabels(allLabels, 4);

    /* ---- 4e. draw ---- */
    for (const L of allLabels) {
      if (L.draw === false) continue;
      const r = L.rect;
      const cx = (r.x0 + r.x1) / 2;
      const cy = (r.y0 + r.y1) / 2;

      if (L.anchor) {
        const target = nearestPointOnTarget(cx, cy, L.anchor);
        if (target) {
          const d = Math.hypot(cx - target.x, cy - target.y);
          if (d > 3) {
            drawLabelLeader(g, r, target, L.fontSize, PNG_RES);
          }
        }
      }

      const rotRad = L.rotationDeg * Math.PI / 180;

      g.save();
      g.translate(cx, cy);
      g.rotate(rotRad);

      g.fillStyle = "#ffffff";
      g.fillRect(-L.plateW / 2, -L.plateH / 2, L.plateW, L.plateH);

      g.fillStyle = "#000000";
      g.font = `${L.kind === "inside-name" || L.kind === "outside-name"
                   ? "600" : "500"
                } ${L.fontSize.toFixed(1)}px 'JetBrains Mono', monospace`;
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText(L.text, 0, 0);

      g.restore();
    }

    cv.toBlob((blob) => {
      if (!blob) {
        flashStatus(T("msgPNGFailed"), "bad");
        resolve(false);
        return;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "room_boxes.png";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      flashStatus(T("msgPNGWritten")(W, H), "ok");
      resolve(true);
    }, "image/png");
  });
}
"""
