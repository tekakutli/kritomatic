"""
pg_arrows_state.py — the left/right side structures, the initial
bundle proposal, the route-ref rebuild, and plan-space cluster
discovery and Y assignment.

A "side" is one of the two groups every route falls into: routes
whose target plan point is left of the plan's mid-x, and routes whose
target is right.  Sides are independent — the solver runs once per
side and never lets one side's bundles influence the other's.

Contents
--------
    _raAssignQuadrants       quadrant and upperHalf per route
    _raBuildSides            split routes into left/right side objects
    _raProposeInitialBundles seed bundles by plan-x continuity within
                             an upper/lower half
    _raRebuildRouteRefs      write bundleIdx/groupIdx back onto routes
    _raBundleFanOut          recompute bundleFanRank for every attack-
                             bundled group from the perpendicular
                             offset of each route from the trunk
    _raFindPlanClusters      connected components in plan space under
                             ROUTE_CLUSTER_RADIUS, size-filtered
    _raAssignClusterY        assign a staggered corridor Y to every
                             cluster-attack member
"""


STATE_JS = r"""
/* ==========================================================================
   SECTION 3 — SIDE STATE
   ========================================================================== */

function _raAssignQuadrants(side) {
  const routes = side.routes;
  if (!routes.length) {
    side.comX = 0;
    side.comY = 0;
    return;
  }
  let sumX = 0, sumY = 0;
  for (const r of routes) {
    sumX += r.planX;
    sumY += r.planY;
  }
  side.comX = sumX / routes.length;
  side.comY = sumY / routes.length;
  for (const r of routes) {
    const left = r.planX < side.comX;
    const top  = r.planY < side.comY;
    r.quadrant = left ? (top ? 0 : 2) : (top ? 1 : 3);
    r.upperHalf = top;
  }
}

function _raBuildSides(allRoutes) {
  const left  = {
    routes: [], bundles: [],
    bundleSpacing: ROUTE_BUNDLE_SPACING_MIN,
    groupSpacing:  ROUTE_GROUP_SPACING_MIN,
    peelStagger:   ROUTE_PEEL_STAGGER_MIN,
    clusters:      [],
    comX: 0, comY: 0,
    escapeInfo:    { fans: 0, evacuees: 0, applied: false,
                     preScore: null, postScore: null },
    rerouteInfo:   { candidates: 0, committed: 0,
                     preScore: null, postScore: null,
                     conflictTable: null },
  };
  const right = {
    routes: [], bundles: [],
    bundleSpacing: ROUTE_BUNDLE_SPACING_MIN,
    groupSpacing:  ROUTE_GROUP_SPACING_MIN,
    peelStagger:   ROUTE_PEEL_STAGGER_MIN,
    clusters:      [],
    comX: 0, comY: 0,
    escapeInfo:    { fans: 0, evacuees: 0, applied: false,
                     preScore: null, postScore: null },
    rerouteInfo:   { candidates: 0, committed: 0,
                     preScore: null, postScore: null,
                     conflictTable: null },
  };

  for (const r of allRoutes) {
    if (r.useLeft) left.routes.push(r);
    else           right.routes.push(r);
  }
  for (const s of [left, right]) {
    _raAssignQuadrants(s);
    _raProposeInitialBundles(s);
    _raFindPlanClusters(s);
  }
  window.__arrowsSides = { left, right };
  return { left, right };
}

function _raProposeInitialBundles(side) {
  const routes = side.routes;
  const indexed = routes.map((r, i) => ({ r, i }));
  indexed.sort((a, b) => {
    const ha = a.r.upperHalf ? 0 : 1;
    const hb = b.r.upperHalf ? 0 : 1;
    if (ha !== hb) return ha - hb;
    return a.r.planX - b.r.planX;
  });

  const bundles = [];
  let cur = [];
  let lastX = -Infinity;
  let lastH = -1;
  for (let k = 0; k < indexed.length; k++) {
    const { r, i } = indexed[k];
    const h = r.upperHalf ? 0 : 1;
    const newHalf = (h !== lastH);
    const gapExceeded = (cur.length > 0
                         && r.planX - lastX > ROUTE_BUNDLE_GAP_LIMIT);
    if (cur.length > 0 && (newHalf || gapExceeded)) {
      bundles.push(cur);
      cur = [];
    }
    cur.push(i);
    lastX = r.planX;
    lastH = h;
  }
  if (cur.length) bundles.push(cur);

  side.bundles = bundles.map((routeIndices) => {
    const groups = [{
      routeIndices: routeIndices.slice(),
      descentX: 0,
      attackBundled: false,
      trunkDir: null,
      forceUnbundled: false,
    }];
    const upperHalf = routeIndices.length > 0
      ? routes[routeIndices[0]].upperHalf : true;
    return { routeIndices: routeIndices.slice(), groups, upperHalf };
  });
  _raRebuildRouteRefs(side);
}

function _raRebuildRouteRefs(side) {
  for (let bi = 0; bi < side.bundles.length; bi++) {
    const b = side.bundles[bi];
    for (let gi = 0; gi < b.groups.length; gi++) {
      const g = b.groups[gi];
      for (const ri of g.routeIndices) {
        side.routes[ri].bundleIdx = bi;
        side.routes[ri].groupIdx  = gi;
      }
    }
  }
}

/* Recompute bundleFanRank for every attack-bundled group's
   non-cluster routes, sorted by perpendicular offset from the trunk.

   Called after every pass that changes group membership, so the
   ranks are never stale.  A stale rank (typically 0 from a
   freshly-created route) defeats both the along-trunk and the
   perpendicular fan offsets and leaves all bundled attacks on the
   same trunk line. */
function _raBundleFanOut(side) {
  for (const b of side.bundles) {
    for (const g of b.groups) {
      if (!g.attackBundled || !g.trunkDir) continue;
      const [ux, uy] = g.trunkDir;
      const nx = -uy, ny = ux;

      const entries = [];
      for (const ri of g.routeIndices) {
        const r = side.routes[ri];
        if (r.clusterAttack) continue;
        const H = r.planX - r.descentX;
        const V = r.planY - r.bundleY;
        const perp = H * nx + V * ny;
        entries.push({ ri, perp });
      }
      if (entries.length < 2) {
        for (const ri of g.routeIndices) {
          side.routes[ri].bundleFanRank = 0;
        }
        continue;
      }

      entries.sort((a, b) => a.perp - b.perp);
      const n = entries.length;
      const center = (n - 1) / 2;
      for (let k = 0; k < n; k++) {
        side.routes[entries[k].ri].bundleFanRank = k - center;
      }
      for (const ri of g.routeIndices) {
        const r = side.routes[ri];
        if (typeof r.bundleFanRank !== "number") r.bundleFanRank = 0;
      }
    }
  }
}


/* ==========================================================================
   SECTION 3b — PLAN-SPACE TARGET CLUSTERS
   ========================================================================== */

function _raFindPlanClusters(side) {
  const routes = side.routes;
  const n = routes.length;
  for (let i = 0; i < n; i++) routes[i].clusterId = -1;

  const visited = new Array(n).fill(false);
  const groups  = [];

  for (let i = 0; i < n; i++) {
    if (visited[i]) continue;
    const cluster = [i];
    visited[i] = true;
    const queue = [i];
    while (queue.length) {
      const cur = queue.shift();
      const r = routes[cur];
      for (let j = 0; j < n; j++) {
        if (visited[j]) continue;
        const q = routes[j];
        const d = Math.hypot(r.planX - q.planX, r.planY - q.planY);
        if (d < ROUTE_CLUSTER_RADIUS) {
          visited[j] = true;
          cluster.push(j);
          queue.push(j);
        }
      }
    }
    groups.push(cluster);
  }

  const significant = [];
  let cid = 0;
  for (const g of groups) {
    if (g.length < ROUTE_CLUSTER_MIN_SIZE) continue;
    for (const ri of g) routes[ri].clusterId = cid;
    significant.push(g);
    cid++;
  }
  side.clusters = significant;
  return significant;
}

function _raAssignClusterY(side) {
  for (const cluster of side.clusters || []) {
    let minPlanY = Infinity;
    let anyFlagged = false;
    for (const ri of cluster) {
      const r = side.routes[ri];
      minPlanY = Math.min(minPlanY, r.planY);
      if (r.clusterAttack) anyFlagged = true;
    }
    if (!anyFlagged) continue;
    const cyBase = minPlanY - ROUTE_CLUSTER_GAP;

    const members = [];
    for (const ri of cluster) {
      const r = side.routes[ri];
      if (r.clusterAttack) members.push(r);
    }
    members.sort((a, b) =>
      (a.planX - b.planX) || (a.planY - b.planY));

    const maxRank = members.length - 1;
    let stagger = CLUSTER_CORRIDOR_STAGGER;
    if (maxRank > 0) {
      const room = minPlanY - cyBase - 4;
      const maxStagger = room > 0 ? room / maxRank : 0;
      if (stagger > maxStagger) stagger = maxStagger;
    }

    for (let i = 0; i < members.length; i++) {
      const r = members[i];
      r.clusterY      = cyBase + i * stagger;
      r.clusterDropX  = r.planX;
      r.clusterRank   = i;
      r.clusterSize   = members.length;
    }

    for (const ri of cluster) {
      const r = side.routes[ri];
      if (r.clusterAttack) {
        if (typeof r.clusterY !== "number") r.clusterY = cyBase;
        if (typeof r.clusterDropX !== "number") r.clusterDropX = r.planX;
        if (typeof r.clusterRank !== "number") r.clusterRank = 0;
        if (typeof r.clusterSize !== "number") r.clusterSize = 1;
      }
    }
  }
}
"""
