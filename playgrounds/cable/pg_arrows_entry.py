"""
pg_arrows_entry.py — the solve cache, the topology signature, the two
affine transforms that keep the cache valid across viewport changes,
and `drawWallToPlanArrows` — the single public entry point.

ES module.  Two named exports:

    drawWallToPlanArrows   the arrow-field entry point
    arrowsEntryState       a const object wrapping the one mutable
                           flag the diagnostics module flips

CACHE MODEL
===========
`_raTransCache` holds the last solve's route list plus the viewport
transform at which it was solved.  On every call, the topology
signature is compared against the cached one; if it matches, the
cache is valid and the only question is whether either view has
moved.  Two affine transforms keep the cache valid under viewport
changes:

    _raApplyFloorAffine   floor pan / zoom — writes planX, planY,
                          descentX, bundleY, ownPts

    _raApplyWallAffine    wall pan / zoom — writes stripX, stripBaseY,
                          stripRouteY, corridorX

The two write disjoint field sets and compose cleanly when both
views move in the same frame.

The solve path is re-entered only when:
    · the topology signature has changed, or
    · the cache is empty or the debounce window has elapsed.

`arrowsEntryState.enabled` is a const-object field rather than a bare
`let` because the diagnostics module flips it from another file, and
an ES-module `let` export is read-only from the importer's side.
"""

