"""
pg_arrows_reroute.py — the high-conflict reroute pass and the
post-reroute rebundle.

_raRerouteHighCrossers finds the ROUTE_REROUTE_MAX_PER_SWEEP routes
with the highest foreign-conflict score, tries a two-stage grid search
(coarse then refined) over a middle-point waypoint for each, and
commits any change that reduces the total score by more than
ROUTE_REROUTE_ACCEPT.  A rerouted route is tagged "R" so the shape
builder skips it and the rebundle pass can isolate it.

_raRebundleAfterReroute walks the bundles after the reroute sweep and
splits every "R"-tagged route into its own singleton group with
forceUnbundled set.  This keeps the shape builder from re-immersing a
freshly-detoured route in the bundle it was trying to escape.

The reroute pass is skipped entirely during an escape-fan re-solve,
because the frozen-polylines mechanism already constrains the
evacuees' shapes against the survivors' tails.
"""


REROUTE_JS = r"""
/* ==========================================================================
   SECTION 5b — HIGH-CONFLICT REROUTE (PASS 6) AND REBUNDLE (PASS 6b)
   ========================================================================== */

function _raCountConflicts(r, allRoutes) {
  let nCross = 0, nParallel = 0, total = 0;
  let totalCross = 0, totalParallel = 0;
  for (const q of allRoutes) {
    if (q === r) continue;
    if (!q.ownPts) continue;
    const sameGroup = (q.bundleIdx === r.bundleIdx &&
                       q.groupIdx  === r.groupIdx);
    const sameCluster = (r.clusterAttack && q.clusterAttack &&
                         r.clusterId >= 0 && r.clusterId === q.clusterId);
    if (sameGroup || sameCluster) continue;
    const s = _raPathPairScore(r.ownPts, q.ownPts);
    const contrib = s.p + s.c;
    if (contrib > 0) {
      if (s.c > 0) nCross++;
      if (s.p > 0) nParallel++;
      totalCross += s.c;
      totalParallel += s.p;
      total += contrib;
    }
  }
  const n = nCross + nParallel;
  return { nCross, nParallel, n, total, totalCross, totalParallel };
}

function _raDetourPenalty(r, pts) {
  if (!pts || pts.length < 2) return 0;

  const directLen = Math.hypot(r.planX - r.descentX,
                               r.planY - r.bundleY);
  let totalLen = 0;
  for (let i = 0; i < pts.length - 1; i++) {
    totalLen += Math.hypot(pts[i + 1][0] - pts[i][0],
                           pts[i + 1][1] - pts[i][1]);
  }
  const extraLen = Math.max(0, totalLen - directLen);

  let bendSum = 0;
  for (let i = 1; i < pts.length - 1; i++) {
    const ax = pts[i][0] - pts[i - 1][0];
    const ay = pts[i][1] - pts[i - 1][1];
    const bx = pts[i + 1][0] - pts[i][0];
    const by = pts[i + 1][1] - pts[i][1];
    const aLen = Math.hypot(ax, ay);
    const bLen = Math.hypot(bx, by);
    if (aLen < 0.5 || bLen < 0.5) continue;
    const cosA = Math.max(-1, Math.min(1,
      (ax * bx + ay * by) / (aLen * bLen)));
    bendSum += (1 - cosA);
  }

  return extraLen * REROUTE_LENGTH_W + bendSum * REROUTE_BEND_W;
}

function _raBuildDetourCandidates(r) {
  const startX = r.descentX, startY = r.bundleY;
  const endX   = r.planX,    endY   = r.planY;

  const vx = endX - startX, vy = endY - startY;
  const vLen = Math.hypot(vx, vy);
  if (vLen < 1) return [];

  const ux = vx / vLen, uy = vy / vLen;
  const nx = -uy, ny = ux;

  const perpExtent = Math.min(ROUTE_REROUTE_SPAN, vLen);
  const N = ROUTE_REROUTE_GRID_STEPS;
  const out = [];

  for (let i = 1; i < N; i++) {
    const t = i / N;
    const bx = startX + ux * vLen * t;
    const by = startY + uy * vLen * t;
    for (let j = 0; j < N; j++) {
      const s = ((j + 0.5) / N) * 2 - 1;
      const wx = bx + nx * perpExtent * s;
      const wy = by + ny * perpExtent * s;
      const pts = [ [startX, startY], [wx, wy], [endX, endY] ];
      out.push({ tag: "R", pts, wx, wy });
    }
  }
  return out;
}

function _raBuildRefineCandidates(r, centerW) {
  const startX = r.descentX, startY = r.bundleY;
  const endX   = r.planX,    endY   = r.planY;

  const vx = endX - startX, vy = endY - startY;
  const vLen = Math.hypot(vx, vy);
  if (vLen < 1) return [];

  const ux = vx / vLen, uy = vy / vLen;
  const nx = -uy, ny = ux;
  const perpExtent = Math.min(ROUTE_REROUTE_SPAN, vLen);

  const dxW = centerW[0] - startX;
  const dyW = centerW[1] - startY;
  const t0 = Math.max(0, Math.min(1, (dxW * ux + dyW * uy) / vLen));
  const s0 = (dxW * nx + dyW * ny) / (perpExtent || 1);

  const N = ROUTE_REROUTE_REFINE_STEPS;
  const stepT = 1 / ROUTE_REROUTE_GRID_STEPS;
  const stepS = 2 / ROUTE_REROUTE_GRID_STEPS;

  const out = [];
  for (let i = 0; i < N; i++) {
    const dt = (i / (N - 1) - 0.5) * stepT * 2;
    const t  = Math.max(0.05, Math.min(0.95, t0 + dt));
    const bx = startX + ux * vLen * t;
    const by = startY + uy * vLen * t;
    for (let j = 0; j < N; j++) {
      const ds = (j / (N - 1) - 0.5) * stepS * 2;
      const s  = Math.max(-1, Math.min(1, s0 + ds));
      const wx = bx + nx * perpExtent * s;
      const wy = by + ny * perpExtent * s;
      const pts = [ [startX, startY], [wx, wy], [endX, endY] ];
      out.push({ tag: "R", pts, wx, wy });
    }
  }
  return out;
}

function _raTryReroute(side, r) {
  const savedPts  = r.ownPts;
  const savedTag  = r.tag;
  const savedDiag = r.isDiag;

  const curScore = _raScoreSide(side).total;
  const curPen   = _raDetourPenalty(r, r.ownPts);
  const curTotal = curScore + curPen;

  let bestTotal = curTotal;
  let bestPts   = null;

  const coarse = _raBuildDetourCandidates(r);
  for (const cand of coarse) {
    r.ownPts = cand.pts;
    const s = _raScoreSide(side).total + _raDetourPenalty(r, cand.pts);
    if (s < bestTotal - 0.5) {
      bestTotal = s;
      bestPts   = cand.pts;
    }
  }

  if (bestPts) {
    const center = bestPts[1];
    const fine = _raBuildRefineCandidates(r, center);
    for (const cand of fine) {
      r.ownPts = cand.pts;
      const s = _raScoreSide(side).total + _raDetourPenalty(r, cand.pts);
      if (s < bestTotal - 0.5) {
        bestTotal = s;
        bestPts   = cand.pts;
      }
    }
  }

  if (bestPts && curTotal - bestTotal > ROUTE_REROUTE_ACCEPT) {
    r.ownPts = bestPts;
    r.tag    = "R";
    r.isDiag = false;
    return true;
  }

  r.ownPts = savedPts;
  r.tag    = savedTag;
  r.isDiag = savedDiag;
  return false;
}

function _raRerouteHighCrossers(side) {
  const routes = side.routes;
  const n = routes.length;
  if (n < 3) return false;

  const preScore  = _raScoreSide(side).total;
  const preOwnPts = routes.map(r => r.ownPts ? r.ownPts.slice() : null);
  const preTags   = routes.map(r => r.tag);
  const preIsDiag = routes.map(r => !!r.isDiag);

  const allCands = [];
  const breakdown = [];
  for (let i = 0; i < n; i++) {
    const r = routes[i];
    if (!r.ownPts) continue;
    if (r.clusterAttack) continue;
    const c = _raCountConflicts(r, routes);
    allCands.push({ ri: i, total: c.total, n: c.n,
                    nCross: c.nCross, nParallel: c.nParallel });
    breakdown.push({ ri: i,
                     nCross: c.nCross, nParallel: c.nParallel,
                     totalCross: c.totalCross,
                     totalParallel: c.totalParallel,
                     total: c.total });
  }
  allCands.sort((a, b) => b.total - a.total);

  const cands = allCands.slice(0, ROUTE_REROUTE_MAX_PER_SWEEP);

  side.rerouteInfo.candidates += cands.length;
  if (side.rerouteInfo.preScore == null) {
    side.rerouteInfo.preScore = preScore;
  }
  side.rerouteInfo.conflictTable = breakdown;

  let committed = 0;
  for (const entry of cands) {
    const r = routes[entry.ri];
    if (_raTryReroute(side, r)) committed++;
  }

  side.rerouteInfo.committed += committed;
  if (committed === 0) {
    if (side.rerouteInfo.postScore == null) {
      side.rerouteInfo.postScore = preScore;
    }
    return false;
  }

  const postScore = _raScoreSide(side).total;
  side.rerouteInfo.postScore = postScore;

  if (postScore >= preScore - 0.5) {
    for (let i = 0; i < n; i++) {
      if (preOwnPts[i]) routes[i].ownPts = preOwnPts[i];
      routes[i].tag    = preTags[i];
      routes[i].isDiag = preIsDiag[i];
    }
    side.rerouteInfo.committed -= committed;
    return false;
  }
  return true;
}

function _raRebundleAfterReroute(side) {
  let changed = false;
  const newBundles = [];

  for (const b of side.bundles) {
    const newGroups = [];

    for (const g of b.groups) {
      const rerouted = [];
      const kept     = [];

      for (const ri of g.routeIndices) {
        const r = side.routes[ri];
        if (r.tag === "R") rerouted.push(ri);
        else               kept.push(ri);
      }

      if (kept.length > 0) {
        newGroups.push({
          routeIndices: kept,
          descentX: g.descentX,
          attackBundled: !!g.attackBundled,
          trunkDir: g.trunkDir ? g.trunkDir.slice() : null,
          forceUnbundled: !!g.forceUnbundled,
        });
        if (rerouted.length > 0) changed = true;
      }

      for (const ri of rerouted) {
        newGroups.push({
          routeIndices: [ri],
          descentX: g.descentX,
          attackBundled: false,
          trunkDir: null,
          forceUnbundled: true,
        });
        changed = true;
      }
    }

    if (newGroups.length === 0) continue;

    const allRi = [];
    for (const gg of newGroups) {
      for (const ri of gg.routeIndices) allRi.push(ri);
    }
    const newBundle = { routeIndices: allRi, groups: newGroups,
                        upperHalf: !!b.upperHalf };
    if (b.isSatellite) {
      newBundle.isSatellite = true;
      newBundle.satelliteMinPlanY = b.satelliteMinPlanY;
    }
    newBundles.push(newBundle);
  }

  if (changed) {
    side.bundles = newBundles;
    _raRebuildRouteRefs(side);
    _raBundleFanOut(side);
  }
  return changed;
}
"""
