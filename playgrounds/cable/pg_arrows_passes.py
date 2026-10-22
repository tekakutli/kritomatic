"""
pg_arrows_passes.py — the state-mutating post-solve passes.

ES module.  Six functions, each making one specific structural
improvement to a side that the iterated solver has already run on:

    _raForceTightClusterAttack    flag a knot of tight-plan targets
                                  for cluster attack directly, without
                                  waiting for score pressure
    _raUnifyClusterAnchors        give every member of a cluster-
                                  attack group the same bundleY and
                                  descentX, so their attacks share an
                                  anchor
    _raDedupeClusterAttacks       drop the shorter of two cluster-
                                  attack routes whose attack angles
                                  are within ROUTE_PARALLEL_TOL
    _raEnforceLongRouteBundling   merge a solo long-attack route into
                                  a nearby parallel group
    _raForceBundleParallelAttacks merge same-column groups whose attack
                                  angles form a parallel block
    _raSplitLongAttackBundles     pull routes with attack length above
                                  LONG_ATTACK_SPLIT_THRESHOLD into a
                                  fresh satellite bundle

Note that `_raReduceCrossings`, `_raCrossingReducerCandidates`, and
`_raCountPathCrossings` — which the concatenation model defined in the
passes file — are now defined in `pg_arrows_solver.py`.  They call
into solver's own clone/restore/refinement helpers and into scoring
and shapes; keeping them in solver breaks a cycle that would otherwise
run passes → solver → passes.

Imports from `pg_arrows_tuning.js` (constants), `pg_arrows_geometry.js`
(`_raAngleDiff`), and `pg_arrows_state.js` (`_raRebuildRouteRefs`,
`_raBundleFanOut`).  No other arrow module is imported, so this file
sits directly above state in the dependency graph.
"""

