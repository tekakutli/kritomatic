"""
pg_arrows_solver.py — the top-level solve, the side clone/restore, the
refinement chooser, the escape-fan eviction, and every find-the-next-
target helper.

_raSolve runs the iterated refinement loop, then hands off to the post-
solve passes in pg_arrows_passes, then to the crossing reducer and the
reroute pass, then — unless the solve is itself an escape-fan re-solve
— to the escape-fan eviction step.

The escape-fan step is the one recursive path in the solver: it
identifies fans of parallel-close attacks, keeps the shortest member
of each, removes the rest, and calls _raSolve on the evacuees with the
survivors' tails frozen via _raFrozenPolylines.  The
_raEscapeInProgress guard keeps that recursion from running the
reroute and escape passes again inside the sub-solve.

The clone/restore pair is how the solver keeps a best-so-far snapshot
across iterations: it deep-copies the entire side state — bundles,
groups, route flags, and route shapes — so a failed refinement can be
rolled back exactly.  Only the fields the solver mutates are copied;
everything else (planX, stripX, ownPts, etc.) is left as a reference.
"""


SOLVER_JS = r"""
/* ==========================================================================
   SECTION 7 — SOLVER
   ========================================================================== */

function _raCloneSide(side) {
  return {
    bundles: side.bundles.map(b => {
      const bndl = {
        routeIndices: b.routeIndices.slice(),
        upperHalf: !!b.upperHalf,
        groups: b.groups.map(g => ({
          routeIndices: g.routeIndices.slice(),
          descentX: g.descentX,
          forceUnbundled: !!g.forceUnbundled,
          attackBundled:  !!g.attackBundled,
          trunkDir: g.trunkDir ? g.trunkDir.slice() : null,
        })),
      };
      if (b.isSatellite) {
        bndl.isSatellite = true;
        bndl.satelliteMinPlanY = b.satelliteMinPlanY;
      }
      return bndl;
    }),
    bundleSpacing: side.bundleSpacing,
    groupSpacing:  side.groupSpacing,
    peelStagger:   side.peelStagger,
    comX:          side.comX,
    comY:          side.comY,
    routeFlags:    side.routes.map(r => ({
      clusterAttack: !!r.clusterAttack,
    })),
    routeShapes: side.routes.map(r => ({
      ownPts:   r.ownPts ? r.ownPts.map(p => [p[0], p[1]]) : null,
      tag:      r.tag,
      isDiag:   !!r.isDiag,
      descentX: r.descentX,
      bundleY:  r.bundleY,
      baseBreak: (typeof r.baseBreak === "number") ? r.baseBreak : null,
      bundleFanRank:   (typeof r.bundleFanRank === "number")
                       ? r.bundleFanRank : 0,
      bundleJogOffset: (typeof r.bundleJogOffset === "number")
                       ? r.bundleJogOffset : 0,
    })),
  };
}

function _raRestoreSide(side, snap) {
  side.bundles = snap.bundles.map(b => {
    const bndl = {
      routeIndices: b.routeIndices.slice(),
      upperHalf: !!b.upperHalf,
      groups: b.groups.map(g => ({
        routeIndices: g.routeIndices.slice(),
        descentX: g.descentX,
        attackBundled: !!g.attackBundled,
        trunkDir: g.trunkDir ? g.trunkDir.slice() : null,
        forceUnbundled: !!g.forceUnbundled,
      })),
    };
    if (b.isSatellite) {
      bndl.isSatellite = true;
      bndl.satelliteMinPlanY = b.satelliteMinPlanY;
    }
    return bndl;
  });
  side.bundleSpacing = snap.bundleSpacing;
  side.groupSpacing  = snap.groupSpacing;
  side.peelStagger   = snap.peelStagger;
  if (typeof snap.comX === "number") side.comX = snap.comX;
  if (typeof snap.comY === "number") side.comY = snap.comY;

  if (snap.routeFlags) {
    for (let i = 0; i < side.routes.length; i++) {
      const f = snap.routeFlags[i];
      if (f) side.routes[i].clusterAttack = f.clusterAttack;
    }
  }

  if (snap.routeShapes) {
    for (let i = 0; i < side.routes.length; i++) {
      const s = snap.routeShapes[i];
      if (!s) continue;
      const r = side.routes[i];
      r.ownPts   = s.ownPts ? s.ownPts.map(p => [p[0], p[1]]) : null;
      r.tag      = s.tag;
      r.isDiag   = s.isDiag;
      r.descentX = s.descentX;
      r.bundleY  = s.bundleY;
      if (s.baseBreak !== null && s.baseBreak !== undefined) {
        r.baseBreak = s.baseBreak;
      }
      if (typeof s.bundleFanRank === "number") {
        r.bundleFanRank = s.bundleFanRank;
      }
      if (typeof s.bundleJogOffset === "number") {
        r.bundleJogOffset = s.bundleJogOffset;
      }
    }
  }

  _raRebuildRouteRefs(side);
}

function _raSolve(side) {
  if (!side.routes.length) return;

  let best = null;

  for (let iter = 0; iter < SOLVER_MAX_ITERATIONS; iter++) {
    _raLayoutSide(side);
    _raAssignShapes(side);
    const score = _raScoreSide(side);

    if (!best || score.total < best.score.total - 0.5) {
      best = { score, snapshot: _raCloneSide(side) };
    }

    if (score.total < SOLVER_ACCEPTABLE_SCORE &&
        score.attackParallel < SOLVER_ATTACK_PARALLEL_BUDGET &&
        score.clusterPenalty  < SOLVER_CLUSTER_BUDGET &&
        score.tipPenalty      < SOLVER_TIP_BUDGET) break;

    const action = _raChooseRefinement(side, score, iter);
    if (!action) break;
    _raApplyRefinement(side, action);
  }

  if (best) {
    _raRestoreSide(side, best.snapshot);
  }

  /* ---- Pass 10: merge long solo routes into nearby groups. ---- */
  _raEnforceLongRouteBundling(side);

  /* ---- Pass 11: force-bundle parallel attacks. ---- */
  _raForceBundleParallelAttacks(side);
  _raAssignShapes(side);

  /* ---- Pass 14 (pre-split): force cluster-attack on tight knots.
     Runs here so the flags are visible to the shape builder before
     Pass 13 repositions the long-attack satellites.  If it fires,
     re-run layout and shape assignment so the cluster-attack shapes
     are actually built. ---- */
  window.__tightClusterFlags = 0;
  if (_raForceTightClusterAttack(side) > 0) {
    _raLayoutSide(side);
    _raForceBundleParallelAttacks(side);
    _raAssignShapes(side);
  }

  /* ---- Pass 13: satellite bundle for long attacks. ----
     Now runs on every route including R-tagged ones, because the
     R-skip was removed from _raSplitLongAttackBundles (Fix A).  Long
     rerouted detours finally get their satellite. ---- */
  window.__longAttackSplits = 0;
  const splitCount = _raSplitLongAttackBundles(side);
  if (splitCount > 0) {
    window.__longAttackSplits = splitCount;
    _raLayoutSide(side);
    _raForceBundleParallelAttacks(side);
    _raAssignShapes(side);
  }

  /* ---- Pass 14 (post-split): re-run tight-knot detection.
     Pass 13 may have moved routes into new bundles; a knot that was
     hidden inside a mixed bundle before the split may now be
     exposed.  Re-flag if so. ---- */
  if (_raForceTightClusterAttack(side) > 0) {
    _raLayoutSide(side);
    _raForceBundleParallelAttacks(side);
    _raAssignShapes(side);
  }

  /* ---- Pass 12: iterated crossing reduction. ---- */
  _raReduceCrossings(side);

  if (!_raEscapeInProgress) {
    for (let p = 0; p < 2; p++) {
      if (!_raRerouteHighCrossers(side)) break;
      _raRebundleAfterReroute(side);
    }
    _raRebundleAfterReroute(side);
  }

  if (!_raEscapeInProgress) {
    side.escapeInfo.fans      = 0;
    side.escapeInfo.evacuees  = 0;
    side.escapeInfo.applied   = false;
    side.escapeInfo.preScore  = null;
    side.escapeInfo.postScore = null;

    for (let p = 0; p < ROUTE_ESCAPE_MAX_PASSES; p++) {
      if (!_raEscapeFans(side)) break;
    }
  }
}

function _raEscapeFans(side) {
  if (_raEscapeInProgress) return false;

  const fans = _raFindAttackFans(side);
  side.escapeInfo.fans += fans.length;
  if (!fans.length) {
    return false;
  }

  const fanMembers = new Set();
  const survivors = new Set();
  for (const fan of fans) {
    for (const ri of fan) fanMembers.add(ri);
    let best = -1, bestLen = Infinity;
    for (const ri of fan) {
      const r = side.routes[ri];
      if (!r.ownPts || r.ownPts.length < 2) continue;
      const a = r.ownPts[r.ownPts.length - 2];
      const b = r.ownPts[r.ownPts.length - 1];
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (len < bestLen) { bestLen = len; best = ri; }
    }
    if (best >= 0) survivors.add(best);
  }

  const evacuees = new Set();
  for (const ri of fanMembers) {
    if (!survivors.has(ri)) evacuees.add(ri);
  }
  side.escapeInfo.evacuees += evacuees.size;
  if (evacuees.size === 0) {
    return false;
  }

  const preSnapshot = _raCloneSide(side);
  const preFlags = side.routes.map(r => ({
    escaped:  !!r.escaped,
    survivor: !!r.survivor,
  }));
  const preScore = _raScoreSide(side).total;
  if (side.escapeInfo.preScore == null) {
    side.escapeInfo.preScore = preScore;
  }

  const frozen = [];
  for (let i = 0; i < side.routes.length; i++) {
    if (evacuees.has(i)) continue;
    const r = side.routes[i];
    if (!r.ownPts || r.ownPts.length < 2) continue;
    const n = r.ownPts.length;
    const start = Math.max(0, n - 1 - ROUTE_ESCAPE_FROZEN_TAIL);
    frozen.push(r.ownPts.slice(start));
  }

  for (const b of side.bundles) {
    b.routeIndices = b.routeIndices.filter(ri => !evacuees.has(ri));
    for (const g of b.groups) {
      g.routeIndices = g.routeIndices.filter(ri => !evacuees.has(ri));
    }
    b.groups = b.groups.filter(g => g.routeIndices.length > 0);
  }
  side.bundles = side.bundles.filter(b => b.routeIndices.length > 0);

  const evacRoutes = [];
  for (let i = 0; i < side.routes.length; i++) {
    if (evacuees.has(i)) evacRoutes.push(side.routes[i]);
  }

  const subSide = {
    routes: evacRoutes,
    bundles: [],
    bundleSpacing: ROUTE_BUNDLE_SPACING_MIN,
    groupSpacing:  ROUTE_GROUP_SPACING_MIN,
    peelStagger:   ROUTE_PEEL_STAGGER_MIN,
    clusters:      [],
    comX:          side.comX,
    comY:          side.comY,
    escapeInfo:    { fans: 0, evacuees: 0, applied: false,
                     preScore: null, postScore: null },
    rerouteInfo:   { candidates: 0, committed: 0,
                     preScore: null, postScore: null,
                     conflictTable: null },
  };
  _raProposeInitialBundles(subSide);
  _raFindPlanClusters(subSide);

  _raEscapeInProgress = true;
  try {
    _raFrozenPolylines = frozen;
    _raSolve(subSide);
  } finally {
    _raEscapeInProgress = false;
    _raFrozenPolylines = null;
  }

  for (const b of subSide.bundles) {
    const newBundle = {
      routeIndices: b.routeIndices.map(ri =>
        side.routes.indexOf(subSide.routes[ri])),
      upperHalf: !!b.upperHalf,
      groups: b.groups.map(g => ({
        routeIndices: g.routeIndices.map(ri =>
          side.routes.indexOf(subSide.routes[ri])),
        descentX: g.descentX,
        attackBundled: !!g.attackBundled,
        trunkDir: g.trunkDir ? g.trunkDir.slice() : null,
        forceUnbundled: !!g.forceUnbundled,
      })),
    };
    if (b.isSatellite) {
      newBundle.isSatellite = true;
      newBundle.satelliteMinPlanY = b.satelliteMinPlanY;
    }
    side.bundles.push(newBundle);
  }
  _raRebuildRouteRefs(side);
  _raBundleFanOut(side);

  const postScore = _raScoreSide(side).total;
  side.escapeInfo.postScore = postScore;

  if (postScore >= preScore) {
    _raRestoreSide(side, preSnapshot);
    for (let i = 0; i < side.routes.length; i++) {
      side.routes[i].escaped  = preFlags[i].escaped;
      side.routes[i].survivor = preFlags[i].survivor;
    }
    side.escapeInfo.applied = false;
    return false;
  }

  for (const ri of evacuees)  side.routes[ri].escaped  = true;
  for (const ri of survivors) side.routes[ri].survivor = true;
  side.escapeInfo.applied = true;
  return true;
}

function _raChooseRefinement(side, score, iter) {
  if (iter >= SOLVER_CLUSTER_EARLY_ITER &&
      score.clusterPenalty > SOLVER_CLUSTER_BUDGET) {
    const cluster = _raFindClusterToFix(side);
    if (cluster) return { type: "enableClusterAttack", cluster };
  }

  const mergePair = _raFindParallelNearGroupPair(side);
  if (mergePair) {
    return { type: "mergeGroups", pair: mergePair };
  }

  const crowdedGroup = _raFindCrowdedGroup(side);
  if (crowdedGroup) {
    return { type: "splitGroup",
             bundleIdx: crowdedGroup.b, groupIdx: crowdedGroup.g };
  }

  const crowdedBundle = _raFindCrowdedBundle(side);
  if (crowdedBundle >= 0) {
    return { type: "splitBundle", index: crowdedBundle };
  }

  if ((score.parallel > SOLVER_PARALLEL_BUDGET ||
       score.bundlePenalty > SOLVER_BUNDLE_SPACING_BUDGET) &&
      side.bundleSpacing < SOLVER_MAX_BUNDLE_SPACING) {
    return { type: "growBundleSpacing" };
  }

  if ((score.crossing > SOLVER_CROSSING_BUDGET ||
       score.groupPenalty > 0) &&
      side.groupSpacing < SOLVER_MAX_GROUP_SPACING) {
    return { type: "growGroupSpacing" };
  }

  if (score.attackParallel > SOLVER_ATTACK_PARALLEL_BUDGET &&
      side.peelStagger < ROUTE_PEEL_STAGGER_MAX) {
    return { type: "growPeelStagger" };
  }

  if (score.crossing > SOLVER_CROSSING_BUDGET ||
      score.tipPenalty > SOLVER_TIP_BUDGET) {
    const g = _raFindBundledGroupToUnbundle(side);
    if (g) return { type: "unbundleGroup",
                    bundleIdx: g.b, groupIdx: g.g };
  }

  if (score.clusterPenalty > SOLVER_CLUSTER_BUDGET ||
      score.tipPenalty > SOLVER_TIP_BUDGET) {
    const cluster = _raFindClusterToFix(side);
    if (cluster) return { type: "enableClusterAttack", cluster };
  }

  if (score.total > SOLVER_ACCEPTABLE_SCORE) {
    const bIdx = _raFindLargestBundle(side);
    if (bIdx >= 0) return { type: "splitBundle", index: bIdx };
  }
  if (score.total > SOLVER_ACCEPTABLE_SCORE) {
    const bg = _raFindLargestGroup(side);
    if (bg) return { type: "splitGroup", bundleIdx: bg.b, groupIdx: bg.g };
  }
  return null;
}

function _raApplyRefinement(side, action) {
  if (action.type === "growBundleSpacing") {
    side.bundleSpacing += SOLVER_BUNDLE_SPACING_STEP;
  } else if (action.type === "growGroupSpacing") {
    side.groupSpacing += SOLVER_GROUP_SPACING_STEP;
  } else if (action.type === "growPeelStagger") {
    side.peelStagger += ROUTE_PEEL_STAGGER_STEP;
  } else if (action.type === "unbundleGroup") {
    const b = side.bundles[action.bundleIdx];
    if (!b) return;
    const g = b.groups[action.groupIdx];
    if (!g) return;
    g.forceUnbundled = true;
    g.attackBundled = false;
    g.trunkDir = null;
  } else if (action.type === "enableClusterAttack") {
    for (const ri of action.cluster) {
      side.routes[ri].clusterAttack = true;
    }
    _raAssignClusterY(side);
  } else if (action.type === "splitBundle") {
    const b = side.bundles[action.index];
    if (!b || b.routeIndices.length < 2) return;
    const sorted = b.routeIndices.slice()
      .sort((i, j) => side.routes[i].planX - side.routes[j].planX);
    const mid = Math.ceil(sorted.length / 2);
    const left  = sorted.slice(0, mid);
    const right = sorted.slice(mid);
    b.routeIndices = left;
    b.groups = [{ routeIndices: left.slice(), descentX: 0,
                  attackBundled: false, trunkDir: null,
                  forceUnbundled: false }];
    const newBundle = {
      routeIndices: right,
      upperHalf: !!b.upperHalf,
      groups: [{ routeIndices: right.slice(), descentX: 0,
                 attackBundled: false, trunkDir: null,
                 forceUnbundled: false }],
    };
    if (b.isSatellite) {
      newBundle.isSatellite = true;
      newBundle.satelliteMinPlanY = b.satelliteMinPlanY;
    }
    side.bundles.splice(action.index + 1, 0, newBundle);
  } else if (action.type === "splitGroup") {
    const b = side.bundles[action.bundleIdx];
    if (!b) return;
    const g = b.groups[action.groupIdx];
    if (!g || g.routeIndices.length < 2) return;
    const sorted = g.routeIndices.slice()
      .sort((i, j) => side.routes[i].planX - side.routes[j].planX);
    const mid = Math.ceil(sorted.length / 2);
    const left  = sorted.slice(0, mid);
    const right = sorted.slice(mid);
    g.routeIndices = left;
    b.groups.splice(action.groupIdx + 1, 0, {
      routeIndices: right,
      descentX: g.descentX,
      attackBundled: false,
      trunkDir: null,
      forceUnbundled: !!g.forceUnbundled,
    });
  } else if (action.type === "mergeGroups") {
    const b = side.bundles[action.pair.bundleIdx];
    if (!b) return;
    const gi = Math.min(action.pair.gi, action.pair.gj);
    const gj = Math.max(action.pair.gi, action.pair.gj);
    const ga = b.groups[gi], gb = b.groups[gj];
    if (!ga || !gb) return;
    const merged = ga.routeIndices.concat(gb.routeIndices);
    const newX = (ga.descentX + gb.descentX) / 2;
    const newGroup = { routeIndices: merged, descentX: newX,
                       attackBundled: false, trunkDir: null,
                       forceUnbundled: false };
    b.groups.splice(gj, 1);
    b.groups.splice(gi, 1, newGroup);
  }
}

function _raFindLargestBundle(side) {
  let bestIdx = -1, bestSize = 1;
  for (let i = 0; i < side.bundles.length; i++) {
    if (side.bundles[i].routeIndices.length > bestSize) {
      bestSize = side.bundles[i].routeIndices.length;
      bestIdx = i;
    }
  }
  return bestIdx;
}

function _raFindLargestGroup(side) {
  let best = null, bestSize = 1;
  for (let bi = 0; bi < side.bundles.length; bi++) {
    const b = side.bundles[bi];
    for (let gi = 0; gi < b.groups.length; gi++) {
      const g = b.groups[gi];
      if (g.routeIndices.length > bestSize) {
        bestSize = g.routeIndices.length;
        best = { b: bi, g: gi };
      }
    }
  }
  return best;
}

function _raFindCrowdedGroup(side) {
  for (let bi = 0; bi < side.bundles.length; bi++) {
    const b = side.bundles[bi];
    for (let gi = 0; gi < b.groups.length; gi++) {
      if (b.groups[gi].routeIndices.length > SOLVER_GROUP_MAX_ROUTES) {
        return { b: bi, g: gi };
      }
    }
  }
  return null;
}

function _raFindCrowdedBundle(side) {
  for (let bi = 0; bi < side.bundles.length; bi++) {
    if (side.bundles[bi].routeIndices.length > SOLVER_BUNDLE_MAX_ROUTES) {
      return bi;
    }
  }
  return -1;
}

function _raFindParallelNearGroupPair(side) {
  for (let bi = 0; bi < side.bundles.length; bi++) {
    const b = side.bundles[bi];
    for (let i = 0; i < b.groups.length; i++) {
      for (let j = i + 1; j < b.groups.length; j++) {
        const ga = b.groups[i], gb = b.groups[j];
        const dx = Math.abs(ga.descentX - gb.descentX);
        if (dx >= SOLVER_MERGE_THRESHOLD) continue;
        const ya = _raGroupYRange(side, ga);
        const yb = _raGroupYRange(side, gb);
        if (ya[1] < yb[0] + 2 || yb[1] < ya[0] + 2) continue;
        return { bundleIdx: bi, gi: i, gj: j, dX: dx };
      }
    }
  }
  return null;
}

function _raGroupYRange(side, group) {
  let lo = Infinity, hi = -Infinity;
  for (const ri of group.routeIndices) {
    const r = side.routes[ri];
    if (r.bundleY != null) { lo = Math.min(lo, r.bundleY); }
    hi = Math.max(hi, r.planY);
  }
  if (lo === Infinity) lo = 0;
  return [lo, hi];
}

function _raFindBundledGroupToUnbundle(side) {
  let best = null, bestSize = 0;
  for (let bi = 0; bi < side.bundles.length; bi++) {
    const b = side.bundles[bi];
    for (let gi = 0; gi < b.groups.length; gi++) {
      const g = b.groups[gi];
      if (!g.attackBundled) continue;
      if (g.forceUnbundled) continue;
      const n = g.routeIndices.length;
      if (n > bestSize) {
        bestSize = n;
        best = { b: bi, g: gi };
      }
    }
  }
  return best;
}

function _raFindClusterToFix(side) {
  let best = null, bestScore = 0;
  for (const cluster of side.clusters || []) {
    let signal = 0;
    let anyUnflagged = false;
    for (const ri of cluster) {
      const r = side.routes[ri];
      if (r.clusterAttack) continue;
      anyUnflagged = true;
      if (!r.ownPts || r.ownPts.length < 2) continue;
      const p0 = r.ownPts[r.ownPts.length - 2];
      const p1 = r.ownPts[r.ownPts.length - 1];
      const len = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
      if (len > ROUTE_LAST_SEG_MIN * ROUTE_CLUSTER_LONG_MULT) signal++;
    }
    if (!anyUnflagged) continue;
    if (signal > bestScore) {
      bestScore = signal;
      best = cluster;
    }
  }
  return best;
}
"""
