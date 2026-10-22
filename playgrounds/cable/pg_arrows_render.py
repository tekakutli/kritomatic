"""
pg_arrows_render.py — the drawing pass.

ES module.  Six exports:

    hitTestRoute          walk every route's polyline and return the
                          nearest within a 6-px tolerance
    _raPrepareRoutes      one route record per visible wall segment
    _raDrawRoutePath      trunk + destination path, haloed
    _raDrawAllRoutes      iterate `state.routeCache`, draw every route
                          in its base style, then re-draw the hovered
                          route in the emphasised weight
    _raDrawArrowhead      arrowhead marker
    _raDrawEndpointDot    small teal dot at the strip and plan ends
    _raClipToFloorBand    clip path for the destination half so a
                          route drawn below the divider does not
                          paint into the strip band

The plot primitives are exported because the printable export module
(`pg_export_*`) reuses the same haloed-arrow drawing path.

STATE INTERFACE
===============
`state.routeCache` is the current route list.  `state.hoveredRoute`
is the single route the hover test resolved last mousemove.

`WALL.segments`, `GEOMETRY.bounds`, `layout`, `viewWall`, and
`router` come from `pg_state.js`.  `isSegHidden` comes from
`pg_core.js`.  Every drawing call writes to the shared `ctx`.
"""

RENDER_JS = r"""
import {
  WALL, GEOMETRY, layout, viewWall, router,
  ctx, PALETTE,
  state,
  pointToSegmentDist,
  w2sWall, w2sFloor,
} from "./pg_state.js";
import { isSegHidden } from "./pg_core.js";

/* ==========================================================================
   ARROWHEAD
   ========================================================================== */

export function _raDrawArrowhead(x, y, dx, dy, color) {
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

/* ==========================================================================
   TRUNK / DESTINATION PATH
   ========================================================================== */

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

export function _raClipToFloorBand() {
  ctx.beginPath();
  ctx.rect(0, 0, window.innerWidth, layout.dividerY);
  ctx.clip();
}

export function _raDrawRoutePath(r, emphasized) {
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

export function _raDrawEndpointDot(x, y, r) {
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
   PREPARE ROUTES
   ========================================================================== */

export function _raPrepareRoutes() {
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
   HIT TESTING
   ========================================================================== */

export function hitTestRoute(sx, sy) {
  if (!state.routeCache.length) return null;
  const TOL = 6;
  let best = null, bestDist = TOL;
  for (const r of state.routeCache) {
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
   BATCH DRAW
   ========================================================================== */

export function _raDrawAllRoutes() {
  ctx.save();
  ctx.lineJoin = "miter";
  ctx.lineCap  = "round";
  for (const r of state.routeCache) {
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
