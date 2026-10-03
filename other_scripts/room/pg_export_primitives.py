"""
pg_export_primitives.py — the drawing atoms every renderer shares.

Badge geometry, the arrow-style table, the path-trimming utilities,
the arrow sampler the legend uses, the shared label geometry
constants, and the anchor-side offset refiner.

    badgeRadiusFor / drawBadge       the numbered circle on the
                                     strip-header and the legend
    SEG_ARROW_STYLES / styleFor      the ten alternating arrow
                                     decorations, indexed by a wall
                                     segment's order badge
    drawArrowHead / drawStyledPath   the one arrow-drawing path
    drawArrowSample                  the legend's short horizontal
                                     arrow
    LABEL_* / LEADER_STYLES          the shared numeric constants
                                     the label placement and the
                                     pill draw both read
    refineLeaderOffsets              the pre-optimiser pass that
                                     fans out the anchor-side legs
                                     of overlapping leaders before
                                     the geometry optimiser runs

Nothing here depends on the scoring or optimiser modules; every
other module depends on this one for at least one primitive.
"""


PRIMITIVES_JS = r"""
/* ---- Badge ---- */

function badgeRadiusFor(n) {
  const digits = String(n).length;
  if (digits <= 1) return 11;
  if (digits === 2) return 12;
  return 13;
}

function drawBadge(c, x, y, num) {
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
  c.textAlign = "center"; c.textBaseline = "middle";
  c.fillText(String(num), x, y + 0.5);
  c.restore();
}

/* ---- Arrow styles ---- */

const SEG_ARROW_STYLES = [
  { dash: null,           head: "solid-tri"      },
  { dash: [6, 4],         head: "hollow-tri"     },
  { dash: [2, 3],         head: "solid-circle"   },
  { dash: null,           head: "hollow-diamond" },
  { dash: [8, 3, 2, 3],   head: "solid-tri"      },
  { dash: [10, 4],        head: "solid-square"   },
  { dash: null,           head: "bar"            },
  { dash: [2, 3],         head: "hollow-circle"  },
  { dash: [6, 4],         head: "solid-diamond"  },
  { dash: null,           head: "hollow-square"  },
];

function styleFor(order) {
  return SEG_ARROW_STYLES[(order - 1) % SEG_ARROW_STYLES.length];
}

function drawArrowHead(c, x, y, ux, uy, style) {
  const px = -uy, py = ux;
  c.save();
  c.lineJoin = "round";
  c.lineCap = "round";

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

function pathTotalLength(points) {
  let L = 0;
  for (let i = 1; i < points.length; i++) {
    L += Math.hypot(points[i][0] - points[i-1][0],
                    points[i][1] - points[i-1][1]);
  }
  return L;
}

function trimPathStart(points, trimLen) {
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

function trimPathEnd(points, trimLen) {
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

function drawStyledPath(c, points, style, gapStart) {
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

function drawArrowSample(c, x, y, len, style) {
  drawStyledPath(c, [[x, y], [x + len, y]], style, 0);
}

/* ---- Vertex label geometry constants (shared) ---- */

const LABEL_CHANNEL_Y0   = 6;
const LABEL_CHANNEL_STEP = 8;
const LABEL_CHANNEL_GAP  = 6;
const LABEL_BOTTOM_PAD   = 4;

/* Minimum horizontal separation between a pill's left-aligned label
   (the "V<n>" name on the first vertex, or empty) and its right-aligned
   "h <cm>" height.  Ensures they never touch when the pill is only as
   wide as the widest of the two columns. */
const LABEL_LEFT_RIGHT_GAP = 18;

const LEADER_STYLES = [
  { dash: null   },
  { dash: [6, 4] },
];

/* ---- Leader offset refinement ---- */

function refineLeaderOffsets(placed, topPad, trackOffsets, stripH) {
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
