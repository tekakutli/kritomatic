"""
pg_arrows_diag.py — the structured diagnostic dump, the diagnostic
button, and the arrow-visibility toggle.

Three concerns, all UI-adjacent:

    • dumpArrowsDiagnostics() — builds a structured dump (available
      at window.__arrowsDiag), prints it line by line to the browser
      console, and optionally POSTs it to /diag so the same text
      lands in the Python terminal's scrollback.

    • The diagnostic button, injected into the panel before the
      hidden <hr> — same slot pg_panel reserves under the OUTPUT
      section label.

    • The arrow-visibility toggle, a checkbox that flips
      _raArrowsEnabled and re-requests a redraw.  Bound to Ctrl+Shift+A
      as well.

The three are together because they share no state with the solver;
they only read it.  dumpArrowsDiagnostics reads the live side objects
(window.__arrowsSides), the current route cache, and the scoring
function, and returns a plain object the caller can keep.

Translation
-----------
The two GUI elements this module creates — the diagnostic button and
the arrow-visibility checkbox — carry T()-sourced labels and titles.
The diagnostic dump itself is developer-facing (browser console,
Python terminal) and is left in English on purpose: it names the
tuning constants, the fields it dumps, and the codes it prints, all
of which are identifiers a translator would not translate anyway.
"""


