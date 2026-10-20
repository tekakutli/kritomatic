"""
pg_arrows_render.py — arrowhead drawing, the trunk and destination
path builders, the per-route path renderer, route preparation from the
wall-segment table, the batch draw, and route hit testing.

The one drawing pipeline every visible arrow passes through:

    _raPrepareRoutes    one route record per visible wall segment
    _raDrawRoutePath    trunk (strip anchor → corridor → bundleY)
                        plus destination (corridor → descent → the
                        orthogonal/attack path in ownPts)
    _raDrawEndpointDot  the small teal dot at the strip end and the
                        plan tip

The destination half is clipped to the floor band so a bundleY below
the divider does not paint into the strip.

_raDrawAllRoutes iterates routeCache, drawing every route in its
base style, then re-draws the hovered route in the emphasised weight.

hitTestRoute walks every route's polyline and returns the nearest
within a 6 px tolerance — the same polyline the renderer draws, so a
click on the visible trunk hits the route.
"""


RENDER_JS = r"""
/* ==========================================================================
   SECTION 8 — DRAWING
   ========================================================================== */

function _raDrawArrowhead(x, y, dx, dy, color) {
  const size = 8, halfW = 4;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const px = -uy, py = ux;
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x - ux * size + px * halfW, y - uy * size + py * halfW);
  ctx.lineTo(x - ux * size - px * halfW, y - uy * size - py * halfW);
  ctx.closePath();
  ctx.save();
  ctx.strokeStyle = PALETTE.paper;
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  ctx.stroke();
  ctx.restore();
  ctx.fillStyle = color;
  ctx.fill();
}

function _raBuildTrunkPath(r) {
  ctx.beginPath();
  ctx.moveTo(r.stripX,    r.stripBaseY);
  ctx.lineTo(r.stripX,    r.stripRouteY);
  ctx.lineTo(r.corridorX, r.stripRouteY);
  ctx.lineTo(r.corridorX, r.bundleY);
}

function _raBuildDestPath(r) {
  ctx.beginPath();
  ctx.moveTo(r.corridorX, r.bundleY);
  ctx.lineTo(r.descentX,  r.bundleY);
  if (r.ownPts && r.ownPts.length) {
    for (let i = 1; i < r.ownPts.length; i++) {
      ctx.lineTo(r.ownPts[i][0], r.ownPts[i][1]);
    }
  }
}

function _raClipToFloorBand() {
  ctx.beginPath();
  ctx.rect(0, 0, window.innerWidth, layout.dividerY);
  ctx.clip();
}

function _raDrawRoutePath(r, emphasized) {
  const inkW  = emphasized ? 2.4 : 1.4;
  const haloW = emphasized ? 5.2 : 3.4;

  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap  = "round";

  _raBuildTrunkPath(r);
  ctx.strokeStyle = PALETTE.paper;
  ctx.lineWidth   = haloW;
  ctx.stroke();

  ctx.save();
  _raClipToFloorBand();
  _raBuildDestPath(r);
  ctx.strokeStyle = PALETTE.paper;
  ctx.lineWidth   = haloW;
  ctx.stroke();
  ctx.restore();

  _raBuildTrunkPath(r);
  ctx.strokeStyle = PALETTE.transit;
  ctx.lineWidth   = inkW;
  ctx.stroke();

  ctx.save();
  _raClipToFloorBand();
  _raBuildDestPath(r);
  ctx.strokeStyle = PALETTE.transit;
  ctx.lineWidth   = inkW;
  ctx.stroke();

  let adx = r.sign, ady = 0;
  const pts = r.ownPts || [];
  if (pts.length >= 2) {
    const a = pts[pts.length - 2];
    const b = pts[pts.length - 1];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const L = Math.hypot(dx, dy) || 1;
    adx = dx / L; ady = dy / L;
  }
  _raDrawArrowhead(r.planX, r.planY, adx, ady, PALETTE.transit);
  ctx.restore();

  ctx.restore();
}

function _raDrawEndpointDot(x, y, r) {
  ctx.beginPath();
  ctx.arc(x, y, r + 2, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.paper;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.transit;
  ctx.fill();
}


/* ==========================================================================
   SECTION 9 — PREPARE ROUTES
   ========================================================================== */

function _raPrepareRoutes() {
  const routes = [];
  const midX = (GEOMETRY.bounds.minX + GEOMETRY.bounds.maxX) / 2;
  const stripBaseY  = viewWall.ty;
  const stripRouteY = stripBaseY + router.ESCAPE_DROP;

  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const wxMid = (s.a[0] + s.b[0]) / 2;
    const wyMid = (s.a[1] + s.b[1]) / 2;
    const uMid  = (s.u0 + s.u1) / 2;
    const [stripX] = w2sWall(uMid, 0);
    const [planX, planY] = w2sFloor(wxMid, wyMid);
    const useLeft = (wxMid < midX);
    routes.push({
      stripX, stripBaseY, stripRouteY,
      planX, planY, useLeft, segIdx: i,
      corridorX: useLeft ? router.leftBaseX : router.rightBaseX,
      sign: useLeft ? 1 : -1,
      bundleIdx: 0, groupIdx: 0, peelRank: 0,
      bundleY: 0, descentX: 0, baseBreak: null,
      bundleFanRank: 0,
      bundleJogOffset: 0,
      isDiag: false, ownPts: null, tag: "L0",
      clusterId: -1,
      clusterAttack: false,
      clusterY: null,
      clusterDropX: null,
      clusterRank: 0,
      clusterSize: 1,
      quadrant: -1,
      upperHalf: true,
      escaped: false,
      survivor: false,
    });
  }
  return routes;
}


/* ==========================================================================
   SECTION 11 — HIT TESTING
   ========================================================================== */

function hitTestRoute(sx, sy) {
  if (!_raArrowsEnabled) return null;
  if (!routeCache.length) return null;
  const TOL = 6;
  let best = null, bestDist = TOL;
  for (const r of routeCache) {
    const pts = [
      [r.stripX,    r.stripBaseY],
      [r.stripX,    r.stripRouteY],
      [r.corridorX, r.stripRouteY],
      [r.corridorX, r.bundleY],
      [r.descentX,  r.bundleY],
    ];
    if (r.ownPts) {
      for (let i = 1; i < r.ownPts.length; i++) pts.push(r.ownPts[i]);
    }
    for (let i = 0; i < pts.length - 1; i++) {
      const d = pointToSegmentDist(sx, sy,
        pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]);
      if (d < bestDist) { bestDist = d; best = r; }
    }
  }
  return best;
}


/* ==========================================================================
   SECTION 10 — BATCH DRAW
   ==========================================================================
   The batch painter lives here (rather than in pg_arrows_entry) because
   it is a drawing routine; the entry module calls it as its final step
   and never inspects its contents. */

function _raDrawAllRoutes() {
  ctx.save();
  ctx.lineJoin = "miter";
  ctx.lineCap  = "round";
  for (const r of routeCache) {
    _raDrawRoutePath(r);
    _raDrawEndpointDot(r.stripX, r.stripBaseY, 3);
  }
  ctx.restore();

  const hl = state.hoveredRoute;
  if (hl && !isSegHidden(hl.segIdx)) {
    ctx.save();
    _raDrawRoutePath(hl, true);
    _raDrawEndpointDot(hl.stripX, hl.stripBaseY, 5);
    ctx.save();
    _raClipToFloorBand();
    _raDrawEndpointDot(hl.planX, hl.planY, 5);
    ctx.restore();
    ctx.restore();
  }
}
"""
