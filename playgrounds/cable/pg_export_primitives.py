"""
pg_export_primitives.py — drawing atoms every renderer shares, plus
the leader-path builder that used to live in the optimizer.

ES module.  Contents:

    badgeRadiusFor / drawBadge
        the numbered circle on the strip-header and the legend.

    SEG_ARROW_STYLES / styleFor
        the ten alternating arrow decorations, indexed by a wall
        segment's order badge.

    drawArrowHead / drawStyledPath
        the one arrow-drawing path.

    drawArrowSample
        the legend's short horizontal arrow.

    pathTotalLength / trimPathStart / trimPathEnd
        path arithmetic for the styled path.

    LABEL_CHANNEL_Y0 / STEP / GAP / BOTTOM_PAD / LEFT_RIGHT_GAP
    LEADER_STYLES
        the shared numeric constants the label placement and the pill
        draw both read.

    refineLeaderOffsets
        the pre-optimiser pass that fans out the anchor-side legs of
        overlapping leaders before the geometry optimiser runs.

    LEADER PATH — RELOCATED
    `computeLeaderPath` and its bevel / jog helpers were defined in
    `pg_export_optimizer.py` in the concatenation model.  They are
    pure math over primitives with no optimizer state, and moving
    them down here breaks the cycle

        penalties → optimizer → snapshot → penalties

    that the ES-module boundary makes structural.  The bodies are
    unchanged.

    RE-EXPORTS FROM GEOMETRY
    Four symbols from `pg_export_geometry.js` are re-exported here so
    that downstream modules can pull everything they need from this
    one module:

        PILL_PROX         — segment-vs-pill clearance
        SEG_MIN_LEN       — minimum segment length for collision
                            counting
        _segBoxOverlap    — segment vs. AABB overlap test
        _pathSegments     — polyline → segment list

    The re-exports used to sit at the bottom of the file in their own
    `import ...; export const ...;` statement pairs, grouped only by
    accident of where the original author put them.  They are now
    consolidated into the single geometry import at the top of the
    file, with a single `export { ... }` statement immediately after
    it.  This matters because `pg_export_labels.js` and
    `pg_export_pillpush.js` import all four of these symbols from
    this module; if any one of them ever failed to resolve the whole
    labels module would fail to load and the export would render
    without a single pill or leader — exactly the failure the user
    was seeing.
"""

