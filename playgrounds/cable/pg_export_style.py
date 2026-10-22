"""
pg_export_style.py — leader-style bipartite conflict graph.

ES module.  One export: `_buildStyleConflictGraph(placed, stripH,
topPad, trackOffsets)` — the adjacency map that drives the alternating
solid/dashed leader-style assignment in the vertex-label placement.

Two leaders conflict for style purposes when they are close enough,
parallel enough, and share enough horizontal projection that drawing
them in the same style would make it hard to tell where one ends and
the other begins.  The output is a per-placed-item adjacency map
suitable for a two-colour BFS.  Non-bipartite components fall back to
a dashed set — the caller handles that.

WHY A SEPARATE MODULE
=====================
The conflict graph is a read-only inspection of the laid-out leader
paths.  Keeping it out of `pg_export_labels.js` means the (already
long) placement module does not also carry a graph-theory helper, and
the graph-theory helper can be unit-tested on hand-built `placed`
arrays without spinning up a canvas.
"""

STYLE_JS = r"""
import {
  _buildLeaderPathRel, _bevelPath, _applyJogsToPath,
  _pathSegments,
  PILL_PROX, SEG_MIN_LEN,
} from "./pg_export_primitives.js";
import {
  _angleDiff,
  _segSegDist,
} from "./pg_export_geometry.js";

/* Tunables — small and separate from the drawing constants so that
   tightening them does not accidentally change the pill layout. */
const STYLE_ANGLE_TOL   = 0.28;
const STYLE_PROX        = 10.0;
const STYLE_MIN_OVERLAP = 6.0;

/* ==========================================================================
   PROJECTION OVERLAP
   ==========================================================================
   Two leaders in the same horizontal band are considered to share a
   "run" if the projection of the shorter one onto the longer one's
   direction exceeds STYLE_MIN_OVERLAP.  This filters out the very
   common case of two perpendicular leaders crossing once: they touch
   in the crossing, but they do not run alongside each other, and   alternating their styles would be pointless visual noise. */

function _segProjectionOverlap(a, b) {
  const dxA = a.bx - a.ax, dyA = a.by - a.ay;
  const lenA = Math.hypot(dxA, dyA) || 1;
  const ux = dxA / lenA, uy = dyA / lenA;

  const proj = (x, y) => (x - a.ax) * ux + (y - a.ay) * uy;
  const b0 = proj(b.ax, b.ay);
  const b1 = proj(b.bx, b.by);
  const lo = Math.min(b0, b1);
  const hi = Math.max(b0, b1);
  const overlap = Math.min(hi, lenA) - Math.max(lo, 0);
  return overlap > 0 ? overlap : 0;
}

/* ==========================================================================
   CONFLICT GRAPH
   ========================================================================== */

export function _buildStyleConflictGraph(placed, stripH, topPad, trackOffsets) {
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

/* ==========================================================================
   LAYOUT (local)
   ==========================================================================
   The full path builder is defined in `pg_export_snapshot.js` for
   the optimiser's use; this local version is the minimal subset the
   style graph needs, avoiding a circular import between style and
   snapshot.  The bodies are byte-identical to the primitives' path
   helpers — do not add logic here that the primitives don't have. */

function _buildLayout(placed, stripH, topPad, trackOffsets) {
  const n = placed.length;
  const paths = new Array(n);
  const segs  = new Array(n);
  for (let i = 0; i < n; i++) {
    const it = placed[i];
    let p;
    if (it.finalPath) {
      p = it.finalPath;
    } else {
      const base = _buildLeaderPathRel(it, placed, stripH, topPad, trackOffsets);
      const bev  = _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0);
      p          = _applyJogsToPath(bev, it);
    }
    paths[i] = p;
    segs[i]  = _pathSegments(p, i, SEG_MIN_LEN);
  }
  return { paths, segs };
}
"""