ENTRY_JS = r"""
import {
  WALL, GEOMETRY, layout, viewFloor, viewWall, router,
  state,
} from "./pg_state.js";
import { isSegHidden } from "./pg_core.js";
import {
  RA_SOLVE_SIGNATURE_PRECISION, RA_SOLVE_DEBOUNCE_MS,
  _raSetZoom,
} from "./pg_arrows_tuning.js";
import { _raSolve } from "./pg_arrows_solver.js";
import { _raBuildSides } from "./pg_arrows_state.js";
import {
  _raPrepareRoutes, _raDrawAllRoutes,
} from "./pg_arrows_render.js";

/* ==========================================================================
   MODULE-SCOPED MUTABLE FLAG
   ========================================================================== */

export const arrowsEntryState = {
  enabled: true,
};

/* ==========================================================================
   CACHE STRUCTURE
   ========================================================================== */

const _raTransCache = {
  topoSig:     null,
  floorScale:  1,
  floorTx:     0,
  floorTy:     0,
  wallScaleX:  1,
  wallScaleY:  1,
  wallTx:      0,
  wallTy:      0,
  routes:      null,
  lastSolveMs: 0,
};

function _raQuantize(v, prec) {
  return Math.round(v / prec) * prec;
}

/* ==========================================================================
   TOPOLOGY SIGNATURE
   ==========================================================================
   The identity of a solve.  Changes only when the SOLVE would produce
   a different result.  Every view-transform field is deliberately
   absent — those are absorbed by the affine transforms below. */

function _raTopologySig() {
  const p = RA_SOLVE_SIGNATURE_PRECISION;
  const parts = [];

  parts.push("vp" + window.innerWidth + "x" + window.innerHeight);
  parts.push("dpr" + (window.devicePixelRatio || 1).toFixed(2));

  const b = GEOMETRY.bounds;
  parts.push("gb"
    + _raQuantize(b.minX, p) + ","
    + _raQuantize(b.maxX, p) + ","
    + _raQuantize(b.minY, p) + ","
    + _raQuantize(b.maxY, p));

  parts.push("lo" + layout.dividerY
                  + "," + layout.wallY
                  + "," + layout.wallH
                  + "," + layout.floorH);

  parts.push("r" + router.ESCAPE_DROP);

  parts.push("ws" + WALL.segments.length);
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    parts.push(
      (isSegHidden(i) ? "h" : "v")
      + _raQuantize(s.a[0], p) + "," + _raQuantize(s.a[1], p)
      + _raQuantize(s.b[0], p) + "," + _raQuantize(s.b[1], p)
      + _raQuantize(s.u0, 0.1) + "," + _raQuantize(s.u1, 0.1));
  }

  return parts.join("|");
}

/* ==========================================================================
   AFFINE TRANSFORMS
   ========================================================================== */

function _raApplyFloorAffine(routes, s0, tx0, ty0, s1, tx1, ty1) {
  if (s0 <= 0) s0 = s1;
  const k  = s1 / s0;
  const dx = tx1 - tx0 * k;
  const dy = ty1 - ty0 * k;

  for (const r of routes) {
    r.planX    = r.planX    * k + dx;
    r.planY    = r.planY    * k + dy;
    r.descentX = r.descentX * k + dx;
    r.bundleY  = r.bundleY  * k + dy;
    if (r.ownPts) {
      for (const pt of r.ownPts) {
        pt[0] = pt[0] * k + dx;
        pt[1] = pt[1] * k + dy;
      }
    }
  }
}

function _raApplyWallAffine(routes,
                            s0x, s0y, tx0, ty0,
                            s1x, s1y, tx1, ty1) {
  if (s0x <= 0) s0x = s1x;
  const kx = s1x / s0x;
  const cx = tx1 - tx0 * kx;
  const dy = ty1 - ty0;

  const lb = router.leftBaseX;
  const rb = router.rightBaseX;

  for (const r of routes) {
    r.stripX      = r.stripX      * kx + cx;
    r.stripBaseY  = r.stripBaseY  + dy;
    r.stripRouteY = r.stripRouteY + dy;
    r.corridorX   = r.useLeft ? lb : rb;
  }
  void s0y; void s1y;
}

/* ==========================================================================
   FORCE-RESOLVE (diagnostics hook)
   ========================================================================== */

window.__arrowForceResolve = function () {
  _raTransCache.topoSig     = null;
  _raTransCache.routes      = null;
  _raTransCache.lastSolveMs = 0;
};

/* ==========================================================================
   THE ENTRY POINT
   ========================================================================== */

export function drawWallToPlanArrows() {
  if (!WALL.segments.length) {
    state.routeCache = [];
    return;
  }

  window.__arrowCacheStats = window.__arrowCacheStats
    || { reuse: 0, transformed: 0, misses: 0, skips: 0 };

  const topoSig = _raTopologySig();
  const fs   = viewFloor.scale;
  const ftx  = viewFloor.tx;
  const fty  = viewFloor.ty;
  const wsx  = viewWall.scaleX;
  const wsy  = viewWall.scaleY;
  const wtx  = viewWall.tx;
  const wty  = viewWall.ty;
  const now  = (typeof performance !== "undefined")
               ? performance.now() : Date.now();

  if (topoSig === _raTransCache.topoSig && _raTransCache.routes) {
    const floorMoved =
      fs  !== _raTransCache.floorScale ||
      ftx !== _raTransCache.floorTx    ||
      fty !== _raTransCache.floorTy;

    const wallMoved =
      wsx !== _raTransCache.wallScaleX ||
      wsy !== _raTransCache.wallScaleY ||
      wtx !== _raTransCache.wallTx     ||
      wty !== _raTransCache.wallTy;

    if (!floorMoved && !wallMoved) {
      window.__arrowCacheStats.reuse++;
      if (arrowsEntryState.enabled) {
        state.routeCache = _raTransCache.routes;
        _raDrawAllRoutes();
      }
      return;
    }

    if (floorMoved) {
      _raApplyFloorAffine(
        _raTransCache.routes,
        _raTransCache.floorScale,
        _raTransCache.floorTx,
        _raTransCache.floorTy,
        fs, ftx, fty);
      _raTransCache.floorScale = fs;
      _raTransCache.floorTx    = ftx;
      _raTransCache.floorTy    = fty;
    }

    if (wallMoved) {
      _raApplyWallAffine(
        _raTransCache.routes,
        _raTransCache.wallScaleX,
        _raTransCache.wallScaleY,
        _raTransCache.wallTx,
        _raTransCache.wallTy,
        wsx, wsy, wtx, wty);
      _raTransCache.wallScaleX = wsx;
      _raTransCache.wallScaleY = wsy;
      _raTransCache.wallTx     = wtx;
      _raTransCache.wallTy     = wty;
    }

    window.__arrowCacheStats.transformed++;
    if (arrowsEntryState.enabled) {
      state.routeCache = _raTransCache.routes;
      _raDrawAllRoutes();
    }
    return;
  }

  if (_raTransCache.routes
      && (now - _raTransCache.lastSolveMs) < RA_SOLVE_DEBOUNCE_MS) {
    window.__arrowCacheStats.skips++;
    if (arrowsEntryState.enabled) {
      state.routeCache = _raTransCache.routes;
      _raDrawAllRoutes();
    }
    return;
  }

  if (!arrowsEntryState.enabled) return;

  window.__arrowCacheStats.misses++;
  state.routeCache = [];

  _raSetZoom(1);

  const allRoutes = _raPrepareRoutes();
  const sides = _raBuildSides(allRoutes);

  _raSolve(sides.left);
  _raSolve(sides.right);

  for (const r of allRoutes) state.routeCache.push(r);

  _raTransCache.topoSig     = topoSig;
  _raTransCache.floorScale  = fs;
  _raTransCache.floorTx     = ftx;
  _raTransCache.floorTy     = fty;
  _raTransCache.wallScaleX  = wsx;
  _raTransCache.wallScaleY  = wsy;
  _raTransCache.wallTx      = wtx;
  _raTransCache.wallTy      = wty;
  _raTransCache.routes      = state.routeCache;
  _raTransCache.lastSolveMs = now;

  _raDrawAllRoutes();
}
"""
