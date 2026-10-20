"""
pg_arrows_layout.py — the multi-pass layout step.

_raLayoutSide runs ten ordered passes and, for each, writes the
positions every later pass reads:

    1    bundle Y, split by upper/lower half
    1b   satellite bundle Y (long-attack splits)
    2    vertical-budget shift, so an upper bundle never sits below
         the deepest target it serves
    3    descent-group X, clamped to the sign of the side
    3.5  collision resolution between descent columns
    4    peel rank, from plan Y within a group
    4b   base break Y, staggered by peel rank
    4c   group attack-bundle detection
    4d   group descent X candidate search
    4d.5 column merge for near-collinear descent columns
    4e   bundle fan-out (bundleFanRank recompute)
    4f   tiny bundled attack cluster detection and jog assignment
    4g   local baseBreak search against tip clearance
    5    cluster Y assignment (delegated to pg_arrows_state)
    8    cluster anchor unification (delegated to pg_arrows_passes)
    9    cluster attack dedupe (delegated to pg_arrows_passes)

The three helper functions in this module are the passes' own
sub-routines: the attack-bundle decision, the group-descent penalty
evaluation, and the tiny bundled attack cluster finder.
"""


LAYOUT_JS = r"""
/* ==========================================================================
   SECTION 4 — LAYOUT
   ========================================================================== */

function _raComputeGroupAttackBundle(side, g) {
  g.attackBundled = false;
  g.trunkDir = null;
  if (g.forceUnbundled) return;

  const routes = g.routeIndices.map(ri => side.routes[ri]);
  if (routes.length < 2) return;

  const dirs = [];
  let sumDx = 0, sumDy = 0;
  for (const r of routes) {
    const H = r.planX - r.descentX;
    const V = r.planY - r.bundleY;
    const L = Math.hypot(H, V);
    if (L < 1e-6) continue;
    const dx = H / L, dy = V / L;
    dirs.push([dx, dy]);
    sumDx += dx;
    sumDy += dy;
  }
  if (dirs.length < 2) return;

  const normLen = Math.hypot(sumDx, sumDy);
  if (normLen < 1e-6) return;
  const ux = sumDx / normLen, uy = sumDy / normLen;

  let maxSpread = 0;
  for (const [dx, dy] of dirs) {
    const cos = Math.min(1, Math.abs(dx * ux + dy * uy));
    const ang = Math.acos(cos);
    if (ang > maxSpread) maxSpread = ang;
  }
  if (maxSpread > ROUTE_ATTACK_BUNDLE_TOL) return;

  g.attackBundled = true;
  g.trunkDir = [ux, uy];
}

function _raEvaluateGroupDescentCandidate(side, g, candidateX) {
  if (g.routeIndices.length === 0) return 0;
  const bundleY = side.routes[g.routeIndices[0]].bundleY;

  let sumDx = 0, sumDy = 0, n = 0;
  for (const ri of g.routeIndices) {
    const r = side.routes[ri];
    const H = r.planX - candidateX;
    const V = r.planY - bundleY;
    const L = Math.hypot(H, V);
    if (L < 1e-6) continue;
    sumDx += H / L;
    sumDy += V / L;
    n++;
  }
  if (n < 2) return 0;

  const nl = Math.hypot(sumDx, sumDy);
  if (nl < 1e-6) return 0;
  const ux = sumDx / nl, uy = sumDy / nl;

  let maxT = 0;
  for (const ri of g.routeIndices) {
    const r = side.routes[ri];
    const H = r.planX - candidateX;
    const V = r.planY - bundleY;
    let t = H * ux + V * uy;
    if (t > maxT) maxT = t;
  }
  const ex = candidateX + ux * maxT, ey = bundleY + uy * maxT;

  let pen = 0;
  for (const ri of g.routeIndices) {
    const r = side.routes[ri];
    const d = pointToSegmentDist(r.planX, r.planY,
                                  candidateX, bundleY, ex, ey);
    if (d < ROUTE_TIP_CLEARANCE) {
      pen += (ROUTE_TIP_CLEARANCE - d) * ROUTE_TIP_W;
    }
  }
  return pen;
}

function _raFindTinyBundledClusters(side, g) {
  if (!g.attackBundled || !g.trunkDir) return [];
  const [ux, uy] = g.trunkDir;
  const nx = -uy, ny = ux;

  const entries = [];
  for (const ri of g.routeIndices) {
    const r = side.routes[ri];
    if (r.clusterAttack) continue;

    const startX = r.descentX, startY = r.bundleY;
    const H = r.planX - startX, V = r.planY - startY;

    const tNatural = Math.max(0, H * ux + V * uy);
    const perpSigned = H * nx + V * ny;
    const perpDist = Math.abs(perpSigned);

    const MIN = BUNDLED_ATTACK_MIN;
    const disc = Math.sqrt(Math.max(0, MIN * MIN - perpDist * perpDist));

    const rank = (typeof r.bundleFanRank === "number")
      ? r.bundleFanRank : 0;
    let h = rank * BUNDLE_FAN_STEP;
    if (Math.abs(h) < disc) h = (rank < 0 ? -1 : 1) * disc;

    let t = tNatural - h;
    if (t < 0) t = 0;

    const qx = startX + ux * t, qy = startY + uy * t;
    const attLen = Math.hypot(r.planX - qx, r.planY - qy);

    entries.push({
      ri,
      x0: qx, y0: qy,
      x1: r.planX, y1: r.planY,
      len: attLen,
    });
  }

  const adj = new Map();
  for (const e of entries) adj.set(e.ri, new Set());
  for (let i = 0; i < entries.length; i++) {
    const A = entries[i];
    if (A.len > BUNDLE_TINY_ATTACK_MAX) continue;
    for (let j = i + 1; j < entries.length; j++) {
      const B = entries[j];
      if (B.len > BUNDLE_TINY_ATTACK_MAX) continue;
      const d = _raSegSegDist(A.x0, A.y0, A.x1, A.y1,
                              B.x0, B.y0, B.x1, B.y1);
      if (d < BUNDLE_TINY_PROX) {
        adj.get(A.ri).add(B.ri);
        adj.get(B.ri).add(A.ri);
      }
    }
  }

  const visited = new Set();
  const clusters = [];
  for (const e of entries) {
    if (visited.has(e.ri)) continue;
    if (adj.get(e.ri).size === 0) { visited.add(e.ri); continue; }
    const comp = [];
    const queue = [e.ri];
    visited.add(e.ri);
    while (queue.length) {
      const cur = queue.shift();
      comp.push(cur);
      for (const nbr of adj.get(cur)) {
        if (!visited.has(nbr)) { visited.add(nbr); queue.push(nbr); }
      }
    }
    if (comp.length >= 2) clusters.push(comp);
  }
  return clusters;
}

function _raLayoutSide(side) {
  {
    let sumX = 0, sumY = 0;
    const rr = side.routes;
    for (const r of rr) { sumX += r.planX; sumY += r.planY; }
    side.comX = rr.length > 0 ? sumX / rr.length : 0;
    side.comY = rr.length > 0 ? sumY / rr.length : 0;
  }

  /* ---- Pass 1: bundle Y, split by half ---- */
  {
    const upperBundles = side.bundles.filter(b => b.upperHalf && !b.isSatellite);
    const lowerBundles = side.bundles.filter(b => !b.upperHalf && !b.isSatellite);

    const distOf = (b) => {
      let d = 0;
      for (const ri of b.routeIndices) {
        const r = side.routes[ri];
        d = Math.max(d, Math.abs(r.corridorX - r.planX));
      }
      return d;
    };
    upperBundles.sort((a, b) => distOf(b) - distOf(a));
    lowerBundles.sort((a, b) => distOf(b) - distOf(a));

    const topBaseY    = ROUTE_BUNDLE_SCREEN_MIN;
    const bottomBaseY = layout.dividerY - ROUTE_BUNDLE_SCREEN_MIN;

    for (let k = 0; k < upperBundles.length; k++) {
      const b = upperBundles[k];
      const y = topBaseY + k * side.bundleSpacing;
      for (const ri of b.routeIndices) side.routes[ri].bundleY = y;
    }
    for (let k = 0; k < lowerBundles.length; k++) {
      const b = lowerBundles[k];
      const y = bottomBaseY - k * side.bundleSpacing;
      for (const ri of b.routeIndices) side.routes[ri].bundleY = y;
    }
  }

  /* Satellites */
  {
    let lowestUpperSatY = -Infinity;
    let highestLowerSatY = Infinity;
    for (const b of side.bundles) {
      if (!b.isSatellite) continue;
      if (b.routeIndices.length === 0) continue;

      let minPY = Infinity, maxPY = -Infinity;
      for (const ri of b.routeIndices) {
        const r = side.routes[ri];
        if (r.planY < minPY) minPY = r.planY;
        if (r.planY > maxPY) maxPY = r.planY;
      }
      if (minPY === Infinity) continue;

      let targetY;
      if (b.upperHalf) {
        targetY = minPY - ROUTE_LAST_SEG_MIN * 2;
        if (targetY < ROUTE_BUNDLE_SCREEN_MIN) targetY = ROUTE_BUNDLE_SCREEN_MIN;
        if (lowestUpperSatY > -Infinity
            && targetY < lowestUpperSatY + side.bundleSpacing) {
          targetY = lowestUpperSatY + side.bundleSpacing;
        }
        lowestUpperSatY = targetY;
      } else {
        targetY = maxPY + ROUTE_LAST_SEG_MIN * 2;
        const bottomLimit = layout.dividerY - ROUTE_BUNDLE_SCREEN_MIN;
        if (targetY > bottomLimit) targetY = bottomLimit;
        if (highestLowerSatY < Infinity
            && targetY > highestLowerSatY - side.bundleSpacing) {
          targetY = highestLowerSatY - side.bundleSpacing;
        }
        highestLowerSatY = targetY;
      }

      for (const ri of b.routeIndices) {
        side.routes[ri].bundleY = targetY;
      }
    }
  }

  /* ---- Pass 2: vertical-budget shift ---- */
  {
    let neededShift = 0;
    for (const b of side.bundles) {
      if (b.isSatellite) continue;
      let minPlanY = Infinity;
      for (const ri of b.routeIndices) {
        minPlanY = Math.min(minPlanY, side.routes[ri].planY);
      }
      if (minPlanY === Infinity) continue;
      const bY = side.routes[b.routeIndices[0]].bundleY;
      const maxAllowed = minPlanY - ROUTE_LAST_SEG_MIN;
      if (bY > maxAllowed && b.upperHalf) {
        neededShift = Math.max(neededShift, bY - maxAllowed);
      }
    }
    if (neededShift > 0) {
      let minBundleY = Infinity;
      for (const b of side.bundles) {
        if (b.isSatellite) continue;
        if (!b.upperHalf) continue;
        if (!b.routeIndices.length) continue;
        minBundleY = Math.min(minBundleY,
          side.routes[b.routeIndices[0]].bundleY);
      }
      if (minBundleY !== Infinity) {
        const maxShift = Math.max(0, minBundleY - ROUTE_BUNDLE_SCREEN_MIN);
        const actualShift = Math.min(neededShift, maxShift);
        if (actualShift > 0) {
          for (const b of side.bundles) {
            if (b.isSatellite) continue;
            if (!b.upperHalf) continue;
            for (const ri of b.routeIndices) {
              side.routes[ri].bundleY -= actualShift;
            }
          }
        }
      }
    }
  }

  /* ---- Pass 3: descent group X, clamped by half ---- */
  for (const b of side.bundles) {
    const withMedian = b.groups.map((g, gi) => {
      const xs = g.routeIndices.map(ri => side.routes[ri].planX);
      xs.sort((a, z) => a - z);
      const median = xs[Math.floor(xs.length / 2)];
      return { g, gi, median };
    });
    withMedian.sort((a, b) => a.median - b.median);

    const sign = side.routes[withMedian.length ? withMedian[0].g.routeIndices[0] : 0]
      ? (side.routes[withMedian[0].g.routeIndices[0]].sign) : 1;

    let prevX = -Infinity;
    for (const w of withMedian) {
      let x = w.median;
      if (prevX !== -Infinity) {
        x = Math.max(x, prevX + side.groupSpacing * sign > 0
                        ? prevX + side.groupSpacing
                        : prevX - side.groupSpacing);
      }
      if (sign > 0) x = Math.max(w.median, prevX === -Infinity ? w.median : prevX + side.groupSpacing);
      else          x = Math.min(w.median, prevX === -Infinity ? w.median : prevX - side.groupSpacing);

      const cx = side.comX;
      if (w.median < cx && x >= cx) x = cx - ROUTE_MIN_SEP;
      else if (w.median >= cx && x <= cx) x = cx + ROUTE_MIN_SEP;

      w.g.descentX = x;
      prevX = x;
    }

    for (const g of b.groups) {
      for (const ri of g.routeIndices) {
        side.routes[ri].descentX = g.descentX;
      }
    }
  }

  /* ---- Pass 3.5 ---- */
  {
    const routes = side.routes;
    const n = routes.length;
    const memberSet = new Set();
    const acceptedCols = [];

    const allGroups = [];
    for (const b of side.bundles) {
      for (const g of b.groups) {
        if (g.routeIndices.length === 0) continue;
        allGroups.push(g);
      }
    }

    for (const g of allGroups) {
      let x = g.descentX;
      for (const px of acceptedCols) {
        if (Math.abs(px - x) < ROUTE_MIN_SEP) { x = px; break; }
      }
      let already = false;
      for (const px of acceptedCols) {
        if (Math.abs(px - x) < 0.5) { already = true; break; }
      }
      if (!already) acceptedCols.push(x);
      g.descentX = x;
      for (const ri of g.routeIndices) routes[ri].descentX = x;
    }

    for (const g of allGroups) {
      const x0 = g.descentX;
      const bundleY = routes[g.routeIndices[0]].bundleY;

      let deepPlanY = -Infinity;
      for (const ri of g.routeIndices) {
        deepPlanY = Math.max(deepPlanY, routes[ri].planY);
      }
      if (bundleY >= deepPlanY) continue;

      memberSet.clear();
      for (const ri of g.routeIndices) memberSet.add(ri);

      const yMin = bundleY - ROUTE_TIP_CLEARANCE;
      const yMax = deepPlanY + ROUTE_TIP_CLEARANCE;

      const isClear = (candidateX) => {
        for (let i = 0; i < n; i++) {
          if (memberSet.has(i)) continue;
          const q = routes[i];
          if (Math.abs(q.planX - candidateX) >= ROUTE_TIP_CLEARANCE) continue;
          if (q.planY < yMin || q.planY > yMax) continue;
          return false;
        }
        return true;
      };

      if (isClear(x0)) continue;

      const sign = routes[g.routeIndices[0]].sign;
      const step = ROUTE_TIP_CLEARANCE * 1.5;
      let newX = x0;

      for (let trial = 1; trial <= 30; trial++) {
        const cand = x0 + sign * step * trial;
        if (isClear(cand)) { newX = cand; break; }
      }
      if (newX === x0) {
        for (let trial = 1; trial <= 30; trial++) {
          const cand = x0 - sign * step * trial;
          if (isClear(cand)) { newX = cand; break; }
        }
      }

      for (const px of acceptedCols) {
        if (Math.abs(px - newX) < ROUTE_MIN_SEP) { newX = px; break; }
      }
      let already = false;
      for (const px of acceptedCols) {
        if (Math.abs(px - newX) < 0.5) { already = true; break; }
      }
      if (!already) acceptedCols.push(newX);

      if (newX !== x0) {
        g.descentX = newX;
        for (const ri of g.routeIndices) routes[ri].descentX = newX;
      }
    }
  }

  /* ---- Pass 4: peel rank ---- */
  for (const b of side.bundles) {
    for (const g of b.groups) {
      const sorted = g.routeIndices.slice().sort((i, j) =>
        side.routes[i].planY - side.routes[j].planY);
      for (let rank = 0; rank < sorted.length; rank++) {
        side.routes[sorted[rank]].peelRank = rank;
      }
    }
  }

  /* ---- Pass 4b ---- */
  for (const b of side.bundles) {
    for (const g of b.groups) {
      const sorted = g.routeIndices.slice().sort((i, j) =>
        side.routes[i].peelRank - side.routes[j].peelRank);
      const stagger = side.peelStagger;
      let prevBreak = Infinity;
      for (const ri of sorted) {
        const r = side.routes[ri];
        const H = r.planX - r.descentX;
        const minVert = Math.sqrt(Math.max(0,
          ROUTE_LAST_SEG_MIN * ROUTE_LAST_SEG_MIN - H * H));
        let bk = r.planY - minVert;
        if (bk > prevBreak - stagger) bk = prevBreak - stagger;
        if (bk < r.bundleY)           bk = r.bundleY;
        r.baseBreak = bk;
        prevBreak = bk;
      }
    }
  }

  /* ---- Pass 4c ---- */
  for (const b of side.bundles) {
    for (const g of b.groups) {
      _raComputeGroupAttackBundle(side, g);
    }
  }

  /* ---- Pass 4d ---- */
  for (const b of side.bundles) {
    for (const g of b.groups) {
      if (!g.attackBundled) continue;
      if (g.routeIndices.length < 2) continue;

      const planXs = g.routeIndices
        .map(ri => side.routes[ri].planX)
        .sort((a, z) => a - z);

      const curPenalty = _raEvaluateGroupDescentCandidate(side, g, g.descentX);
      if (curPenalty === 0) continue;

      const candidates = [g.descentX];
      {
        let bestW = 0, bestMid = null;
        for (let i = 0; i < planXs.length - 1; i++) {
          const w = planXs[i + 1] - planXs[i];
          if (w > bestW) {
            bestW = w;
            bestMid = (planXs[i] + planXs[i + 1]) / 2;
          }
        }
        if (bestMid !== null) candidates.push(bestMid);
      }
      candidates.push(planXs[0] - ROUTE_TIP_CLEARANCE * 1.5);
      candidates.push(planXs[planXs.length - 1] + ROUTE_TIP_CLEARANCE * 1.5);

      let bestX = g.descentX, bestPen = curPenalty;
      for (const c of candidates) {
        const p = _raEvaluateGroupDescentCandidate(side, g, c);
        if (p < bestPen - 0.5) { bestPen = p; bestX = c; }
      }

      if (bestX !== g.descentX) {
        g.descentX = bestX;
        for (const ri of g.routeIndices) {
          side.routes[ri].descentX = bestX;
        }
      }
    }
  }

  /* ---- Pass 4d.5 ---- */
  {
    const allGroups = [];
    for (const b of side.bundles) {
      for (const g of b.groups) {
        if (g.routeIndices.length === 0) continue;
        allGroups.push(g);
      }
    }
    allGroups.sort((a, c) => a.descentX - c.descentX);

    const colClusters = [];
    let cur = [];
    for (const g of allGroups) {
      if (cur.length === 0 ||
          g.descentX - cur[cur.length - 1].descentX < ROUTE_MIN_SEP) {
        cur.push(g);
      } else {
        colClusters.push(cur);
        cur = [g];
      }
    }
    if (cur.length) colClusters.push(cur);

    for (const cluster of colClusters) {
      if (cluster.length < 2) continue;
      let sum = 0;
      for (const g of cluster) sum += g.descentX;
      const mean = sum / cluster.length;
      for (const g of cluster) {
        g.descentX = mean;
        for (const ri of g.routeIndices) side.routes[ri].descentX = mean;
      }
    }
  }
  for (const b of side.bundles) {
    for (const g of b.groups) {
      _raComputeGroupAttackBundle(side, g);
    }
  }

  /* ---- Pass 4e: bundle attack fan-out ---- */
  _raBundleFanOut(side);

  /* ---- Pass 4f ---- */
  for (const b of side.bundles) {
    for (const g of b.groups) {
      for (const ri of g.routeIndices) {
        side.routes[ri].bundleJogOffset = 0;
      }
      if (!g.attackBundled || !g.trunkDir) continue;
      if (g.routeIndices.length < 2) continue;

      const clusters = _raFindTinyBundledClusters(side, g);
      for (const cluster of clusters) {
        let sumRank = 0;
        for (const ri of cluster) {
          sumRank += side.routes[ri].bundleFanRank || 0;
        }
        const meanRank = sumRank / cluster.length;
        for (const ri of cluster) {
          const r = side.routes[ri];
          const rank = r.bundleFanRank || 0;
          r.bundleJogOffset = (rank - meanRank) * BUNDLE_JOG_STEP;
        }
      }
    }
  }

  /* ---- Pass 4g ---- */
  {
    const routes = side.routes;
    const n = routes.length;

    for (let ri = 0; ri < n; ri++) {
      const r = routes[ri];
      if (!r.ownPts || r.ownPts.length < 2) continue;
      if (r.clusterAttack) continue;
      if (r.tag === "R" || r.tag === "B" || r.tag === "C") continue;

      const b = side.bundles[r.bundleIdx];
      const g = b ? b.groups[r.groupIdx] : null;
      if (g && g.attackBundled && g.trunkDir) continue;

      const curBreak = (typeof r.baseBreak === "number")
        ? r.baseBreak : r.bundleY;

      const buildShape = (bY) => {
        let bk = bY;
        if (bk < r.bundleY) bk = r.bundleY;
        if (bk > r.planY)   bk = r.planY;
        const pts = [[r.descentX, r.bundleY]];
        if (bk > r.bundleY + 0.5) pts.push([r.descentX, bk]);
        const tail = pts[pts.length - 1];
        if (Math.abs(tail[0] - r.planX) > 0.5 ||
            Math.abs(tail[1] - r.planY) > 0.5) {
          pts.push([r.planX, r.planY]);
        }
        return pts;
      };

      const evalPenalty = (bY) => {
        const candPts = buildShape(bY);
        const candSegs = _raPathSegments(candPts);
        let pen = 0;
        for (let j = 0; j < n; j++) {
          if (j === ri) continue;
          const q = routes[j];
          if (!q.ownPts) continue;
          pen += _raTipClearanceAgainstSegs(q.planX, q.planY, candSegs);
        }
        return pen;
      };

      const curPen = evalPenalty(curBreak);
      if (curPen < 1) continue;

      let bestBk = curBreak, bestPen = curPen;
      const offsets = [-50, -40, -30, -25, -20, -15, -10, -5,
                        5, 10, 15, 20, 25, 30, 40, 50];
      for (const off of offsets) {
        const cand = curBreak + off;
        if (cand < r.bundleY || cand > r.planY) continue;
        const p = evalPenalty(cand);
        if (p < bestPen - 0.5) { bestPen = p; bestBk = cand; }
      }

      if (bestBk !== curBreak) {
        r.baseBreak = bestBk;
      }
    }
  }

  /* ---- Pass 5 ---- */
  _raAssignClusterY(side);

  /* ---- Pass 8 ---- */
  _raUnifyClusterAnchors(side);

  /* ---- Pass 9 ---- */
  _raDedupeClusterAttacks(side);
}
"""