PRIMITIVES_JS = r"""
import { FONT_MONO } from "./pg_state.js";
import {
  PILL_PROX, SEG_MIN_LEN,
  _segBoxOverlap, _pathSegments,
} from "./pg_export_geometry.js";

export { PILL_PROX, SEG_MIN_LEN, _segBoxOverlap, _pathSegments };

/* ==========================================================================
   BADGE
   ========================================================================== */

export function badgeRadiusFor(n) {
  const digits = String(n).length;
  if (digits <= 1) return 11;
  if (digits === 2) return 12;
  return 13;
}

export function drawBadge(c, x, y, num) {
  const r = badgeRadiusFor(num);
  c.save();
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.fillStyle = "#ffffff";
  c.fill();
  c.strokeStyle = "#000000";
  c.lineWidth = 1.6;
  c.stroke();
  c.font = "700 12px " + FONT_MONO;
  c.fillStyle = "#000000";
  c.textAlign = "center";
  c.textBaseline = "middle";
  c.fillText(String(num), x, y + 0.5);
  c.restore();
}

/* ==========================================================================
   ARROW STYLES
   ========================================================================== */

export const SEG_ARROW_STYLES = [
  { dash: null,         head: "solid-tri"      },
  { dash: [6, 4],       head: "hollow-tri"     },
  { dash: [2, 3],       head: "solid-circle"   },
  { dash: null,         head: "hollow-diamond" },
  { dash: [8, 3, 2, 3], head: "solid-tri"      },
  { dash: [10, 4],      head: "solid-square"   },
  { dash: null,         head: "bar"            },
  { dash: [2, 3],       head: "hollow-circle"  },
  { dash: [6, 4],       head: "solid-diamond"  },
  { dash: null,         head: "hollow-square"  },
];

export function styleFor(order) {
  return SEG_ARROW_STYLES[(order - 1) % SEG_ARROW_STYLES.length];
}

export function drawArrowHead(c, x, y, ux, uy, style) {
  const px = -uy, py = ux;
  c.save();
  c.lineJoin = "round";
  c.lineCap  = "round";

  const drawSilhouette = () => {
    c.beginPath();
    switch (style.head) {
      case "solid-tri":
      case "hollow-tri": {
        const s = 8, w = 4.5;
        c.moveTo(x, y);
        c.lineTo(x - ux * s + px * w, y - uy * s + py * w);
        c.lineTo(x - ux * s - px * w, y - uy * s - py * w);
        c.closePath();
        break;
      }
      case "solid-circle":
      case "hollow-circle": {
        c.arc(x, y, 4.5, 0, Math.PI * 2);
        break;
      }
      case "solid-diamond":
      case "hollow-diamond": {
        const s = 5.5;
        c.moveTo(x + ux * s, y + uy * s);
        c.lineTo(x + px * s, y + py * s);
        c.lineTo(x - ux * s, y - uy * s);
        c.lineTo(x - px * s, y - py * s);
        c.closePath();
        break;
      }
      case "solid-square":
      case "hollow-square": {
        const s = 4;
        c.moveTo(x + ux * s + px * s, y + uy * s + py * s);
        c.lineTo(x + ux * s - px * s, y + uy * s - py * s);
        c.lineTo(x - ux * s - px * s, y - uy * s - py * s);
        c.lineTo(x - ux * s + px * s, y - uy * s + py * s);
        c.closePath();
        break;
      }
      case "bar": {
        const s = 5.5;
        c.moveTo(x + px * s, y + py * s);
        c.lineTo(x - px * s, y - py * s);
        break;
      }
    }
  };

  drawSilhouette();
  c.strokeStyle = "#ffffff";
  c.lineWidth = 5;
  c.stroke();

  c.strokeStyle = "#000000";
  c.lineWidth = 1.6;
  drawSilhouette();

  switch (style.head) {
    case "solid-tri":
    case "solid-circle":
    case "solid-diamond":
    case "solid-square": {
      c.fillStyle = "#000000";
      c.fill();
      break;
    }
    case "hollow-tri":
    case "hollow-circle":
    case "hollow-diamond":
    case "hollow-square": {
      c.fillStyle = "#ffffff";
      c.fill();
      c.stroke();
      break;
    }
    case "bar": {
      c.stroke();
      break;
    }
  }
  c.restore();
}

/* ==========================================================================
   PATH ARITHMETIC
   ========================================================================== */

export function pathTotalLength(points) {
  let L = 0;
  for (let i = 1; i < points.length; i++) {
    L += Math.hypot(points[i][0] - points[i-1][0],
                    points[i][1] - points[i-1][1]);
  }
  return L;
}

export function trimPathStart(points, trimLen) {
  if (trimLen <= 0 || points.length < 2) return points;
  const total = pathTotalLength(points);
  if (total <= trimLen) return [points[points.length - 1]];
  let acc = 0;
  for (let i = 1; i < points.length; i++) {
    const seg = Math.hypot(points[i][0] - points[i-1][0],
                            points[i][1] - points[i-1][1]);
    if (acc + seg >= trimLen) {
      const t = (trimLen - acc) / seg;
      const x = points[i-1][0] + (points[i][0] - points[i-1][0]) * t;
      const y = points[i-1][1] + (points[i][1] - points[i-1][1]) * t;
      return [[x, y], ...points.slice(i)];
    }
    acc += seg;
  }
  return points;
}

export function trimPathEnd(points, trimLen) {
  if (trimLen <= 0 || points.length < 2) return points;
  const total = pathTotalLength(points);
  if (total <= trimLen) return [points[0]];
  const target = total - trimLen;
  let acc = 0;
  for (let i = 1; i < points.length; i++) {
    const seg = Math.hypot(points[i][0] - points[i-1][0],
                            points[i][1] - points[i-1][1]);
    if (acc + seg >= target) {
      const t = (target - acc) / seg;
      const x = points[i-1][0] + (points[i][0] - points[i-1][0]) * t;
      const y = points[i-1][1] + (points[i][1] - points[i-1][1]) * t;
      return [...points.slice(0, i), [x, y]];
    }
    acc += seg;
  }
  return points;
}

export function drawStyledPath(c, points, style, gapStart) {
  if (points.length < 2) return;
  const HEAD_SIZE = 9;
  const total = pathTotalLength(points);

  if (total <= HEAD_SIZE + 1) {
    const tip  = points[points.length - 1];
    const prev = points[points.length - 2];
    const dx = tip[0] - prev[0], dy = tip[1] - prev[1];
    const d = Math.hypot(dx, dy);
    if (d < 0.1) return;
    drawArrowHead(c, tip[0], tip[1], dx / d, dy / d, style);
    return;
  }

  let pts = points.slice();
  if (gapStart > 0) pts = trimPathStart(pts, gapStart);
  if (pts.length < 2) return;

  const tip = pts[pts.length - 1];
  const beforeTip = pts[pts.length - 2];
  const dxT = tip[0] - beforeTip[0], dyT = tip[1] - beforeTip[1];
  const dT = Math.hypot(dxT, dyT);
  if (dT < 0.1) return;
  const ux = dxT / dT, uy = dyT / dT;

  const body = trimPathEnd(pts, HEAD_SIZE);
  if (body.length < 2) {
    drawArrowHead(c, tip[0], tip[1], ux, uy, style);
    return;
  }

  c.save();
  c.strokeStyle = "#000000";
  c.lineWidth = 1.4;
  c.lineCap = "round";
  c.lineJoin = "round";
  c.setLineDash(style.dash || []);
  c.beginPath();
  c.moveTo(body[0][0], body[0][1]);
  for (let i = 1; i < body.length; i++) {
    c.lineTo(body[i][0], body[i][1]);
  }
  c.stroke();
  c.setLineDash([]);
  c.restore();

  drawArrowHead(c, tip[0], tip[1], ux, uy, style);
}

export function drawArrowSample(c, x, y, len, style) {
  drawStyledPath(c, [[x, y], [x + len, y]], style, 0);
}

/* ==========================================================================
   LABEL CONSTANTS
   ========================================================================== */

export const LABEL_CHANNEL_Y0   = 6;
export const LABEL_CHANNEL_STEP = 8;
export const LABEL_CHANNEL_GAP  = 6;
export const LABEL_BOTTOM_PAD   = 4;

export const LABEL_LEFT_RIGHT_GAP = 18;

export const LEADER_STYLES = [
  { dash: null   },
  { dash: [6, 4] },
];

/* ==========================================================================
   LEADER PATH (relocated from optimizer)
   ==========================================================================
   These were in `pg_export_optimizer.py` in the concatenation model.
   Moving them into primitives breaks the penalties → optimizer →
   snapshot → penalties cycle.  Bodies are unchanged. */

export const BEVEL_STEP      = 3.0;
export const BEVEL_MAX       = 45.0;
export const BEVEL_MIN_APPLY = 6.0;
export const JOG_STEP        = 3.0;
export const JOG_MAX         = 24.0;
export const JOG_MIN_APPLY   = 6.0;

export function _buildChannelToPill(descentX, pillX, chanY, pillTopY,
                                     self, placed, stripBottom, topPad,
                                     trackOffsets) {
  const M = 4;
  const EPS = 2;
  const bias = (self && self.detourBias) || 0;

  const boxes = [];
  for (const Q of placed) {
    if (Q === self) continue;
    const qT = stripBottom + topPad + trackOffsets[Q.track];
    const qB = qT + Q.h;
    if (qB <= chanY) continue;
    if (qT >= pillTopY) continue;
    boxes.push({
      qL: Q.pillCenterX - Q.w / 2,
      qR: Q.pillCenterX + Q.w / 2,
      qT, qB,
    });
  }

  const blockers = [];
  for (const b of boxes) {
    if (descentX >= b.qL - EPS && descentX <= b.qR + EPS) blockers.push(b);
  }
  if (!blockers.length) {
    const out = [[descentX, pillTopY]];
    if (Math.abs(descentX - pillX) > 0.5) {
      out.push([pillX, pillTopY]);
    }
    return out;
  }

  blockers.sort((a, b) => a.qT - b.qT);
  const bands = [];
  let cur = { qT: blockers[0].qT, qB: blockers[0].qB,
              qL: blockers[0].qL, qR: blockers[0].qR };
  for (let i = 1; i < blockers.length; i++) {
    const b = blockers[i];
    if (b.qT <= cur.qB + 2 * M) {
      cur.qB = Math.max(cur.qB, b.qB);
      cur.qL = Math.min(cur.qL, b.qL);
      cur.qR = Math.max(cur.qR, b.qR);
    } else {
      bands.push(cur);
      cur = { qT: b.qT, qB: b.qB, qL: b.qL, qR: b.qR };
    }
  }
  bands.push(cur);

  const hClear = (y, xa, xb) => {
    const lo = Math.min(xa, xb), hi = Math.max(xa, xb);
    for (const b of boxes) {
      if (b.qT - EPS > y || b.qB + EPS < y) continue;
      if (b.qR < lo || b.qL > hi) continue;
      return false;
    }
    return true;
  };

  const vClear = (x, ya, yb) => {
    const lo = Math.min(ya, yb), hi = Math.max(ya, yb);
    for (const b of boxes) {
      if (b.qL - EPS > x || b.qR + EPS < x) continue;
      if (b.qB <= lo || b.qT >= hi) continue;
      return false;
    }
    return true;
  };

  const out = [];
  let curY = chanY;

  for (const band of bands) {
    const entryY = Math.max(curY, band.qT - M);
    const exitY  = Math.min(pillTopY, band.qB + M);
    if (entryY >= exitY - 0.5) continue;

    if (entryY > curY + 0.5) {
      out.push([descentX, entryY]);
    }

    const leftX  = band.qL - M;
    const rightX = band.qR + M;
    const preferRight = (rightX - descentX) < (descentX - leftX);
    const tryOrder = preferRight ? [rightX, leftX] : [leftX, rightX];

    let detourX = null;
    for (const base of tryOrder) {
      const dir = base > descentX ? 1 : -1;
      let probe = base;
      for (let k = 0; k < 60; k++) {
        if (vClear(probe, entryY, exitY) &&
            hClear(entryY, descentX, probe) &&
            hClear(exitY,  descentX, probe)) {
          detourX = probe;
          break;
        }
        probe += dir * 6;
      }
      if (detourX !== null) break;
    }
    if (detourX === null) {
      detourX = preferRight ? rightX : leftX;
    }

    if (bias !== 0) {
      const biased = detourX + bias;
      if (vClear(biased, entryY, exitY) &&
          hClear(entryY, descentX, biased) &&
          hClear(exitY,  descentX, biased)) {
        detourX = biased;
      }
    }

    out.push([detourX, entryY]);
    out.push([detourX, exitY]);
    out.push([descentX, exitY]);
    curY = exitY;
  }

  if (pillTopY > curY + 0.5) {
    out.push([descentX, pillTopY]);
  }
  if (Math.abs(descentX - pillX) > 0.5) {
    out.push([pillX, pillTopY]);
  }
  return out;
}

export function computeLeaderPath(anchorX, anchorY, offA, offP, chanY,
                                  pillX, pillTopY,
                                  self, placed, stripBottom, topPad,
                                  trackOffsets, diveMode) {
  const mode = diveMode || 0;
  const descentX = pillX + (offP || 0);
  const path = [];
  path.push([anchorX, anchorY]);
  if (mode === 1) {
    path.push([anchorX, chanY]);
    path.push([descentX, chanY]);
  } else if (mode === 2) {
    path.push([descentX, anchorY]);
    path.push([descentX, chanY]);
  } else {
    path.push([anchorX + offA, chanY]);
    path.push([descentX,       chanY]);
  }

  const tail = _buildChannelToPill(descentX, pillX, chanY, pillTopY,
                                    self, placed, stripBottom,
                                    topPad, trackOffsets);
  for (const p of tail) path.push(p);
  return path;
}

export function _buildLeaderPathRel(it, placed, stripH, topPad, trackOffsets) {
  const anchorY  = it.anchorCyRel;
  const chanY    = stripH + it.channelYRel;
  const pillTopY = stripH + topPad + trackOffsets[it.track];
  return computeLeaderPath(
    it.anchorCx, anchorY, it.offsetA || 0, it.offsetP || 0,
    chanY, it.pillCenterX, pillTopY,
    it, placed, stripH, topPad, trackOffsets, it.diveMode || 0);
}

export function _bevelPath(path, b0, b1) {
  const n = path.length;
  if (n < 4) return path;
  if (b0 < BEVEL_MIN_APPLY && b1 < BEVEL_MIN_APPLY) return path;

  const out = [path[0]];

  const A   = path[0];
  const C1  = path[1];
  const N1  = path[2];
  const lenA = Math.hypot(C1[0] - A[0],  C1[1] - A[1]);
  const lenB = Math.hypot(N1[0] - C1[0], N1[1] - C1[1]);
  const b0u  = Math.min(b0, lenA * 0.85, lenB * 0.85);
  if (b0u >= BEVEL_MIN_APPLY && lenA > 1e-6 && lenB > 1e-6) {
    const t1 = (lenA - b0u) / lenA;
    const t2 = b0u / lenB;
    out.push([A[0]  + (C1[0] - A[0])  * t1,
              A[1]  + (C1[1] - A[1])  * t1]);
    out.push([C1[0] + (N1[0] - C1[0]) * t2,
              C1[1] + (N1[1] - C1[1]) * t2]);
  } else {
    out.push(C1);
  }

  for (let i = 2; i <= n - 3; i++) out.push(path[i]);

  const M2  = path[n - 3];
  const C2  = path[n - 2];
  const P   = path[n - 1];
  const lenC = Math.hypot(C2[0] - M2[0], C2[1] - M2[1]);
  const lenD = Math.hypot(P[0]  - C2[0], P[1]  - C2[1]);
  const b1u  = Math.min(b1, lenC * 0.85, lenD * 0.85);
  if (b1u >= BEVEL_MIN_APPLY && lenC > 1e-6 && lenD > 1e-6) {
    const t1 = (lenC - b1u) / lenC;
    const t2 = b1u / lenD;
    out.push([M2[0] + (C2[0] - M2[0]) * t1,
              M2[1] + (C2[1] - M2[1]) * t1]);
    out.push([C2[0] + (P[0]  - C2[0]) * t2,
              C2[1] + (P[1]  - C2[1]) * t2]);
  } else {
    out.push(C2);
  }

  out.push(P);
  return out;
}

export function _jogSegment(points, segIdx, shift) {
  if (!shift || Math.abs(shift) < JOG_MIN_APPLY) return points;
  const n = points.length;
  if (segIdx < 0 || segIdx >= n - 1) return points;
  const A = points[segIdx];
  const B = points[segIdx + 1];
  if (Math.abs(A[0] - B[0]) > 0.5) return points;
  const d  = Math.abs(shift);
  const X  = A[0];
  const yA = A[1], yB = B[1];
  const L  = Math.abs(yB - yA);
  if (L < 4 * d) return points;
  const sgn = (yB > yA) ? 1 : -1;
  const M1 = [X + shift, yA + sgn * d];
  const M2 = [X + shift, yB - sgn * d];
  return points.slice(0, segIdx + 1)
    .concat([M1, M2], points.slice(segIdx + 1));
}

export function _applyJogsToPath(path, it) {
  let p = path;

  const anchorEndX = (it.anchorCx || 0) + (it.offsetA || 0);
  const fwd = Math.sign((it.pillCenterX || 0) - anchorEndX);

  const jogAllowed = (jog) => {
    if (fwd === 0) return true;
    if (jog === 0) return true;
    return Math.sign(jog) === fwd;
  };

  if (it.jog0 && Math.abs(it.jog0) >= JOG_MIN_APPLY && p.length >= 2
      && jogAllowed(it.jog0)) {
    p = _jogSegment(p, 0, it.jog0);
  }
  if (it.jog1 && Math.abs(it.jog1) >= JOG_MIN_APPLY && p.length >= 2
      && jogAllowed(it.jog1)) {
    p = _jogSegment(p, p.length - 2, it.jog1);
  }
  return p;
}

export function _candidatesFor(cur, isBevel) {
  const step     = isBevel ? BEVEL_STEP : JOG_STEP;
  const maxV     = isBevel ? BEVEL_MAX  : JOG_MAX;
  const minV     = isBevel ? BEVEL_MIN_APPLY : JOG_MIN_APPLY;
  const allowNeg = !isBevel;

  const set = new Set();

  const snap = (v) => {
    if (v === 0) return 0;
    if (!allowNeg && v < 0) return null;
    const a = Math.abs(v);
    if (a < minV) return null;
    const c = Math.min(maxV, a);
    return allowNeg ? Math.sign(v) * c : c;
  };

  const s1 = snap(cur + step);
  const s2 = snap(cur - step);
  if (s1 !== null) set.add(s1);
  if (s2 !== null) set.add(s2);

  if (cur === 0) {
    set.add(minV);
    if (allowNeg) set.add(-minV);
  }

  if (cur !== 0) set.add(0);

  set.delete(cur);
  return set;
}

/* ==========================================================================
   REFINE LEADER OFFSETS
   ========================================================================== */

export function refineLeaderOffsets(placed, topPad, trackOffsets, stripH) {
  const MIN_SEP    = 3.0;
  const FAN_STEP   = 4.0;
  const MAX_OFFSET = 14.0;
  const MAX_PASSES = 20;

  if (!placed) return;
  for (const it of placed) {
    it.offsetA = 0;
    it.offsetP = 0;
  }
  if (placed.length < 2) return;

  for (const it of placed) {
    it._anchorYRel = it.anchorCyRel - stripH;
    it._chanYRel   = it.channelYRel;
  }

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const yOverlap = (a0, a1, b0, b1) =>
    Math.max(a0, b0) < Math.min(a1, b1) - 0.5;

  const anchorXAt = (it, y) => {
    const denom = it._chanYRel - it._anchorYRel;
    const t = denom > 1e-6 ? (y - it._anchorYRel) / denom : 0;
    return it.anchorCx + it.offsetA * t;
  };

  const anchorConflict = (A, B) => {
    if (!yOverlap(A._anchorYRel, A._chanYRel, B._anchorYRel, B._chanYRel))
      return false;
    const y0 = Math.max(A._anchorYRel, B._anchorYRel);
    const y1 = Math.min(A._chanYRel,   B._chanYRel);
    for (let s = 0; s <= 5; s++) {
      const y = y0 + (y1 - y0) * s / 5;
      if (Math.abs(anchorXAt(A, y) - anchorXAt(B, y)) < MIN_SEP) return true;
    }
    return false;
  };

  for (let pass = 0; pass < MAX_PASSES; pass++) {
    let anyChange = false;
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const A = placed[i], B = placed[j];
        if (anchorConflict(A, B)) {
          const dir = (B.anchorCx >= A.anchorCx) ? 1 : -1;
          const nA = clamp(A.offsetA - dir * FAN_STEP * 0.5,
                           -MAX_OFFSET, MAX_OFFSET);
          const nB = clamp(B.offsetA + dir * FAN_STEP * 0.5,
                           -MAX_OFFSET, MAX_OFFSET);
          if (Math.abs(nA - A.offsetA) > 1e-6) { A.offsetA = nA; anyChange = true; }
          if (Math.abs(nB - B.offsetA) > 1e-6) { B.offsetA = nB; anyChange = true; }
        }
      }
    }
    if (!anyChange) break;
  }

  for (const it of placed) {
    const h = it._chanYRel - it._anchorYRel;
    const cap = Math.min(MAX_OFFSET, Math.max(0, h * 0.5));
    it.offsetA = clamp(it.offsetA, -cap, cap);
  }
}
"""