PASSES_JS = r"""
import {
  ROUTE_CLUSTER_RADIUS,
  ROUTE_PARALLEL_TOL,
  SOLVER_GROUP_MAX_ROUTES,
  LONG_ATTACK_THRESHOLD,
  LONG_ATTACK_SPLIT_THRESHOLD,
  SAME_COL_TOL,
  ROUTE_MIN_SEP,
} from "./pg_arrows_tuning.js";
import { _raAngleDiff } from "./pg_arrows_geometry.js";
import {
  _raRebuildRouteRefs, _raBundleFanOut,
} from "./pg_arrows_state.js";

/* ==========================================================================
   PASS 14 — FORCE CLUSTER-ATTACK ON TIGHT KNOTS
   ==========================================================================
   The regular cluster solver only flags a cluster for cluster attack
   after many iterations of score pressure.  A cluster whose members'
   plan targets all lie within ROUTE_CLUSTER_RADIUS / 2 of one another
   is a tight knot: their tips will pile up however the attacks are
   laid out, and the correct handling is cluster attack with a shared
   anchor.

   This pass detects that shape directly and flags it, without waiting
   for the score-based iteration to converge.  It runs after Pass 11
   and after Pass 13, so the flags survive to shape assignment. */

export function _raForceTightClusterAttack(side) {
  const routes = side.routes;
  let flagged = 0;

  for (const cluster of side.clusters || []) {
    const unflagged = [];
    for (const ri of cluster) {
      const r = routes[ri];
      if (r.clusterAttack) continue;
      unflagged.push(ri);
    }
    if (unflagged.length < 3) continue;

    const R2 = (ROUTE_CLUSTER_RADIUS * 0.5) *
               (ROUTE_CLUSTER_RADIUS * 0.5);
    let knot = false;
    for (let i = 0; i < unflagged.length && !knot; i++) {
      const a = routes[unflagged[i]];
      let nbr = 0;
      for (let j = 0; j < unflagged.length; j++) {
        if (i === j) continue;
        const b = routes[unflagged[j]];
        const dx = a.planX - b.planX;
        const dy = a.planY - b.planY;
        if (dx * dx + dy * dy < R2) nbr++;
      }
      if (nbr >= 2) knot = true;
    }
    if (!knot) continue;

    for (const ri of unflagged) routes[ri].clusterAttack = true;
    flagged += unflagged.length;
  }

  window.__tightClusterFlags = flagged;
  return flagged;
}

/* ==========================================================================
   PASS 8 — UNIFY CLUSTER ANCHORS
   ==========================================================================
   Give every cluster-attack member the same bundleY (the highest one
   in the cluster) and the same descentX (the midpoint of their plan-x
   span).  Without this their attacks fan from different anchors and
   the whole point of cluster attack — a shared anchor so the tips
   diverge from one place — is lost. */

export function _raUnifyClusterAnchors(side) {
  const routes = side.routes;
  for (const cluster of side.clusters || []) {
    const members = [];
    for (const ri of cluster) {
      const r = routes[ri];
      if (r.clusterAttack) members.push(r);
    }
    if (members.length === 0) continue;

    let sharedBundleY = Infinity;
    let minPlanX = Infinity;
    let maxPlanX = -Infinity;
    for (const r of members) {
      if (r.bundleY < sharedBundleY) sharedBundleY = r.bundleY;
      if (r.planX < minPlanX) minPlanX = r.planX;
      if (r.planX > maxPlanX) maxPlanX = r.planX;
    }
    const sharedDescentX = (minPlanX + maxPlanX) * 0.5;

    for (const r of members) {
      r.bundleY  = sharedBundleY;
      r.descentX = sharedDescentX;
    }
  }
}

/* ==========================================================================
   PASS 9 — DEDUPE CLUSTER ATTACKS
   ==========================================================================
   Drop the shorter of two cluster-attack routes whose attack angles
   are within ROUTE_PARALLEL_TOL.  Without this a knot of tightly-
   packed targets draws four or five near-identical attacks and the
   whole cluster reads as one thick stroke. */

export function _raDedupeClusterAttacks(side) {
  const routes = side.routes;
  let droppedCount = 0;

  for (const cluster of side.clusters || []) {
    const entries = [];
    for (const ri of cluster) {
      const r = routes[ri];
      if (!r.clusterAttack) continue;
      const dx = r.planX - r.descentX;
      const dy = r.planY - r.bundleY;
      const len = Math.hypot(dx, dy);
      if (len < 1) continue;
      entries.push({ ri, angle: Math.atan2(dy, dx), len });
    }
    if (entries.length < 2) continue;

    entries.sort((a, b) => a.angle - b.angle);

    let prevKept = null;
    for (const e of entries) {
      if (prevKept === null) { prevKept = e; continue; }

      let diff = Math.abs(e.angle - prevKept.angle);
      if (diff > Math.PI) diff = 2 * Math.PI - diff;

      if (diff < ROUTE_PARALLEL_TOL) {
        if (e.len > prevKept.len) {
          routes[prevKept.ri].clusterAttack = false;
          droppedCount++;
          prevKept = e;
        } else {
          routes[e.ri].clusterAttack = false;
          droppedCount++;
        }
      } else {
        prevKept = e;
      }
    }
  }

  window.__clusterDedupeDrops = droppedCount;
}

/* ==========================================================================
   PASS 10 — ENFORCE LONG-ROUTE BUNDLING
   ==========================================================================
   A long-attack solo route whose descent column coincides (within
   SAME_COL_TOL) with a nearby group whose attack angle is within
   ROUTE_PARALLEL_TOL gets merged into that group.  This is what
   prevents long attacks from being isolated detours when a whole
   cohort of neighbours is already bundled in the same column. */

export function _raEnforceLongRouteBundling(side) {
  const routes = side.routes;
  let merged = 0;

  for (const b of side.bundles) {
    for (let gi = 0; gi < b.groups.length; gi++) {
      const g0 = b.groups[gi];
      if (g0.routeIndices.length !== 1) continue;
      const r = routes[g0.routeIndices[0]];
      if (r.clusterAttack) continue;

      const attLen = Math.hypot(r.planX - r.descentX,
                                r.planY - r.bundleY);
      if (attLen < LONG_ATTACK_THRESHOLD) continue;
      const ang0 = Math.atan2(r.planY - r.bundleY,
                              r.planX - r.descentX);

      let bestGi = -1;
      for (let gj = 0; gj < b.groups.length; gj++) {
        if (gj === gi) continue;
        const g = b.groups[gj];
        if (g.routeIndices.length < 2) continue;
        if (g.forceUnbundled) continue;
        if (g.routeIndices.length >= SOLVER_GROUP_MAX_ROUTES) continue;
        if (Math.abs(g.descentX - g0.descentX) > SAME_COL_TOL) continue;

        for (const ri2 of g.routeIndices) {
          const r2 = routes[ri2];
          const ang2 = Math.atan2(r2.planY - r2.bundleY,
                                   r2.planX - r2.descentX);
          if (_raAngleDiff(ang0, ang2) < ROUTE_PARALLEL_TOL) {
            bestGi = gj;
            break;
          }
        }
        if (bestGi >= 0) break;
      }

      if (bestGi < 0) continue;

      const target = b.groups[bestGi];
      const ri = g0.routeIndices[0];
      target.routeIndices.push(ri);
      target.routeIndices.sort((i, j) => routes[i].planX - routes[j].planX);
      g0.routeIndices = [];
      merged++;
    }

    b.groups = b.groups.filter(g => g.routeIndices.length > 0);
  }

  if (merged > 0) {
    _raRebuildRouteRefs(side);
    _raBundleFanOut(side);
  }
  window.__longSoloBundled = merged;
}

/* ==========================================================================
   PASS 11 — FORCE-BUNDLE PARALLEL ATTACKS
   ==========================================================================
   Within a bundle, routes on the same descent column whose attack
   angles form a parallel block get merged into a single attack-
   bundled group.  Without this a fresh batch of parallel attacks
   sits in separate groups and their tips pile up. */

export function _raForceBundleParallelAttacks(side) {
  const routes = side.routes;
  let merges = 0;

  for (const b of side.bundles) {
    const colMap = new Map();
    for (let gi = 0; gi < b.groups.length; gi++) {
      const g = b.groups[gi];
      for (const ri of g.routeIndices) {
        const r = routes[ri];
        if (r.clusterAttack) continue;
        const key = Math.round(r.descentX / SAME_COL_TOL);
        if (!colMap.has(key)) colMap.set(key, []);
        colMap.get(key).push({ ri, gi });
      }
    }

    for (const entries of colMap.values()) {
      if (entries.length < 2) continue;

      const infos = [];
      for (const { ri, gi } of entries) {
        const r = routes[ri];
        const H = r.planX - r.descentX;
        const V = r.planY - r.bundleY;
        const len = Math.hypot(H, V);
        infos.push({ ri, gi, len, ang: Math.atan2(V, H) });
      }

      infos.sort((a, c) => a.ang - c.ang);

      const blocks = [];
      let cur = [infos[0]];
      for (let i = 1; i < infos.length; i++) {
        if (_raAngleDiff(infos[i - 1].ang, infos[i].ang)
            < ROUTE_PARALLEL_TOL) {
          cur.push(infos[i]);
        } else {
          blocks.push(cur);
          cur = [infos[i]];
        }
      }
      blocks.push(cur);

      for (const block of blocks) {
        if (block.length < 2) continue;
        if (block.length > SOLVER_GROUP_MAX_ROUTES + 1) continue;

        const targetGi = block[0].gi;
        const targetGroup = b.groups[targetGi];
        if (targetGroup.forceUnbundled) continue;

        let sumDx = 0, sumDy = 0;
        for (const e of block) {
          sumDx += Math.cos(e.ang);
          sumDy += Math.sin(e.ang);
        }
        const nl = Math.hypot(sumDx, sumDy);
        if (nl < 1e-6) continue;

        for (const e of block) {
          if (e.gi === targetGi) continue;
          const src = b.groups[e.gi];
          const idx = src.routeIndices.indexOf(e.ri);
          if (idx >= 0) src.routeIndices.splice(idx, 1);
          targetGroup.routeIndices.push(e.ri);
        }
        targetGroup.routeIndices.sort((i, j) =>
          routes[i].planX - routes[j].planX);
        targetGroup.attackBundled = true;
        targetGroup.trunkDir = [sumDx / nl, sumDy / nl];
        targetGroup.forceUnbundled = false;
        merges++;
      }
    }

    b.groups = b.groups.filter(g => g.routeIndices.length > 0);
  }

  if (merges > 0) {
    _raRebuildRouteRefs(side);
    _raBundleFanOut(side);
  }
  window.__longSoloBundled = merges;
}

/* ==========================================================================
   PASS 13 — SPLIT LONG-ATTACK BUNDLES
   ==========================================================================
   Routes whose final attack segment is above
   LONG_ATTACK_SPLIT_THRESHOLD get pulled into a fresh satellite
   bundle.  The satellite reserves its own bundleY that tracks the
   deep-plan band, so a cluster of long attacks no longer crowds the
   regular bundle's mid-strip neighbourhood. */

export function _raSplitLongAttackBundles(side) {
  const routes = side.routes;
  const longArr = [];

  for (let i = 0; i < routes.length; i++) {
    const r = routes[i];
    if (!r.ownPts || r.ownPts.length < 2) continue;
    if (r.clusterAttack) continue;

    const bidx = r.bundleIdx;
    if (bidx >= 0 && bidx < side.bundles.length
        && side.bundles[bidx].isSatellite) continue;

    const a = r.ownPts[r.ownPts.length - 2];
    const b = r.ownPts[r.ownPts.length - 1];
    const attLen = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (attLen >= LONG_ATTACK_SPLIT_THRESHOLD) longArr.push(i);
  }

  if (longArr.length === 0) return 0;

  let minPY = Infinity;
  for (const ri of longArr) {
    if (routes[ri].planY < minPY) minPY = routes[ri].planY;
  }
  if (minPY === Infinity) return 0;

  const longSet = new Set(longArr);

  for (const b of side.bundles) {
    b.routeIndices = b.routeIndices.filter(ri => !longSet.has(ri));
    for (const g of b.groups) {
      g.routeIndices = g.routeIndices.filter(ri => !longSet.has(ri));
    }
    b.groups = b.groups.filter(g => g.routeIndices.length > 0);
  }
  side.bundles = side.bundles.filter(b => b.routeIndices.length > 0);

  const sorted = longArr.slice().sort((i, j) => {
    const hi = routes[i].upperHalf ? 0 : 1;
    const hj = routes[j].upperHalf ? 0 : 1;
    if (hi !== hj) return hi - hj;
    const dXi = routes[i].descentX;
    const dXj = routes[j].descentX;
    if (dXi !== dXj) return dXi - dXj;
    return routes[i].planX - routes[j].planX;
  });

  const groups = [];
  let cur = [sorted[0]];
  let curX = routes[sorted[0]].descentX;
  let curH = routes[sorted[0]].upperHalf ? 0 : 1;
  for (let k = 1; k < sorted.length; k++) {
    const ri = sorted[k];
    const dX = routes[ri].descentX;
    const h  = routes[ri].upperHalf ? 0 : 1;
    if (h === curH && Math.abs(dX - curX) < ROUTE_MIN_SEP) {
      cur.push(ri);
    } else {
      groups.push(cur);
      cur = [ri];
      curX = dX;
      curH = h;
    }
  }
  groups.push(cur);

  const upperHalf = routes[sorted[0]].upperHalf;

  side.bundles.push({
    routeIndices: sorted.slice(),
    groups: groups.map(g => ({
      routeIndices: g.slice(),
      descentX: 0,
      attackBundled: false,
      trunkDir: null,
      forceUnbundled: false,
    })),
    isSatellite: true,
    satelliteMinPlanY: minPY,
    upperHalf: upperHalf,
  });

  _raRebuildRouteRefs(side);
  _raBundleFanOut(side);
  return longArr.length;
}
"""
