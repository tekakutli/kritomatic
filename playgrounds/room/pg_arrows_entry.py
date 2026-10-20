"""
pg_arrows_entry.py — the solve cache, the topology signature, the two
affine transforms that keep the cache valid across viewport changes,
and drawWallToPlanArrows — the single public entry point.

Cache model
-----------
_raTransCache holds the last solve's route list plus the viewport
transform at which it was solved.  On every call, the topology
signature is compared against the cached one; if it matches, the
cache is valid and the only question is whether either view has moved.

Two affine transforms keep the cache valid under viewport changes:

    _raApplyFloorAffine   floor pan / zoom — writes planX, planY,
                          descentX, bundleY, ownPts.  Handles both a
                          pure translation and a scale-about-a-
                          shifted-origin.

    _raApplyWallAffine    wall pan / zoom — writes stripX, stripBaseY,
                          stripRouteY, corridorX.  Handles both the
                          stripX scale and the pure translation of the
                          two row-space fields; corridorX is re-read
                          from router because it is a fixed screen
                          offset from the strip's edge, not a fixed
                          strip-mm point.

The two transforms write disjoint field sets and compose cleanly when
both views move in the same frame.

The solve path is re-entered only when:
    • the topology signature has changed (wall-segment table, bounds,
      layout bands, viewport size, ESCAPE_DROP), or
    • the cache is empty or the debounce window has elapsed.

The tuning constants are held at their base values via _raSetZoom(1)
at solve time.  Coupling them to the wall zoom was what forced a full
re-solve on every wheel tick of the wall view in an earlier revision;
with the affine transform in place, they must not vary with either
view.
"""


