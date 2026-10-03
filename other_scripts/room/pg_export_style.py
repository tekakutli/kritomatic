"""
pg_export_style.py — the style-conflict graph.

The parallel-proximity detector that decides which leaders lose
their solid stroke to make every pair visually separable.

A style conflict is two leaders whose rendered polylines come within
STYLE_PROX pixels of each other *while running parallel*.  Crossings
are excluded: two leaders that merely cross are unambiguously
distinguishable and need no alternation.

Two segments count as parallel-close when all of:

    • their angle difference is under STYLE_ANGLE_TOL (30°),
    • their minimum distance is under STYLE_PROX (10 px),
    • their projection overlap along the common direction is at
      least STYLE_MIN_OVERLAP (12 px).

The graph is built from the FINAL leader polylines — offsets, dive
modes, bevels, jogs, channel-Y slides and pill pushes included —
because it must see exactly what the renderer will draw.

The vertex cover that consumes this graph lives in
computeVertexLabelPlacement, in pg_export_labels.py — it is a
label-placement concern (the style assignment is written onto each
pill on the way out), so it stays with the label pass.
"""


STYLE_JS = r"""
/* ---- Style conflict detection ---- */

const STYLE_ANGLE_TOL   = Math.PI / 6;
const STYLE_PROX        = 10.0;
const STYLE_MIN_OVERLAP = 12.0;

function _segProjectionOverlap(A, B) {
  const lenA = Math.hypot(A.bx - A.ax, A.by - A.ay);
  const lenB = Math.hypot(B.bx - B.ax, B.by - B.ay);
  if (lenA < 1e-6 || lenB < 1e-6) return 0;
  let ux = (A.bx - A.ax) / lenA + (B.bx - B.ax) / lenB;
  let uy = (A.by - A.ay) / lenA + (B.by - B.ay) / lenB;
  const dLen = Math.hypot(ux, uy);
  if (dLen < 1e-6) return 0;
  ux /= dLen; uy /= dLen;
  const a0 = A.ax * ux + A.ay * uy;
  const a1 = A.bx * ux + A.by * uy;
  const b0 = B.ax * ux + B.ay * uy;
  const b1 = B.bx * ux + B.by * uy;
  const aLo = Math.min(a0, a1), aHi = Math.max(a0, a1);
  const bLo = Math.min(b0, b1), bHi = Math.max(b0, b1);
  return Math.max(0, Math.min(aHi, bHi) - Math.max(aLo, bLo));
}

function _buildStyleConflictGraph(placed, stripH, topPad, trackOffsets) {
  const layout = _buildLayout(placed, stripH, topPad, trackOffsets);
  const segsByPath = layout.segs;

  const adj = new Map();
  for (const it of placed) adj.set(it, new Set());

  for (let i = 0; i < segsByPath.length; i++) {
    for (let k = 0; k < segsByPath[i].length; k++) {
      const A = segsByPath[i][k];
      if (A.len < 2.0) continue;
      for (let j = i + 1; j < segsByPath.length; j++) {
        for (let l = 0; l < segsByPath[j].length; l++) {
          const B = segsByPath[j][l];
          if (B.len < 2.0) continue;
          if (_angleDiff(A.angle, B.angle) > STYLE_ANGLE_TOL) continue;
          if (A.maxX + STYLE_PROX < B.minX) continue;
          if (B.maxX + STYLE_PROX < A.minX) continue;
          if (A.maxY + STYLE_PROX < B.minY) continue;
          if (B.maxY + STYLE_PROX < A.minY) continue;
          const d = _segSegDist(A.ax, A.ay, A.bx, A.by,
                                B.ax, B.ay, B.bx, B.by);
          if (d >= STYLE_PROX) continue;
          if (_segProjectionOverlap(A, B) < STYLE_MIN_OVERLAP) continue;
          adj.get(placed[i]).add(placed[j]);
          adj.get(placed[j]).add(placed[i]);
        }
      }
    }
  }
  return adj;
}
"""
