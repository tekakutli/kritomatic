"""
pg_arrows_scoring.py — the per-side score and the attack-fan detector.

ES module.  Two exports:

    _raScoreSide        the seven-term total the solver minimises:
                        parallel, crossing, tipPenalty, attackParallel,
                        bundlePenalty, groupPenalty, clusterPenalty

    _raFindAttackFans   fan-shaped attack bundles — groups of routes
                        whose final attack segments are parallel,
                        overlapping, and close.  The escape pass in
                        pg_arrows_solver uses this.

Also exports `_raClusterPenalty` because the solver's refinement chooser
reads it to decide whether to invoke `enableClusterAttack`.

Imports from `pg_arrows_tuning.js` (constants + `arrowsState`) and
from `pg_arrows_geometry.js` (pair scoring + tip clearance + proper
crossing).  `arrowsState.frozenPolylines` is the escape-pass constraint
— a list of foreign polylines a candidate must not hug — and is read
on every `_raScoreSide` call.
"""

SCORING_JS = r"""
import {
  ROUTE_PARALLEL_TOL, ROUTE_PARALLEL_W,
  ROUTE_MIN_SEP,
  ROUTE_LAST_SEG_MIN,
  ROUTE_CLUSTER_MIN_SIZE, ROUTE_CLUSTER_LONG_MULT,
  ROUTE_CLUSTER_LONG_W,
  ROUTE_ESCAPE_ANGLE_TOL, ROUTE_ESCAPE_PROX,
  ROUTE_ESCAPE_MIN_OVERLAP,
  arrowsState,
} from "./pg_arrows_tuning.js";
import {
  _raPathPairScore, _raPathSegments,
  _raSegSegDist, _raAngleDiff, _raProjOverlap,
  _raTipClearanceAgainstSegs,
} from "./pg_arrows_geometry.js";

/* ==========================================================================
   SIDE SCORE
   ========================================================================== */

export function _raScoreSide(side) {
  const byRoute = side.routes;
  let parallel = 0, crossing = 0;

  for (let i = 0; i < byRoute.length; i++) {
    const a = byRoute[i];
    if (!a.ownPts) continue;
    for (let j = i + 1; j < byRoute.length; j++) {
      const b = byRoute[j];
      if (!b.ownPts) continue;
      if (a.bundleIdx === b.bundleIdx && a.groupIdx === b.groupIdx) continue;

      const sameCluster = (a.clusterAttack && b.clusterAttack &&
                           a.clusterId >= 0 && a.clusterId === b.clusterId);
      const s = _raPathPairScore(a.ownPts, b.ownPts);
      if (sameCluster) {
        crossing += s.c;
      } else {
        parallel += s.p;
        crossing += s.c;
      }
    }
  }

  if (arrowsState.frozenPolylines) {
    for (let i = 0; i < byRoute.length; i++) {
      const a = byRoute[i];
      if (!a.ownPts) continue;
      for (const fpts of arrowsState.frozenPolylines) {
        const s = _raPathPairScore(a.ownPts, fpts);
        parallel += s.p;
        crossing += s.c;
      }
    }
  }

  let attackParallel = 0;
  for (const b of side.bundles) {
    for (const g of b.groups) {
      const attacks = [];
      for (const ri of g.routeIndices) {
        const r = side.routes[ri];
        if (!r.ownPts || r.ownPts.length < 2) continue;
        const a = r.ownPts[r.ownPts.length - 2];
        const c = r.ownPts[r.ownPts.length - 1];
        attacks.push({
          x0: a[0], y0: a[1], x1: c[0], y1: c[1],
          angle: Math.atan2(c[1] - a[1], c[0] - a[0]),
          minX: Math.min(a[0], c[0]), maxX: Math.max(a[0], c[0]),
          minY: Math.min(a[1], c[1]), maxY: Math.max(a[1], c[1]),
        });
      }
      for (let i = 0; i < attacks.length; i++) {
        const a = attacks[i];
        for (let j = i + 1; j < attacks.length; j++) {
          const c = attacks[j];
          if (_raAngleDiff(a.angle, c.angle) > ROUTE_PARALLEL_TOL) continue;
          if (a.maxX + ROUTE_MIN_SEP < c.minX) continue;
          if (c.maxX + ROUTE_MIN_SEP < a.minX) continue;
          if (a.maxY + ROUTE_MIN_SEP < c.minY) continue;
          if (c.maxY + ROUTE_MIN_SEP < a.minY) continue;
          const d = _raSegSegDist(a.x0, a.y0, a.x1, a.y1,
                                  c.x0, c.y0, c.x1, c.y1);
          if (d < ROUTE_MIN_SEP) {
            attackParallel += (ROUTE_MIN_SEP - d) * ROUTE_PARALLEL_W;
          }
        }
      }
    }
  }

  const segsByRoute = byRoute.map(r =>
    r.ownPts ? _raPathSegments(r.ownPts) : []);
  let tipPenalty = 0;
  for (let i = 0; i < byRoute.length; i++) {
    const a = byRoute[i];
    if (!a.ownPts) continue;
    for (let j = 0; j < byRoute.length; j++) {
      if (i === j) continue;
      const b = byRoute[j];
      if (!b.ownPts) continue;
      if (a.clusterAttack && b.clusterAttack &&
          a.clusterId >= 0 && a.clusterId === b.clusterId) continue;
      tipPenalty += _raTipClearanceAgainstSegs(a.planX, a.planY, segsByRoute[j]);
    }
  }

  let bundlePenalty = 0;
  for (let i = 0; i < side.bundles.length; i++) {
    for (let j = i + 1; j < side.bundles.length; j++) {
      const dy = Math.abs(i - j) * side.bundleSpacing;
      if (dy < ROUTE_MIN_SEP * 1.5) {
        bundlePenalty += (ROUTE_MIN_SEP * 1.5 - dy) * ROUTE_PARALLEL_W;
      }
    }
  }

  let groupPenalty = 0;
  for (const b of side.bundles) {
    const uniqueX = [];
    for (const g of b.groups) {
      if (g.routeIndices.length === 0) continue;
      let isNew = true;
      for (const sx of uniqueX) {
        if (Math.abs(sx - g.descentX) < 0.5) { isNew = false; break; }
      }
      if (isNew) uniqueX.push(g.descentX);
    }
    for (let i = 0; i < uniqueX.length; i++) {
      for (let j = i + 1; j < uniqueX.length; j++) {
        const dx = Math.abs(uniqueX[i] - uniqueX[j]);
        if (dx < ROUTE_MIN_SEP) {
          groupPenalty += (ROUTE_MIN_SEP - dx) * ROUTE_PARALLEL_W;
        }
      }
    }
  }

  const clusterPenalty = _raClusterPenalty(side);

  return {
    parallel, crossing, tipPenalty, attackParallel, bundlePenalty,
    groupPenalty, clusterPenalty,
    total: parallel + crossing + tipPenalty + attackParallel +
           bundlePenalty + groupPenalty + clusterPenalty,
  };
}

export function _raClusterPenalty(side) {
  let penalty = 0;
  const clusters = side.clusters || [];
  const routes = side.routes;

  for (const cluster of clusters) {
    let longAttackCount = 0;

    for (const ri of cluster) {
      const r = routes[ri];
      if (r.clusterAttack) continue;
      if (!r.ownPts || r.ownPts.length < 2) continue;
      const p0 = r.ownPts[r.ownPts.length - 2];
      const p1 = r.ownPts[r.ownPts.length - 1];
      const len = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
      if (len > ROUTE_LAST_SEG_MIN * ROUTE_CLUSTER_LONG_MULT) {
        longAttackCount++;
      }
    }

    let tipConflictCount = 0;
    const unflagged = [];
    for (const ri of cluster) {
      const r = routes[ri];
      if (r.clusterAttack) continue;
      if (!r.ownPts) continue;
      unflagged.push(ri);
    }
    for (let i = 0; i < unflagged.length; i++) {
      const a = routes[unflagged[i]];
      const aSegs = _raPathSegments(a.ownPts);
      for (let j = i + 1; j < unflagged.length; j++) {
        const b = routes[unflagged[j]];
        const bSegs = _raPathSegments(b.ownPts);
        let conflict = false;
        if (_raTipClearanceAgainstSegs(a.planX, a.planY, bSegs) > 0) {
          conflict = true;
        } else if (_raTipClearanceAgainstSegs(b.planX, b.planY, aSegs) > 0) {
          conflict = true;
        }
        if (conflict) tipConflictCount++;
      }
    }

    const signal = longAttackCount + tipConflictCount;
    if (signal < ROUTE_CLUSTER_MIN_SIZE) continue;
    penalty += (signal - ROUTE_CLUSTER_MIN_SIZE + 1) *
               ROUTE_CLUSTER_LONG_W;
  }
  return penalty;
}

/* ==========================================================================
   ATTACK-FAN DETECTION
   ========================================================================== */

export function _raFindAttackFans(side) {
  const routes = side.routes;
  const n = routes.length;

  const attacks = routes.map(r => {
    if (!r.ownPts || r.ownPts.length < 2) return null;
    const a = r.ownPts[r.ownPts.length - 2];
    const b = r.ownPts[r.ownPts.length - 1];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    if (len < 2) return null;
    return {
      x0: a[0], y0: a[1], x1: b[0], y1: b[1],
      angle: Math.atan2(dy, dx),
      len,
      minX: Math.min(a[0], b[0]), maxX: Math.max(a[0], b[0]),
      minY: Math.min(a[1], b[1]), maxY: Math.max(a[1], b[1]),
    };
  });

  const adj = new Map();
  for (let i = 0; i < n; i++) adj.set(i, new Set());

  for (let i = 0; i < n; i++) {
    if (!attacks[i]) continue;
    for (let j = i + 1; j < n; j++) {
      if (!attacks[j]) continue;

      const aR = routes[i], bR = routes[j];
      if (aR.clusterAttack && bR.clusterAttack &&
          aR.clusterId >= 0 && aR.clusterId === bR.clusterId) continue;

      const A = attacks[i], B = attacks[j];
      if (_raAngleDiff(A.angle, B.angle) > ROUTE_ESCAPE_ANGLE_TOL) continue;

      if (A.maxX + ROUTE_ESCAPE_PROX < B.minX) continue;
      if (B.maxX + ROUTE_ESCAPE_PROX < A.minX) continue;
      if (A.maxY + ROUTE_ESCAPE_PROX < B.minY) continue;
      if (B.maxY + ROUTE_ESCAPE_PROX < A.minY) continue;

      const d = _raSegSegDist(A.x0, A.y0, A.x1, A.y1,
                              B.x0, B.y0, B.x1, B.y1);
      if (d >= ROUTE_ESCAPE_PROX) continue;

      if (_raProjOverlap(A, B) < ROUTE_ESCAPE_MIN_OVERLAP) continue;

      adj.get(i).add(j);
      adj.get(j).add(i);
    }
  }

  const visited = new Set();
  const fans = [];
  for (let i = 0; i < n; i++) {
    if (visited.has(i)) continue;
    if (adj.get(i).size === 0) { visited.add(i); continue; }
    const comp = [];
    const queue = [i];
    visited.add(i);
    while (queue.length) {
      const cur = queue.shift();
      comp.push(cur);
      for (const nbr of adj.get(cur)) {
        if (!visited.has(nbr)) { visited.add(nbr); queue.push(nbr); }
      }
    }
    if (comp.length >= 2) fans.push(comp);
  }
  return fans;
}
"""