ENTRY_JS = r"""
/* ==========================================================================
   SECTION 10 — PUBLIC ENTRY POINT
   ========================================================================== */

let _raArrowsEnabled = true;

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

/* Topology signature — the identity of a solve.

   The signature must change only when the SOLVE would produce a
   different result.  The solver reads only floor-space fields and the
   tuning constants; the four strip-space fields (stripX, stripBaseY,
   stripRouteY, corridorX) are written at solve time and thereafter
   only read by the drawing and hit-test code, so a change to either
   view's transform can be absorbed by transforming them in place.

   That leaves the following as the solve's real dependencies:

     • the viewport size and DPR         — affects GEOMETRY.bounds fit
     • the room's plan bounds            — ditto
     • the layout bands                  — bundleY base, screen min
     • the wall-segment table            — WALL.segments
     • router.ESCAPE_DROP                — stripRouteY offset

   Notably absent:

     • viewFloor.scale / tx / ty
     • viewWall.scaleX / scaleY / zoom / tx / ty
     • router.leftBaseX / rightBaseX

   These were in earlier revisions.  Keeping them there forced a full
   re-solve on every pan and every zoom tick of either view.  All of
   them are now handled by _raApplyFloorAffine / _raApplyWallAffine on
   the cached routes. */
function _raTopologySig() {
  const p = RA_SOLVE_SIGNATURE_PRECISION;
  const parts = [];

  parts.push('vp' + window.innerWidth + 'x' + window.innerHeight);
  parts.push('dpr' + (window.devicePixelRatio || 1).toFixed(2));

  const b = GEOMETRY.bounds;
  parts.push('gb'
    + _raQuantize(b.minX, p) + ','
    + _raQuantize(b.maxX, p) + ','
    + _raQuantize(b.minY, p) + ','
    + _raQuantize(b.maxY, p));

  parts.push('lo' + layout.dividerY
                  + ',' + layout.wallY
                  + ',' + layout.wallH
                  + ',' + layout.floorH);

  parts.push('r' + router.ESCAPE_DROP);

  parts.push('ws' + WALL.segments.length);
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    parts.push(
      (isSegHidden(i) ? 'h' : 'v')
      + _raQuantize(s.a[0], p) + ',' + _raQuantize(s.a[1], p)
      + _raQuantize(s.b[0], p) + ',' + _raQuantize(s.b[1], p)
      + _raQuantize(s.u0, 0.1) + ',' + _raQuantize(s.u1, 0.1));
  }

  return parts.join('|');
}

/* Apply a floor-view scale + translation to the floor-space fields of
   every cached route: planX, planY, descentX, bundleY, and every
   point in ownPts.

   w2sFloor(x, y) = [x·scale + tx, −y·scale + ty].  Under a floor
   view change from (scale0, tx0, ty0) to (scale1, tx1, ty1):

     planX_new = planX_old·k + (tx1 − tx0·k)
     planY_new = planY_old·k + (ty1 − ty0·k)   with k = scale1/scale0

   The four strip-space fields are untouched: they carry u/v
   coordinates that a floor-view change does not affect. */
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

/* Apply a wall-view scale + translation to the four strip-space
   fields of every cached route.

   w2sWall(u, v) = [u·scaleX + tx, −v·scaleY + ty].  Under a wall
   view change:

     stripX_new = (stripX_old − tx0)·kX + tx1
     where kX = scaleX1 / scaleX0.  Rearranged:

       stripX_new = stripX_old·kX + (tx1 − tx0·kX)

   so stripX takes the usual scale-about-a-shifted-origin form.

   stripBaseY and stripRouteY are row-space values — viewWall.ty and
   viewWall.ty + ESCAPE_DROP — so they shift by (ty1 − ty0) and
   never scale.

   corridorX is NOT a fixed strip-mm point: it is a fixed SCREEN
   offset from the strip's edge (12 px left of tx, or 12 px right of
   tx + totalU·scaleX).  It therefore cannot be derived by scaling
   an old value; it has to be re-read from router.leftBaseX /
   router.rightBaseX, which fitViews has already updated.  Each
   route's useLeft flag selects which one to take.

   The floor-space fields are untouched by a wall-view change.  The
   two transforms compose cleanly when both views move in the same
   frame: they write disjoint field sets. */
function _raApplyWallAffine(routes,
                            s0x, s0y, tx0, ty0,
                            s1x, s1y, tx1, ty1) {
  if (s0x <= 0) s0x = s1x;
  const kx = s1x / s0x;
  const cx = tx1 - tx0 * kx;   // stripX_new = stripX_old·kx + cx
  const dy = ty1 - ty0;        // stripBaseY / stripRouteY translate

  const lb = router.leftBaseX;
  const rb = router.rightBaseX;

  for (const r of routes) {
    r.stripX      = r.stripX      * kx + cx;
    r.stripBaseY  = r.stripBaseY  + dy;
    r.stripRouteY = r.stripRouteY + dy;
    r.corridorX   = r.useLeft ? lb : rb;
  }
  void s0y; void s1y;   // y scale is intentionally not used
}

window.__arrowForceResolve = function () {
  _raTransCache.topoSig = null;
  _raTransCache.routes = null;
  _raTransCache.lastSolveMs = 0;
};

function drawWallToPlanArrows() {
  if (!WALL.segments.length) {
    routeCache = [];
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
  const now  = (typeof performance !== 'undefined')
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

    /* Whole-cache fast path: neither view has moved.  Nothing to
       transform, nothing to re-solve — just redraw the cached
       routes. */
    if (!floorMoved && !wallMoved) {
      window.__arrowCacheStats.reuse++;
      if (_raArrowsEnabled) {
        routeCache = _raTransCache.routes;
        _raDrawAllRoutes();
      }
      return;
    }

    /* One or both views have moved rigidly (pan) or affinely (zoom).
       Apply the matching transform to the cached routes in place,
       then draw.

       The two transforms touch disjoint field sets, so they compose
       correctly when both views move in the same frame:

         _raApplyFloorAffine  writes planX, planY, descentX,
                              bundleY, ownPts            (floor-space)
         _raApplyWallAffine   writes stripX, stripBaseY,
                              stripRouteY, corridorX     (strip-space)

       The solver reads only floor-space fields; its decisions are
       invariant under either transform. */
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
    if (_raArrowsEnabled) {
      routeCache = _raTransCache.routes;
      _raDrawAllRoutes();
    }
    return;
  }

  if (_raTransCache.routes
      && (now - _raTransCache.lastSolveMs) < RA_SOLVE_DEBOUNCE_MS) {
    window.__arrowCacheStats.skips++;
    if (_raArrowsEnabled) {
      routeCache = _raTransCache.routes;
      _raDrawAllRoutes();
    }
    return;
  }

  if (!_raArrowsEnabled) return;

  window.__arrowCacheStats.misses++;
  routeCache = [];

  /* The tuning constants are held at their base values.  They are
     compared against floor-screen px inside _raScoreSide and
     _raSegmentPairScore, where floor zoom is already accounted for by
     the geometry itself; coupling them to the wall-view zoom was an
     earlier design choice, and it is what forced a full re-solve on
     every wheel tick of the wall view.  With both views now handled
     by their affine transform on the cache, the constants must not
     vary with either. */
  _raSetZoom(1);

  const allRoutes = _raPrepareRoutes();
  const sides = _raBuildSides(allRoutes);

  _raSolve(sides.left);
  _raSolve(sides.right);

  for (const r of allRoutes) routeCache.push(r);

  _raTransCache.topoSig     = topoSig;
  _raTransCache.floorScale  = fs;
  _raTransCache.floorTx     = ftx;
  _raTransCache.floorTy     = fty;
  _raTransCache.wallScaleX  = wsx;
  _raTransCache.wallScaleY  = wsy;
  _raTransCache.wallTx      = wtx;
  _raTransCache.wallTy      = wty;
  _raTransCache.routes      = routeCache;
  _raTransCache.lastSolveMs = now;

  _raDrawAllRoutes();
}
"""
