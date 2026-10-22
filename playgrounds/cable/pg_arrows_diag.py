"""
pg_arrows_diag.py — the structured diagnostic dump, the diagnostic
button, and the arrow-visibility toggle.

ES module.  Two exports the boot module calls:

    installArrowDiagButton    attach the click handler to the baked
                              `#arrowDiagBtn` element (baked in
                              `pg_base.PG_PANEL_BODY`)

    installArrowToggle        initialise the baked `#arrowVisibilityToggle`
                              checkbox and attach its change handler

And three exports the same buttons hook into:

    dumpArrowsDiagnostics     the diagnostic dump — also on
                              Ctrl+Shift+D
    toggleArrowVisibility     flips arrowsEntryState.enabled
    setArrowVisibility        idempotent setter

WIRE FORMAT — /diag
===================
The boot module passes `/diag` in `kit_serve(routes=…)`, so
`pgkit.server.serve()` parses the body as JSON before handing it to
`_diag_route`.  The POST sends `{ text: "..." }` as
application/json.

(The body of the diagnostic dump itself is unchanged from the
concatenation-era version; see `_radSegsTable`, `_radRouteRow`,
`_radSideSummary`, `_radTopTipViolations`, `_radTinyBundledClusters`
and `dumpArrowsDiagnostics` below.)
"""