DIAG_JS = r"""
/* ==========================================================================
   SECTION 12 — DIAGNOSTICS
   ========================================================================== */

window.__arrowsDiag  = null;
window.__arrowsSides = null;

function _radSegsTable() {
  const out = [];
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    out.push({
      idx: i,
      tag: s.tag || ('seg' + i),
      kind: s.kind || 'wall',
      a: s.a.slice(),
      b: s.b.slice(),
      mid: [(s.a[0] + s.b[0]) / 2, (s.a[1] + s.b[1]) / 2],
      len: s.len,
      u0: s.u0, u1: s.u1,
      z_lo: s.z_lo, z_hi: s.z_hi,
    });
  }
  return out;
}

function _radRouteRow(r, idx) {
  const s = WALL.segments[r.segIdx];
  const wxMid = s ? (s.a[0] + s.b[0]) / 2 : null;
  const wyMid = s ? (s.a[1] + s.b[1]) / 2 : null;

  let attackLen = null, attackAngleDeg = null;
  if (r.ownPts && r.ownPts.length >= 2) {
    const a = r.ownPts[r.ownPts.length - 2];
    const b = r.ownPts[r.ownPts.length - 1];
    attackLen = +Math.hypot(b[0] - a[0], b[1] - a[1]).toFixed(1);
    attackAngleDeg = +(Math.atan2(b[1] - a[1], b[0] - a[0])
                       * 180 / Math.PI).toFixed(1);
  }

  let groupAttackBundled = false;
  let groupForceUnbundled = false;
  let groupTrunkDir = null;
  let groupSize = 0;
  let bundleIsSatellite = false;
  let bundleUpperHalf = true;
  const sides = window.__arrowsSides;
  if (sides) {
    const side = r.useLeft ? sides.left : sides.right;
    if (side) {
      const b = side.bundles[r.bundleIdx];
      const g = b ? b.groups[r.groupIdx] : null;
      if (b) {
        bundleIsSatellite = !!b.isSatellite;
        bundleUpperHalf = !!b.upperHalf;
      }
      if (g) {
        groupAttackBundled = !!g.attackBundled;
        groupForceUnbundled = !!g.forceUnbundled;
        groupTrunkDir = g.trunkDir || null;
        groupSize = g.routeIndices.length;
      }
    }
  }

  return {
    idx,
    segIdx: r.segIdx,
    segTag: s ? s.tag : '?',
    segKind: s ? (s.kind || 'wall') : '?',
    segLen: s ? +s.len.toFixed(1) : null,
    worldMid: (wxMid !== null)
      ? [+wxMid.toFixed(0), +wyMid.toFixed(0)] : null,

    side: r.useLeft ? 'L' : 'R',
    quadrant: r.quadrant,
    upperHalf: !!r.upperHalf,
    stripX: +r.stripX.toFixed(1),
    planX: +r.planX.toFixed(1),
    planY: +r.planY.toFixed(1),

    bundleIdx: r.bundleIdx,
    groupIdx: r.groupIdx,
    groupSize,
    bundleIsSatellite,
    bundleUpperHalf,
    peelRank: r.peelRank,
    bundleY: +r.bundleY.toFixed(1),
    baseBreak: (typeof r.baseBreak === 'number')
      ? +r.baseBreak.toFixed(1) : null,
    bundleFanRank: (typeof r.bundleFanRank === 'number')
      ? +r.bundleFanRank.toFixed(2) : null,
    bundleJogOffset: (typeof r.bundleJogOffset === 'number')
      ? +r.bundleJogOffset.toFixed(1) : 0,
    descentX: +r.descentX.toFixed(1),

    clusterId: r.clusterId,
    clusterAttack: !!r.clusterAttack,
    clusterY: (typeof r.clusterY === 'number')
      ? +r.clusterY.toFixed(1) : null,
    clusterDropX: (typeof r.clusterDropX === 'number')
      ? +r.clusterDropX.toFixed(1) : null,
    clusterRank: (typeof r.clusterRank === 'number')
      ? r.clusterRank : null,
    clusterSize: (typeof r.clusterSize === 'number')
      ? r.clusterSize : null,

    groupAttackBundled,
    groupForceUnbundled,
    groupTrunkDir,

    escaped:  !!r.escaped,
    survivor: !!r.survivor,

    tag: r.tag,
    isDiag: !!r.isDiag,
    attackLen,
    attackAngleDeg,
    pts: r.ownPts
      ? r.ownPts.map(p => [+p[0].toFixed(1), +p[1].toFixed(1)])
      : null,
  };
}

function _radSideSummary(side, name) {
  const score = _raScoreSide(side);
  return {
    side: name,
    routeCount: side.routes.length,
    bundleCount: side.bundles.length,
    bundleSpacing: side.bundleSpacing,
    groupSpacing: side.groupSpacing,
    peelStagger: side.peelStagger,
    clusterCount: (side.clusters || []).length,
    comX: side.comX, comY: side.comY,
    escapeInfo: side.escapeInfo,
    rerouteInfo: side.rerouteInfo,
    score,
  };
}

function _radTopTipViolations(side, nMax) {
  const out = [];
  const routes = side.routes;
  const segsByRoute = routes.map(r =>
    r.ownPts ? _raPathSegments(r.ownPts) : []);
  for (let i = 0; i < routes.length; i++) {
    const a = routes[i];
    if (!a.ownPts) continue;
    for (let j = 0; j < routes.length; j++) {
      if (i === j) continue;
      const b = routes[j];
      if (!b.ownPts) continue;
      if (a.clusterAttack && b.clusterAttack &&
          a.clusterId >= 0 && a.clusterId === b.clusterId) continue;
      for (let si = 0; si < segsByRoute[j].length; si++) {
        const s = segsByRoute[j][si];
        const d = pointToSegmentDist(a.planX, a.planY,
                                      s.x0, s.y0, s.x1, s.y1);
        if (d < ROUTE_TIP_CLEARANCE) {
          const t = ROUTE_TIP_CLEARANCE - d;
          const pen = t * ROUTE_TIP_W + t * t * ROUTE_TIP_QUAD_W;
          out.push({
            tipRoute:  i,
            tipSeg:    a.segIdx,
            tipTag:    (WALL.segments[a.segIdx] || {}).tag || '?',
            hitRoute:  j,
            hitSeg:    b.segIdx,
            hitTag:    (WALL.segments[b.segIdx] || {}).tag || '?',
            hitSegIdx: si,
            dist:      +d.toFixed(1),
            penalty:   +pen.toFixed(1),
          });
        }
      }
    }
  }
  out.sort((x, y) => y.penalty - x.penalty);
  return out.slice(0, nMax);
}

function _radTinyBundledClusters(side) {
  const out = [];
  for (let bi = 0; bi < side.bundles.length; bi++) {
    const b = side.bundles[bi];
    for (let gi = 0; gi < b.groups.length; gi++) {
      const g = b.groups[gi];
      if (!g.attackBundled || !g.trunkDir) continue;
      const clusters = _raFindTinyBundledClusters(side, g);
      for (let ci = 0; ci < clusters.length; ci++) {
        const cluster = clusters[ci];
        let sumRank = 0;
        for (const ri of cluster) {
          sumRank += side.routes[ri].bundleFanRank || 0;
        }
        const meanRank = sumRank / cluster.length;
        const members = cluster.map(ri => {
          const r = side.routes[ri];
          const s = WALL.segments[r.segIdx];
          const rank = r.bundleFanRank || 0;
          const jog = (rank - meanRank) * BUNDLE_JOG_STEP;
          const attackLen = (() => {
            if (!r.ownPts || r.ownPts.length < 2) return null;
            const a = r.ownPts[r.ownPts.length - 2];
            const c = r.ownPts[r.ownPts.length - 1];
            return +Math.hypot(c[0] - a[0], c[1] - a[1]).toFixed(1);
          })();
          return {
            ri,
            segIdx: r.segIdx,
            segTag: s ? s.tag : '?',
            fanRank: +rank.toFixed(2),
            jog: +jog.toFixed(1),
            attackLen,
          };
        });
        out.push({
          bundleIdx: bi,
          groupIdx: gi,
          descentX: +g.descentX.toFixed(1),
          meanRank: +meanRank.toFixed(2),
          members,
        });
      }
    }
  }
  return out;
}

function dumpArrowsDiagnostics(opts) {
  const o = opts || {};
  const sendTerminal = (o.terminal !== false);

  if (!routeCache || !routeCache.length) {
    console.log('[arrows] no routes yet');
    return null;
  }

  const diag = {
    timestamp: new Date().toISOString(),
    geometry: {
      bounds: GEOMETRY.bounds,
      planMidX: (GEOMETRY.bounds.minX + GEOMETRY.bounds.maxX) / 2,
      wallHeight: WALL_HEIGHT,
      canvas: {
        floorH: layout.floorH, dividerY: layout.dividerY,
        wallY: layout.wallY, wallH: layout.wallH,
      },
      viewFloor: { scale: viewFloor.scale,
                   tx: viewFloor.tx, ty: viewFloor.ty },
      viewWall:  { scaleX: viewWall.scaleX, scaleY: viewWall.scaleY,
                   tx: viewWall.tx, ty: viewWall.ty,
                   stripTopY: viewWall.stripTopY,
                   zoom: viewWall.zoom || 1 },
      router: { leftBaseX: router.leftBaseX,
                rightBaseX: router.rightBaseX,
                ESCAPE_DROP: router.ESCAPE_DROP },
      tune: {
        zoom: viewWall.zoom || 1,
        ROUTE_MIN_SEP,
        ROUTE_MIN_OVERLAP,
        ROUTE_TIP_CLEARANCE,
        ROUTE_TIP_QUAD_W,
        ROUTE_LAST_SEG_MIN,
        ROUTE_BUNDLE_TOP_MARGIN,
        ROUTE_BUNDLE_SPACING_MIN,
        SOLVER_ACCEPTABLE_SCORE,
        SOLVER_PARALLEL_BUDGET,
        LONG_ATTACK_THRESHOLD,
        LONG_ATTACK_SPLIT_THRESHOLD,
        SAME_COL_TOL,
        BUNDLE_PERP_FAN_STEP,
      },
    },
    segments: _radSegsTable(),
    routes: routeCache.map((r, i) => _radRouteRow(r, i)),
    sides: window.__arrowsSides || null,
    dedupeDrops: window.__clusterDedupeDrops || 0,
    longSoloBundled: window.__longSoloBundled || 0,
    longAttackSplits: window.__longAttackSplits || 0,
    crossingsBefore: window.__crossingCountStart || 0,
    crossingsAfter:  window.__crossingCountEnd   || 0,
    crossingsPasses: window.__crossingPasses     || 0,
    cacheStats: window.__arrowCacheStats || null,
  };
  window.__arrowsDiag = diag;

  const lines = [];
  const L = (s) => lines.push(s);
  const rule = '══════════════════════════════════════════════════════════════';
  L(rule);
  L(' ARROW FIELD DIAGNOSTIC   ' + diag.timestamp);
  L(rule);
  L('');

  L('GEOMETRY');
  L('  plan bounds (mm)   x [' + GEOMETRY.bounds.minX.toFixed(1)
      + ', ' + GEOMETRY.bounds.maxX.toFixed(1) + ']'
      + '   y [' + GEOMETRY.bounds.minY.toFixed(1)
      + ', ' + GEOMETRY.bounds.maxY.toFixed(1) + ']');
  L('  plan midX (mm)     ' + diag.geometry.planMidX.toFixed(1));
  L('  visible segments   ' + diag.segments.length
      + ' / ' + WALL.segments.length);
  L('  canvas             floor 0..' + layout.floorH
      + '   wall ' + layout.wallY + '..' + (layout.wallY + layout.wallH));
  L('  floor scale        ' + viewFloor.scale.toFixed(4) + ' px/mm'
      + '   tx=' + viewFloor.tx.toFixed(1)
      + '   ty=' + viewFloor.ty.toFixed(1));
  L('  wall  scale        x=' + viewWall.scaleX.toFixed(4)
      + ' y=' + viewWall.scaleY.toFixed(4)
      + '   tx=' + viewWall.tx.toFixed(1)
      + '   ty=' + viewWall.ty.toFixed(1));
  L('');
  L('ZOOM');
  L('  wall zoom factor   ' + (viewWall.zoom || 1).toFixed(3));
  L('  scaled tuning      ROUTE_MIN_SEP=' + ROUTE_MIN_SEP.toFixed(2)
      + '  ROUTE_TIP_CLEARANCE=' + ROUTE_TIP_CLEARANCE.toFixed(2)
      + '  ROUTE_TIP_QUAD_W=' + ROUTE_TIP_QUAD_W.toFixed(3));
  L('                     ROUTE_LAST_SEG_MIN=' + ROUTE_LAST_SEG_MIN.toFixed(2)
      + '  ROUTE_BUNDLE_TOP_MARGIN=' + ROUTE_BUNDLE_TOP_MARGIN.toFixed(2)
      + '  ROUTE_BUNDLE_SPACING_MIN=' + ROUTE_BUNDLE_SPACING_MIN.toFixed(2));
  L('                     SOLVER_ACCEPTABLE_SCORE=' + SOLVER_ACCEPTABLE_SCORE.toFixed(2)
      + '  SOLVER_PARALLEL_BUDGET=' + SOLVER_PARALLEL_BUDGET.toFixed(2));
  L('                     BUNDLE_FAN_STEP=' + BUNDLE_FAN_STEP.toFixed(2)
      + '  BUNDLE_PERP_FAN_STEP=' + BUNDLE_PERP_FAN_STEP.toFixed(2));
  L('  dedupe drops this frame  ' + diag.dedupeDrops);
  L('  long-solo merges         ' + diag.longSoloBundled);
  L('  long-attack splits       ' + diag.longAttackSplits);
  L('  tight-cluster flags     ' + (window.__tightClusterFlags || 0));
  L('  crossings before redux   ' + diag.crossingsBefore);
  L('  crossings after redux    ' + diag.crossingsAfter);
  L('  crossing-reduction passes ' + diag.crossingsPasses);
  L('  solve-cache              '
      + (diag.cacheStats
          ? (diag.cacheStats.reuse       + ' reused / '
             + diag.cacheStats.transformed + ' transformed / '
             + diag.cacheStats.misses    + ' solved / '
             + diag.cacheStats.skips     + ' debounced')
          : '—'));
  L('  tuning                   LONG_ATTACK_THRESHOLD=' + LONG_ATTACK_THRESHOLD
      + '  LONG_ATTACK_SPLIT_THRESHOLD=' + LONG_ATTACK_SPLIT_THRESHOLD
      + '  SAME_COL_TOL=' + SAME_COL_TOL);
  L('');

  L('WALL SEGMENTS (visible, world mm)');
  L('   idx  tag        kind   a                 b                 len    u0..u1              z-band');
  for (const s of diag.segments) {
    L('  '
      + String(s.idx).padStart(4) + '  '
      + String(s.tag).padEnd(10) + ' '
      + String(s.kind).padEnd(5) + '  '
      + '(' + String(s.a[0].toFixed(0)).padStart(5) + ','
      + String(s.a[1].toFixed(0)).padStart(5) + ')  '
      + '(' + String(s.b[0].toFixed(0)).padStart(5) + ','
      + String(s.b[1].toFixed(0)).padStart(5) + ')  '
      + String(s.len.toFixed(0)).padStart(5) + '  '
      + ('[' + s.u0.toFixed(0) + '..' + s.u1.toFixed(0) + ']')
          .padEnd(18) + ' '
      + '[' + s.z_lo.toFixed(0) + '..' + s.z_hi.toFixed(0) + ']');
  }

  if (diag.sides) {
    for (const [name, side] of [['LEFT', diag.sides.left],
                                 ['RIGHT', diag.sides.right]]) {
      if (!side || !side.routes.length) continue;
      const sum = _radSideSummary(side, name);

      L('');
      L('SIDE ' + name + '   (' + sum.routeCount + ' routes)');
      L('  com              x=' + sum.comX.toFixed(1)
          + '   y=' + sum.comY.toFixed(1));
      L('  bundles        ' + sum.bundleCount
          + '   bundleSpacing ' + sum.bundleSpacing
          + '   groupSpacing '  + sum.groupSpacing
          + '   peelStagger '   + sum.peelStagger);
      L('  clusters       ' + sum.clusterCount);

      const ri6 = sum.rerouteInfo || {};
      L('  reroute        candidates=' + (ri6.candidates || 0)
          + '   committed=' + (ri6.committed || 0)
          + '   preScore=' + (ri6.preScore == null ? '—'
                                : ri6.preScore.toFixed(1))
          + '   postScore=' + (ri6.postScore == null ? '—'
                                : ri6.postScore.toFixed(1))
          + '   (cap ' + ROUTE_REROUTE_MAX_PER_SWEEP + '/sweep)');

      const cf = ri6.conflictTable;
      if (cf && cf.length) {
        const rows = cf.slice().sort((a, b) => b.total - a.total);
        const interesting = rows.filter(r => r.total > 0);
        if (interesting.length) {
          L('');
          L('  per-route conflict breakdown (foreign routes only):');
          L('     #  nCross  nParallel  totalCross  totalParallel  total');
          for (const r of interesting) {
            L('    #' + String(r.ri).padStart(3)
                + '  ' + String(r.nCross).padStart(6)
                + '  ' + String(r.nParallel).padStart(9)
                + '  ' + String(r.totalCross.toFixed(1)).padStart(11)
                + '  ' + String(r.totalParallel.toFixed(1)).padStart(13)
                + '  ' + String(r.total.toFixed(1)).padStart(7));
          }
        }
      }

      const ei = sum.escapeInfo || {};
      L('  escape         fans=' + (ei.fans || 0)
          + '   evacuees=' + (ei.evacuees || 0)
          + '   applied=' + (ei.applied ? 'YES' : 'no ')
          + '   preScore=' + (ei.preScore == null ? '—'
                                : ei.preScore.toFixed(1))
          + '   postScore=' + (ei.postScore == null ? '—'
                                : ei.postScore.toFixed(1)));

      L('  score          parallel='   + sum.score.parallel.toFixed(1)
          + '   crossing='   + sum.score.crossing.toFixed(1)
          + '   tipPenalty=' + sum.score.tipPenalty.toFixed(1));
      L('                 attackParallel=' + sum.score.attackParallel.toFixed(1)
          + '   bundlePenalty='  + sum.score.bundlePenalty.toFixed(1)
          + '   groupPenalty='  + sum.score.groupPenalty.toFixed(1)
          + '   clusterPenalty=' + sum.score.clusterPenalty.toFixed(1));
      L('                 TOTAL=' + sum.score.total.toFixed(1));

      const viols = _radTopTipViolations(side, 8);
      if (viols.length) {
        L('');
        L('  top tip violations (tip landing within '
            + ROUTE_TIP_CLEARANCE + ' px of a foreign segment):');
        L('    tip#  tipSeg   ->   hit#  hitSeg   segN  dist   penalty');
        for (const v of viols) {
          L('    #' + String(v.tipRoute).padStart(3) + '  '
              + String(v.tipTag).padEnd(8) + ' -> '
              + '#' + String(v.hitRoute).padStart(3) + '  '
              + String(v.hitTag).padEnd(8) + ' '
              + String(v.hitSegIdx).padStart(4) + '  '
              + String(v.dist).padStart(5) + '  '
              + String(v.penalty).padStart(7));
        }
      }

      L('');
      L('  descent groups (attack-bundle status):');
      for (let bi = 0; bi < side.bundles.length; bi++) {
        const b = side.bundles[bi];
        for (let gi = 0; gi < b.groups.length; gi++) {
          const g = b.groups[gi];
          if (!g.routeIndices.length) continue;
          const dirTxt = (g.attackBundled && g.trunkDir)
            ? ('trunkDir=(' + g.trunkDir[0].toFixed(3) + ','
               + g.trunkDir[1].toFixed(3) + ')'
               + '  angle=' + (Math.atan2(g.trunkDir[1], g.trunkDir[0])
                              * 180 / Math.PI).toFixed(1) + '°')
            : 'trunkDir=—';
          const soloTag = (g.routeIndices.length === 1) ? '  (solo)' : '';
          const satTag  = b.isSatellite ? ' [SAT]' : '';
          const halfTag = b.upperHalf ? ' [U]' : ' [L]';
          L('    b' + bi + '/g' + gi + satTag + halfTag
              + '  routes=' + g.routeIndices.length + soloTag
              + '  descentX=' + g.descentX.toFixed(1)
              + '  attackBundled=' + (g.attackBundled ? 'YES' : 'no ')
              + '  forceUnbundled=' + (g.forceUnbundled ? 'YES' : 'no ')
              + '  ' + dirTxt);
        }
      }

      const tiny = _radTinyBundledClusters(side);
      if (tiny.length) {
        L('');
        L('  tiny bundled attack clusters (Pass 4f detection):');
        for (let ci = 0; ci < tiny.length; ci++) {
          const tc = tiny[ci];
          L('    cluster[' + ci + ']  b' + tc.bundleIdx
              + '/g' + tc.groupIdx
              + '  descentX=' + tc.descentX
              + '  meanRank=' + tc.meanRank
              + '  size=' + tc.members.length);
          for (const m of tc.members) {
            L('      #' + String(m.ri).padStart(3)
                + '  seg=' + String(m.segIdx).padStart(2)
                + ' (' + String(m.segTag).padEnd(10) + ')'
                + '  fanRank=' + String(m.fanRank).padStart(6)
                + '  jog='     + String(m.jog).padStart(6)
                + '  attackLen=' + (m.attackLen === null
                    ? '   —' : String(m.attackLen).padStart(5)));
          }
        }
      }

      const clusters = side.clusters || [];
      if (clusters.length) {
        L('');
        L('  plan-space clusters:');
        for (let ci = 0; ci < clusters.length; ci++) {
          const cluster = clusters[ci];
          const flaggedCount = cluster.filter(
            ri => side.routes[ri].clusterAttack).length;
          const cy = cluster
            .map(ri => side.routes[ri].clusterY)
            .find(v => typeof v === 'number');
          L('    cluster[' + ci + ']  size=' + cluster.length
              + '  flagged=' + flaggedCount + '/' + cluster.length
              + '  clusterY=' + (typeof cy === 'number'
                  ? cy.toFixed(1) : '—'));
          for (const ri of cluster) {
            const r = side.routes[ri];
            const s = WALL.segments[r.segIdx];
            const wxMid = s ? (s.a[0] + s.b[0]) / 2 : 0;
            const wyMid = s ? (s.a[1] + s.b[1]) / 2 : 0;
            const attackLen = (() => {
              if (!r.ownPts || r.ownPts.length < 2) return null;
              const a = r.ownPts[r.ownPts.length - 2];
              const b = r.ownPts[r.ownPts.length - 1];
              return +Math.hypot(b[0] - a[0], b[1] - a[1]).toFixed(1);
            })();
            const rankStr = (typeof r.clusterRank === "number")
              ? String(r.clusterRank) : '—';
            const qStr = (typeof r.quadrant === "number")
              ? String(r.quadrant) : '—';
            const hStr = r.upperHalf ? 'U' : 'L';
            L('      #' + String(ri).padStart(3)
                + '  seg=' + String(r.segIdx).padStart(2)
                + ' (' + (s ? s.tag : '?') + ')'
                + '  segLen=' + String(s ? s.len.toFixed(0) : '?').padStart(5) + 'mm'
                + '  world=(' + wxMid.toFixed(0).padStart(5)
                + ',' + wyMid.toFixed(0).padStart(5) + ')'
                + '  b/g=' + r.bundleIdx + '/' + r.groupIdx
                + '  rank=' + rankStr
                + '  q=' + qStr + ' h=' + hStr
                + '  attackLen=' + (attackLen === null
                    ? '   —' : attackLen.toFixed(0).padStart(4))
                + '  flagged=' + (r.clusterAttack ? 'YES' : 'no '));
          }
        }
      }
    }
  }

  L('');
  L('ROUTES');
  L('     #  seg  tag      kind  worldMid          side  q  h  stripX  planX  planY   b/g  gs  rank  bundleY   brkY   fanR   jog   descentX   cl  cAtt   cY     cDX   cRk  cSz   bnd  unb  esc  sv   attLen  attAng   topo  pts');
  for (const r of diag.routes) {
    L('  '
      + String(r.idx).padStart(4) + '  '
      + String(r.segIdx).padStart(3) + '  '
      + String(r.segTag).padEnd(8) + ' '
      + String(r.segKind).padEnd(5) + ' '
      + '(' + String(r.worldMid[0]).padStart(5) + ','
      + String(r.worldMid[1]).padStart(5) + ')  '
      + String(r.side).padEnd(4) + '  '
      + String(r.quadrant).padStart(1) + '  '
      + (r.upperHalf ? 'U' : 'L') + '  '
      + String(r.stripX).padStart(6) + '  '
      + String(r.planX).padStart(5) + '  '
      + String(r.planY).padStart(5) + '   '
      + (r.bundleIdx + '/' + r.groupIdx).padStart(4) + '  '
      + String(r.groupSize).padStart(2) + '  '
      + String(r.peelRank).padStart(4) + '  '
      + String(r.bundleY).padStart(7) + '  '
      + (r.baseBreak === null
            ? '     — ' : String(r.baseBreak).padStart(7)) + '  '
      + (r.bundleFanRank === null
            ? '    —  ' : String(r.bundleFanRank).padStart(6)) + '  '
      + (r.bundleJogOffset === null
            ? '    —  ' : String(r.bundleJogOffset).padStart(6)) + '  '
      + String(r.descentX).padStart(8) + '  '
      + String(r.clusterId).padStart(3) + '  '
      + (r.clusterAttack ? ' Y  ' : ' .  ') + '  '
      + (r.clusterY === null
            ? '    —  ' : String(r.clusterY).padStart(6)) + '  '
      + (r.clusterDropX === null
            ? '    —  ' : String(r.clusterDropX).padStart(6)) + '  '
      + (r.clusterRank === null
            ? '   —' : String(r.clusterRank).padStart(4)) + '  '
      + (r.clusterSize === null
            ? '   —' : String(r.clusterSize).padStart(4)) + '  '
      + (r.groupAttackBundled ? ' Y  ' : ' .  ') + '  '
      + (r.groupForceUnbundled ? ' Y  ' : ' .  ') + '  '
      + (r.escaped  ? ' Y  ' : ' .  ') + '  '
      + (r.survivor ? ' Y  ' : ' .  ') + '  '
      + (r.attackLen === null
            ? '    — ' : String(r.attackLen).padStart(6)) + '  '
      + (r.attackAngleDeg === null
            ? '     —  ' : String(r.attackAngleDeg).padStart(7)) + '  '
      + String(r.tag).padEnd(4) + '  '
      + (r.pts ? JSON.stringify(r.pts) : '—'));
  }

  L('');
  L('Tip: window.__arrowsDiag holds the structured dump.');
  L('     window.__arrowsSides holds the live side handles.');
  L('     Re-dump any time:  dumpArrowsDiagnostics()  or  Ctrl+Shift+D');
  L('     Console-only dump: dumpArrowsDiagnostics({ terminal: false })');
  L('     Force a re-solve:  window.__arrowForceResolve()');
  L('     Topo codes: L0..L3 = orthogonal, D = diagonal, B = bundled attack,');
  L('                 C = cluster attack, R = rerouted detour.');
  L('     Half: U = upper (planY < comY), L = lower (planY >= comY).');
  L('     Quadrants (screen coords, y-down):');
  L('       0 = NW  x < comX, y < comY');
  L('       1 = NE  x >= comX, y < comY');
  L('       2 = SW  x < comX, y >= comY');
  L('       3 = SE  x >= comX, y >= comY');
  L('');

  for (const ln of lines) console.log(ln);
  if (sendTerminal) {
    try {
      fetch('/diag', {
        method: 'POST',
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        body: lines.join('\n'),
      }).catch(() => {});
    } catch (e) { /* file:// — no fetch, no problem */ }
  }

  return diag;
}

window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey
      && (e.key === 'd' || e.key === 'D')) {
    e.preventDefault();
    dumpArrowsDiagnostics();
  }
});


/* ==========================================================================
   SECTION 13 — DIAGNOSTICS BUTTON
   ========================================================================== */

(function _radInstallDiagButton() {
  const ui = document.getElementById("ui");
  if (!ui) return;
  const hr = ui.querySelector("hr");
  const row = document.createElement("div");
  row.className = "row";
  const btn = document.createElement("button");
  btn.id    = "arrowDiagBtn";
  btn.title = T("tipDiagnostics");
  btn.textContent = T("btnDiagnostics");
  row.appendChild(btn);
  if (hr) hr.parentNode.insertBefore(row, hr);
  else    ui.appendChild(row);

  btn.addEventListener("click", () => dumpArrowsDiagnostics());
})();


/* ==========================================================================
   SECTION 14 — ARROW VISIBILITY TOGGLE
   ========================================================================== */

const _raLastPointer = { x: 0, y: 0 };
window.addEventListener("mousemove", (e) => {
  _raLastPointer.x = e.clientX;
  _raLastPointer.y = e.clientY;
}, { passive: true });

function _raRequestRedraw() {
  const names = [
    "requestRender", "scheduleRender", "invalidate", "redraw",
    "renderAll", "scheduleDraw", "requestDraw", "paint",
  ];
  for (const n of names) {
    if (typeof window[n] === "function") {
      try { window[n](); } catch (e) { }
      return;
    }
  }

  const target =
    (typeof ctx !== "undefined" && ctx && ctx.canvas) ? ctx.canvas :
    (document.body || window);

  target.dispatchEvent(new MouseEvent("mousemove", {
    clientX: _raLastPointer.x,
    clientY: _raLastPointer.y,
    bubbles: true,
    cancelable: true,
    view: window,
  }));
}

(function _radInstallArrowToggle() {
  const ui = document.getElementById("ui");
  if (!ui) return;

  const hr = ui.querySelector("hr");
  const row = document.createElement("div");
  row.className = "row";

  const label = document.createElement("label");
  label.style.display    = "flex";
  label.style.alignItems = "center";
  label.style.gap        = "6px";
  label.style.cursor     = "pointer";
  label.style.userSelect = "none";

  const cb = document.createElement("input");
  cb.type    = "checkbox";
  cb.id      = "arrowVisibilityToggle";
  cb.checked = _raArrowsEnabled;
  cb.title   = T("arrowToggleTitle");

  const txt = document.createElement("span");
  txt.textContent = T("arrowToggleLabel");

  label.appendChild(cb);
  label.appendChild(txt);
  row.appendChild(label);

  if (hr) hr.parentNode.insertBefore(row, hr);
  else    ui.appendChild(row);

  cb.addEventListener("change", () => {
    _raArrowsEnabled = cb.checked;

    if (_raArrowsEnabled) {
      if (_raTransCache.routes) routeCache = _raTransCache.routes;
    } else {
      routeCache = [];
    }

    if (typeof state !== "undefined") state.hoveredRoute = null;
    _raRequestRedraw();
  });
})();

window.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey
      && (e.key === "a" || e.key === "A")) {
    e.preventDefault();
    _raArrowsEnabled = !_raArrowsEnabled;
    const cb = document.getElementById("arrowVisibilityToggle");
    if (cb) cb.checked = _raArrowsEnabled;
    if (typeof state !== "undefined") state.hoveredRoute = null;
    _raRequestRedraw();
  }
});
"""
