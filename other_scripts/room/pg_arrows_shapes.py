"""
pg_arrows_shapes.py — candidate generation, the against-others score,
and the placement iteration.

_raAssignShapes walks the side's routes in plan-x order, builds the
candidate shapes each route could take, scores each against the
already-placed routes, and picks the best.  It then runs
ROUTE_PLACE_PASSES of local refinement, re-scoring every route against
the others until no route improves.

Three candidate builders, one per route kind:

    _raBuildCandidates               orthogonal, with a break Y and
                                     ROUTE_PLACE_PASSES variants
    _raBuildAttackBundleCandidates   a single bundled-attack shape,
                                     joint placed by the group's trunk
                                     direction and the route's
                                     bundleFanRank
    _raBuildClusterCandidates        a single direct segment from the
                                     shared anchor to the target

_raScoreAgainst is the per-candidate score.  It penalises parallel and
crossing contacts with foreign routes, tip proximity to foreign
segments, and — when a frozen tail is set during an escape-fan
re-solve — proximity to those frozen tails.
"""


SHAPES_JS = r"""
/* ==========================================================================
   SECTION 5 — SHAPE ASSIGNMENT
   ========================================================================== */

function _raBuildCandidates(r, side) {
  if (r.clusterAttack && typeof r.clusterY === "number") {
    return _raBuildClusterCandidates(r, side);
  }

  const b = side.bundles[r.bundleIdx];
  const g = b ? b.groups[r.groupIdx] : null;
  if (g && g.attackBundled && g.trunkDir) {
    return _raBuildAttackBundleCandidates(r, g);
  }

  const list = [];
  const { descentX, bundleY, planX, planY } = r;
  const MIN = ROUTE_LAST_SEG_MIN;
  const rank = r.peelRank || 0;
  const stagger = (side && side.peelStagger) || ROUTE_PEEL_STAGGER_MIN;

  const buildShape = (breakY) => {
    if (breakY < bundleY) breakY = bundleY;
    if (breakY > planY)   breakY = planY;
    const pts = [[descentX, bundleY]];
    if (breakY > bundleY + 0.5) pts.push([descentX, breakY]);
    const tail = pts[pts.length - 1];
    if (Math.abs(tail[0] - planX) > 0.5 ||
        Math.abs(tail[1] - planY) > 0.5) {
      pts.push([planX, planY]);
    }
    return pts;
  };

  const baseBreak = (typeof r.baseBreak === "number")
    ? r.baseBreak
    : planY - MIN - rank * stagger;

  list.push({ tag: "L0", pts: buildShape(baseBreak), isDiag: false });
  for (let k = 1; k <= 3; k++) {
    const bk = baseBreak - k * stagger;
    list.push({ tag: "L" + k, pts: buildShape(bk), isDiag: false });
  }
  list.push({ tag: "D", pts: buildShape(bundleY), isDiag: true });

  return list;
}

function _raBuildAttackBundleCandidates(r, group) {
  const [ux, uy] = group.trunkDir;
  const nx = -uy, ny = ux;
  const startX = r.descentX;
  const startY = r.bundleY;

  const H = r.planX - startX;
  const V = r.planY - startY;

  const tNatural = Math.max(0, H * ux + V * uy);
  const perpSigned = H * nx + V * ny;

  const rank = (typeof r.bundleFanRank === "number") ? r.bundleFanRank : 0;
  const jog  = (typeof r.bundleJogOffset === "number")
    ? r.bundleJogOffset : 0;

  const nComp = perpSigned - jog;
  const MIN = BUNDLED_ATTACK_MIN;
  const disc = Math.sqrt(Math.max(0, MIN * MIN - nComp * nComp));

  let h = rank * BUNDLE_FAN_STEP;
  if (Math.abs(h) < disc) {
    h = (rank < 0 ? -1 : 1) * disc;
  }

  let t = tNatural - h;
  if (t < 0) t = 0;

  /* Perpendicular offset.  Without this, the joint sits on the trunk
     line and every anchor-to-joint segment is collinear with the
     trunk — which reads as a fan of parallel diagonals from the same
     anchor, however much the short attack segments diverge.  The
     perpendicular offset slides each joint off the trunk in the
     direction of its target, so the anchor-to-joint strokes fan out
     too. */
  const perpJog = rank * BUNDLE_PERP_FAN_STEP;

  const qx = startX + ux * t + nx * (jog + perpJog);
  const qy = startY + uy * t + ny * (jog + perpJog);

  const raw = [
    [startX, startY],
    [qx, qy],
    [r.planX, r.planY],
  ];

  const pts = [raw[0]];
  for (let i = 1; i < raw.length; i++) {
    const prev = pts[pts.length - 1];
    const d = Math.hypot(raw[i][0] - prev[0], raw[i][1] - prev[1]);
    if (d > 0.5) pts.push(raw[i]);
  }

  return [{ tag: "B", pts, isDiag: false }];
}

function _raBuildClusterCandidates(r, side) {
  const { descentX, bundleY, planX, planY } = r;
  return [{
    tag: "C",
    pts: [[descentX, bundleY], [planX, planY]],
    isDiag: false,
  }];
}

function _raScoreAgainst(r, pts, others) {
  let p = 0, c = 0, tip = 0;
  const candSegs = _raPathSegments(pts);

  for (const q of others) {
    if (q === r) continue;
    if (!q.ownPts) continue;

    const sameGroup = (q.bundleIdx === r.bundleIdx &&
                       q.groupIdx  === r.groupIdx);
    const sameCluster = (r.clusterAttack && q.clusterAttack &&
                         r.clusterId >= 0 && r.clusterId === q.clusterId);

    if (!sameGroup && !sameCluster) {
      const s = _raPathPairScore(pts, q.ownPts);
      p += s.p; c += s.c;
    }

    if (sameCluster) continue;

    const qSegs = _raPathSegments(q.ownPts);
    tip += _raTipClearanceAgainstSegs(r.planX, r.planY, qSegs);
    tip += _raTipClearanceAgainstSegs(q.planX, q.planY, candSegs);
  }

  if (_raFrozenPolylines) {
    for (const fpts of _raFrozenPolylines) {
      const s = _raPathPairScore(pts, fpts);
      p += s.p; c += s.c;
      const fSegs = _raPathSegments(fpts);
      tip += _raTipClearanceAgainstSegs(r.planX, r.planY, fSegs);
    }
  }

  return { p, c, tip, total: p + c + tip };
}

function _raAssignShapes(side) {
  const sorted = side.routes.slice().sort((a, b) => a.planX - b.planX);

  const placed = [];
  for (const r of sorted) {
    const candidates = _raBuildCandidates(r, side);
    let best = null;
    for (const c of candidates) {
      const score = _raScoreAgainst(r, c.pts, placed);
      if (!best || score.total < best.score.total - 0.5) best = { c, score };
      if (!c.isDiag && score.total < ROUTE_ACCEPT_SCORE) break;
    }
    r.ownPts = best.c.pts;
    r.isDiag = best.c.isDiag;
    r.tag    = best.c.tag;
    placed.push(r);
  }

  for (let pass = 0; pass < ROUTE_PLACE_PASSES; pass++) {
    let changed = false;
    for (const r of sorted) {
      const others = sorted.filter(q => q !== r);
      const before = _raScoreAgainst(r, r.ownPts, others);
      const candidates = _raBuildCandidates(r, side);
      let best = null;
      for (const c of candidates) {
        const s = _raScoreAgainst(r, c.pts, others);
        if (!best || s.total < best.score.total - 0.5) best = { c, score: s };
      }
      if (best && best.score.total < before.total - 0.5) {
        r.ownPts = best.c.pts;
        r.isDiag = best.c.isDiag;
        r.tag    = best.c.tag;
        changed = true;
      }
    }
    if (!changed) break;
  }
}
"""