DIAG_JS = r"""
import {
  WALL, GEOMETRY, layout, viewFloor, viewWall, router,
  state,
  callDraw,
  pointToSegmentDist,
} from "./pg_state.js";
import { isSegHidden } from "./pg_core.js";
import { T } from "./pg_i18n.js";
import {
  ROUTE_MIN_SEP, ROUTE_MIN_OVERLAP,
  ROUTE_TIP_CLEARANCE, ROUTE_TIP_QUAD_W,
  ROUTE_LAST_SEG_MIN,
  ROUTE_BUNDLE_TOP_MARGIN, ROUTE_BUNDLE_SPACING_MIN,
  SOLVER_ACCEPTABLE_SCORE, SOLVER_PARALLEL_BUDGET,
  LONG_ATTACK_THRESHOLD, LONG_ATTACK_SPLIT_THRESHOLD,
  SAME_COL_TOL,
  BUNDLE_FAN_STEP, BUNDLE_PERP_FAN_STEP,
  ROUTE_REROUTE_MAX_PER_SWEEP,
} from "./pg_arrows_tuning.js";
import {
  _raScoreSide,
} from "./pg_arrows_scoring.js";
import {
  _raPathSegments,
} from "./pg_arrows_geometry.js";
import {
  _raFindTinyBundledClusters,
} from "./pg_arrows_layout.js";
import {
  arrowsEntryState,
} from "./pg_arrows_entry.js";

/* ==========================================================================
   DUMP HELPERS
   ==========================================================================
   (unchanged; see concatenation-era source for the implementations) */

function _radSegsTable() {
  const out = [];
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    out.push({
      idx: i,
      tag: s.tag || ("seg" + i),
      kind: s.kind || "wall",
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

  let groupAttackBundled  = false;
  let groupForceUnbundled = false;
  let groupTrunkDir = null;
  let groupSize = 0;
  let bundleIsSatellite = false;
  let bundleUpperHalf   = true;
  const sides = window.__arrowsSides;
  if (sides) {
    const side = r.useLeft ? sides.left : sides.right;
    if (side) {
      const b = side.bundles[r.bundleIdx];
      const g = b ? b.groups[r.groupIdx] : null;
      if (b) {
        bundleIsSatellite = !!b.isSatellite;
        bundleUpperHalf   = !!b.upperHalf;
      }
      if (g) {
        groupAttackBundled  = !!g.attackBundled;
        groupForceUnbundled = !!g.forceUnbundled;
        groupTrunkDir       = g.trunkDir || null;
        groupSize           = g.routeIndices.length;
      }
    }
  }

  return {
    idx,
    segIdx: r.segIdx,
    segTag:  s ? s.tag : "?",
    segKind: s ? (s.kind || "wall") : "?",
    segLen:  s ? +s.len.toFixed(1) : null,
    worldMid: (wxMid !== null)
      ? [+wxMid.toFixed(0), +wyMid.toFixed(0)] : null,

    side: r.useLeft ? "L" : "R",
    quadrant: r.quadrant,
    upperHalf: !!r.upperHalf,
    stripX: +r.stripX.toFixed(1),
    planX:  +r.planX.toFixed(1),
    planY:  +r.planY.toFixed(1),

    bundleIdx: r.bundleIdx,
    groupIdx:  r.groupIdx,
    groupSize,
    bundleIsSatellite,
    bundleUpperHalf,
    peelRank: r.peelRank,
    bundleY:  +r.bundleY.toFixed(1),
    baseBreak: (typeof r.baseBreak === "number")
      ? +r.baseBreak.toFixed(1) : null,
    bundleFanRank: (typeof r.bundleFanRank === "number")
      ? +r.bundleFanRank.toFixed(2) : null,
    bundleJogOffset: (typeof r.bundleJogOffset === "number")
      ? +r.bundleJogOffset.toFixed(1) : 0,
    descentX: +r.descentX.toFixed(1),

    clusterId: r.clusterId,
    clusterAttack: !!r.clusterAttack,
    clusterY:     (typeof r.clusterY     === "number") ? +r.clusterY.toFixed(1) : null,
    clusterDropX: (typeof r.clusterDropX === "number") ? +r.clusterDropX.toFixed(1) : null,
    clusterRank:  (typeof r.clusterRank  === "number") ? r.clusterRank  : null,
    clusterSize:  (typeof r.clusterSize  === "number") ? r.clusterSize  : null,

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
    groupSpacing:  side.groupSpacing,
    peelStagger:   side.peelStagger,
    clusterCount:  (side.clusters || []).length,
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
          const pen = t * ROUTE_TIP_CLEARANCE + t * t * ROUTE_TIP_QUAD_W;
          out.push({
            tipRoute:  i,
            tipSeg:    a.segIdx,
            tipTag:    (WALL.segments[a.segIdx] || {}).tag || "?",
            hitRoute:  j,
            hitSeg:    b.segIdx,
            hitTag:    (WALL.segments[b.segIdx] || {}).tag || "?",
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
          const jog = (rank - meanRank) * BUNDLE_FAN_STEP;
          const attackLen = (() => {
            if (!r.ownPts || r.ownPts.length < 2) return null;
            const a = r.ownPts[r.ownPts.length - 2];
            const c = r.ownPts[r.ownPts.length - 1];
            return +Math.hypot(c[0] - a[0], c[1] - a[1]).toFixed(1);
          })();
          return {
            ri,
            segIdx: r.segIdx,
            segTag: s ? s.tag : "?",
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

/* ==========================================================================
   DUMP
   ==========================================================================
   (unchanged from concatenation-era; see below for the JSON
   envelope on /diag and the reveal-button wiring.) */

export function dumpArrowsDiagnostics(opts) {
  const o = opts || {};
  const sendTerminal = (o.terminal !== false);

  if (!state.routeCache || !state.routeCache.length) {
    console.log("[arrows] no routes yet");
    return null;
  }

  const diag = {
    timestamp: new Date().toISOString(),
    geometry: {
      bounds: GEOMETRY.bounds,
      planMidX: (GEOMETRY.bounds.minX + GEOMETRY.bounds.maxX) / 2,
      wallHeight: WALL.height,
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
    routes: state.routeCache.map((r, i) => _radRouteRow(r, i)),
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
  const rule = "══════════════════════════════════════════════════════════════";
  L(rule);
  L(" ARROW FIELD DIAGNOSTIC   " + diag.timestamp);
  L(rule);
  L("");

  L("GEOMETRY");
  L("  plan bounds (mm)   x [" + GEOMETRY.bounds.minX.toFixed(1)
      + ", " + GEOMETRY.bounds.maxX.toFixed(1) + "]"
      + "   y [" + GEOMETRY.bounds.minY.toFixed(1)
      + ", " + GEOMETRY.bounds.maxY.toFixed(1) + "]");
  L("  plan midX (mm)     " + diag.geometry.planMidX.toFixed(1));
  L("  visible segments   " + diag.segments.length
      + " / " + WALL.segments.length);
  L("  canvas             floor 0.." + layout.floorH
      + "   wall " + layout.wallY + ".." + (layout.wallY + layout.wallH));
  L("  floor scale        " + viewFloor.scale.toFixed(4) + " px/mm"
      + "   tx=" + viewFloor.tx.toFixed(1)
      + "   ty=" + viewFloor.ty.toFixed(1));
  L("  wall  scale        x=" + viewWall.scaleX.toFixed(4)
      + " y=" + viewWall.scaleY.toFixed(4)
      + "   tx=" + viewWall.tx.toFixed(1)
      + "   ty=" + viewWall.ty.toFixed(1));
  L("");
  L("ZOOM");
  L("  wall zoom factor   " + (viewWall.zoom || 1).toFixed(3));
  L("  scaled tuning      ROUTE_MIN_SEP=" + ROUTE_MIN_SEP.toFixed(2)
      + "  ROUTE_TIP_CLEARANCE=" + ROUTE_TIP_CLEARANCE.toFixed(2));
  L("  dedupe drops this frame  " + diag.dedupeDrops);
  L("  long-solo merges         " + diag.longSoloBundled);
  L("  long-attack splits       " + diag.longAttackSplits);
  L("  tight-cluster flags     " + (window.__tightClusterFlags || 0));
  L("  crossings before redux   " + diag.crossingsBefore);
  L("  crossings after redux    " + diag.crossingsAfter);
  L("  crossing-reduction passes " + diag.crossingsPasses);
  L("  solve-cache              "
      + (diag.cacheStats
          ? (diag.cacheStats.reuse       + " reused / "
             + diag.cacheStats.transformed + " transformed / "
             + diag.cacheStats.misses    + " solved / "
             + diag.cacheStats.skips     + " debounced")
          : "—"));
  L("");

  L("WALL SEGMENTS (visible, world mm)");
  L("   idx  tag        kind   a                 b                 len    u0..u1              z-band");
  for (const s of diag.segments) {
    L("  "
      + String(s.idx).padStart(4) + "  "
      + String(s.tag).padEnd(10) + " "
      + String(s.kind).padEnd(5) + "  "
      + "(" + String(s.a[0].toFixed(0)).padStart(5) + ","
      + String(s.a[1].toFixed(0)).padStart(5) + ")  "
      + "(" + String(s.b[0].toFixed(0)).padStart(5) + ","
      + String(s.b[1].toFixed(0)).padStart(5) + ")  "
      + String(s.len.toFixed(0)).padStart(5) + "  "
      + ("[" + s.u0.toFixed(0) + ".." + s.u1.toFixed(0) + "]")
          .padEnd(18) + " "
      + "[" + s.z_lo.toFixed(0) + ".." + s.z_hi.toFixed(0) + "]");
  }

  if (diag.sides) {
    for (const [name, side] of [["LEFT", diag.sides.left],
                                 ["RIGHT", diag.sides.right]]) {
      if (!side || !side.routes.length) continue;
      const sum = _radSideSummary(side, name);

      L("");
      L("SIDE " + name + "   (" + sum.routeCount + " routes)");
      L("  com              x=" + sum.comX.toFixed(1)
          + "   y=" + sum.comY.toFixed(1));
      L("  bundles        " + sum.bundleCount
          + "   bundleSpacing " + sum.bundleSpacing
          + "   groupSpacing "  + sum.groupSpacing
          + "   peelStagger "   + sum.peelStagger);
      L("  clusters       " + sum.clusterCount);

      const ri6 = sum.rerouteInfo || {};
      L("  reroute        candidates=" + (ri6.candidates || 0)
          + "   committed=" + (ri6.committed || 0)
          + "   (cap " + ROUTE_REROUTE_MAX_PER_SWEEP + "/sweep)");

      const ei = sum.escapeInfo || {};
      L("  escape         fans=" + (ei.fans || 0)
          + "   evacuees=" + (ei.evacuees || 0)
          + "   applied=" + (ei.applied ? "YES" : "no "));

      L("  score          parallel="   + sum.score.parallel.toFixed(1)
          + "   crossing="   + sum.score.crossing.toFixed(1)
          + "   tipPenalty=" + sum.score.tipPenalty.toFixed(1));
      L("                 TOTAL=" + sum.score.total.toFixed(1));

      const viols = _radTopTipViolations(side, 8);
      if (viols.length) {
        L("");
        L("  top tip violations:");
        for (const v of viols) {
          L("    #" + String(v.tipRoute).padStart(3) + "  "
              + String(v.tipTag).padEnd(8) + " -> "
              + "#" + String(v.hitRoute).padStart(3) + "  "
              + String(v.hitTag).padEnd(8) + " "
              + String(v.hitSegIdx).padStart(4) + "  "
              + String(v.dist).padStart(5) + "  "
              + String(v.penalty).padStart(7));
        }
      }

      const tiny = _radTinyBundledClusters(side);
      if (tiny.length) {
        L("");
        L("  tiny bundled attack clusters:");
        for (let ci = 0; ci < tiny.length; ci++) {
          const tc = tiny[ci];
          L("    cluster[" + ci + "]  b" + tc.bundleIdx
              + "/g" + tc.groupIdx
              + "  descentX=" + tc.descentX
              + "  meanRank=" + tc.meanRank
              + "  size=" + tc.members.length);
          for (const m of tc.members) {
            L("      #" + String(m.ri).padStart(3)
                + "  seg=" + String(m.segIdx).padStart(2)
                + " (" + String(m.segTag).padEnd(10) + ")"
                + "  fanRank=" + String(m.fanRank).padStart(6)
                + "  jog="     + String(m.jog).padStart(6)
                + "  attackLen=" + (m.attackLen === null
                    ? "   —" : String(m.attackLen).padStart(5)));
          }
        }
      }
    }
  }

  L("");
  L("ROUTES");
  for (const r of diag.routes) {
    L("  #" + String(r.idx).padStart(4)
      + "  seg=" + String(r.segIdx).padStart(3)
      + " (" + String(r.segTag).padEnd(8) + ")"
      + "  side=" + r.side
      + "  q=" + String(r.quadrant).padStart(1)
      + (r.upperHalf ? " U" : " L")
      + "  b/g=" + r.bundleIdx + "/" + r.groupIdx
      + "  planX=" + r.planX
      + "  planY=" + r.planY
      + "  bundleY=" + r.bundleY
      + "  descentX=" + r.descentX
      + "  tag=" + r.tag
      + (r.attackLen === null ? "" : "  attLen=" + r.attackLen));
  }

  L("");
  L("Tip: window.__arrowsDiag holds the structured dump.");
  L("     Ctrl+Shift+D to dump.  Ctrl+Shift+A to toggle arrows.");
  L("     Topo codes: L0..L3 = orthogonal, D = diagonal, B = bundled");
  L("                 attack, C = cluster attack, R = rerouted detour.");
  L("");

  for (const ln of lines) console.log(ln);

  if (sendTerminal) {
    try {
      fetch("/diag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: lines.join("\n") }),
      }).catch(() => {});
    } catch (e) { /* file:// — no fetch, no problem */ }
  }

  return diag;
}

/* ==========================================================================
   KEYBOARD SHORTCUTS
   ========================================================================== */

window.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.shiftKey
      && (e.key === "d" || e.key === "D")) {
    e.preventDefault();
    dumpArrowsDiagnostics();
  }
  if ((e.ctrlKey || e.metaKey) && e.shiftKey
      && (e.key === "a" || e.key === "A")) {
    e.preventDefault();
    toggleArrowVisibility();
  }
});

/* ==========================================================================
   VISIBILITY TOGGLE
   ========================================================================== */

export function setArrowVisibility(enabled) {
  arrowsEntryState.enabled = !!enabled;
  if (!arrowsEntryState.enabled) {
    state.routeCache = [];
  }
  state.hoveredRoute = null;
  callDraw();
}

export function toggleArrowVisibility() {
  setArrowVisibility(!arrowsEntryState.enabled);
}

/* ==========================================================================
   BUTTON INSTALLERS — attach-only
   ==========================================================================
   Both buttons are baked in pg_base.PG_PANEL_BODY.  These functions
   attach handlers; they do not create elements. */

export function installArrowDiagButton() {
  const btn = document.getElementById("arrowDiagBtn");
  if (!btn) return;
  btn.addEventListener("click", () => dumpArrowsDiagnostics());
}

export function installArrowToggle() {
  const cb = document.getElementById("arrowVisibilityToggle");
  if (!cb) return;
  cb.checked = arrowsEntryState.enabled;
  cb.addEventListener("change", () => {
    setArrowVisibility(!!cb.checked);
  });
}
"""
