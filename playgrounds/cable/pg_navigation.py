"""
pg_navigation.py — layout split, zoom, pan, resize.

ES module.  This module is the sole authority for the band split, the
view transforms, and every pan/zoom operation.  The three view fields
(`viewFloor.zoom`, `viewWall.zoom`, `viewWall.zoomX`) live on the
objects in `pg_state.js`; this module mutates them and rebuilds the
transforms via `fitViews()`.

In the concatenation model, `resize` and `fitViews` were defined here
AND re-defined identically in `pg_core.py`.  Because `pg_core.py` was
concatenated before `pg_navigation.py`, the navigation version won.
In the ES-module model, `pg_core.py` no longer defines them; this
module is the only definition.

The Home key, the panel's zoom-reset button, and the "reset view"
action all route through `resetZoomPan`, so the three surfaces never
drift.

`draw()` in the original is called from `resize`, `zoomViewAt`,
`stretchWallX`, `panView`, and `resetZoomPan`.  Those calls go
through `callDraw()` — the injected hook — because this module does
not import `pg_dispatch.js` (which would create a cycle).
"""

NAVIGATION_JS = r"""
import {
  GEOMETRY, WALL, WALL_HEIGHT,
  canvas, ctx, env,
  layout, viewFloor, viewWall, router, state,
  PALETTE, FONT_MONO, FONT_SANS,
  MIN_ZOOM, MAX_ZOOM, ZOOM_STEP,
  SPLIT_MIN_FRAC, SPLIT_MAX_FRAC,
  WALL_SIDE_CHAN,
  DIM_ROW_H,
  callDraw,
} from "./pg_state.js";
import { flashStatus } from "./pg_core.js";
import { T } from "./pg_i18n.js";

/* ==========================================================================
   LAYOUT
   ==========================================================================
   The floor band's height is chosen so the room's aspect ratio sits
   comfortably in it: a room taller than it is wide gets a taller
   floor band, up to a 65 % cap; a room wider than it is tall gets a
   shorter floor band, down to a 35 % floor.  The band split between
   the two views is a straight snap — there is no transition. */

export function computeTargetFloorH(cw, ch) {
  const b = GEOMETRY.bounds;
  const bw = Math.max(b.maxX - b.minX, 1);
  const bh = Math.max(b.maxY - b.minY, 1);
  const aspect = bh / bw;

  let frac = 0.50;
  if (aspect > 1) frac += Math.min(0.15, (aspect - 1) * 0.10);
  else            frac -= Math.min(0.15, (1 - aspect) * 0.10);
  frac = Math.max(SPLIT_MIN_FRAC, Math.min(SPLIT_MAX_FRAC, frac));

  const dividerH = 4;
  const availH = ch - dividerH;
  const h = Math.round(availH * frac);
  return Math.max(180, h);
}

export function fitViews() {
  const cw = window.innerWidth;

  /* ---- Floor ---- */
  {
    const b = GEOMETRY.bounds;
    const bw = (b.maxX - b.minX) || 1;
    const bh = (b.maxY - b.minY) || 1;
    const sf = Math.min((cw - 2 * 60) / bw,
                        (layout.floorH - 2 * 60) / bh);
    const mx = (b.minX + b.maxX) / 2, my = (b.minY + b.maxY) / 2;
    const baseTx = cw / 2 - mx * sf;
    const baseTy = layout.floorH / 2 + my * sf;
    const cx = cw / 2, cy = layout.floorH / 2;

    viewFloor.scale = sf * viewFloor.zoom;
    viewFloor.tx = cx + (baseTx - cx) * viewFloor.zoom + viewFloor.panX;
    viewFloor.ty = cy + (baseTy - cy) * viewFloor.zoom + viewFloor.panY;
  }

  /* ---- Wall ----
     The wall band carries TWO independent scale factors:
       viewWall.zoom    uniform, both axes
       viewWall.zoomX   horizontal-only stretch (Shift+wheel)

     Both compose; zoomX = 1 is a no-op. */
  {
    const topPad    = 20;
    const bottomPad = router.ESCAPE_DROP + 20;
    const gutterTop = estimateRulerGutter(cw);
    const W = WALL.totalU || 1, H = WALL_HEIGHT || 1;
    const availW = cw - 2 * topPad - 2 * WALL_SIDE_CHAN;
    const availH = Math.max(40, layout.wallH - topPad - bottomPad - gutterTop);
    const baseSx = availW / W;
    const baseSy = availH / H;
    const baseTx = topPad + WALL_SIDE_CHAN;
    const baseTy = layout.wallY + layout.wallH - bottomPad;
    const cx = cw / 2, cy = layout.wallY + layout.wallH / 2;

    viewWall.scaleX = baseSx * viewWall.zoom * viewWall.zoomX;
    viewWall.scaleY = baseSy * viewWall.zoom;
    viewWall.tx = cx + (baseTx - cx) * viewWall.zoom + viewWall.panX;
    viewWall.ty = cy + (baseTy - cy) * viewWall.zoom + viewWall.panY;
    viewWall.stripTopY = viewWall.ty - H * viewWall.scaleY;

    router.leftBaseX  = viewWall.tx - 12;
    router.rightBaseX = viewWall.tx + WALL.totalU * viewWall.scaleX + 12;
  }
}

export function estimateRulerGutter(cw) {
  if (!WALL.footprints || !WALL.footprints.length) return 36;
  const PAD = 8;
  const EST_TEXT_W = 22;
  const topPad = 20;
  const availW = cw - 2 * topPad - 2 * WALL_SIDE_CHAN;
  const totalU = WALL.totalU || 1;
  const pxPerU = availW / totalU;
  const rowEnds = [];
  const sorted = WALL.footprints.slice().sort((a, b) => a.u0 - b.u0);
  for (const fp of sorted) {
    const xLo = fp.u0 * pxPerU;
    const xHi = fp.u1 * pxPerU;
    const xMid = (xLo + xHi) / 2;
    let placed = false;
    for (let r = 0; r < rowEnds.length; r++) {
      const pillLo = xMid - EST_TEXT_W / 2 - 6;
      if (pillLo > rowEnds[r] + PAD) {
        rowEnds[r] = xHi + PAD;
        placed = true;
        break;
      }
    }
    if (!placed) rowEnds.push(xHi + PAD);
  }
  const rows = Math.max(1, rowEnds.length);
  return Math.max(36, Math.min(120, rows * DIM_ROW_H + 20));
}

/* ==========================================================================
   ZOOM AND PAN
   ========================================================================== */

export function zoomViewAt(view, s2w, w2s, sx, sy, factor) {
  const [wx, wy] = s2w(sx, sy);
  const newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, view.zoom * factor));
  if (newZoom === view.zoom) return;
  view.zoom = newZoom;
  fitViews();
  const [nsx, nsy] = w2s(wx, wy);
  view.panX += sx - nsx;
  view.panY += sy - nsy;
  fitViews();
}

export function stretchWallX(sx, factor) {
  const [wu] = (function() {
    return [(sx - viewWall.tx) / viewWall.scaleX];
  })();
  const seed = (typeof viewWall.zoomX === "number" &&
                isFinite(viewWall.zoomX)) ? viewWall.zoomX : 1;
  const newZoomX = Math.max(MIN_ZOOM,
                            Math.min(MAX_ZOOM, seed * factor));
  if (newZoomX === viewWall.zoomX) return;
  viewWall.zoomX = newZoomX;
  fitViews();
  const newSx = wu * viewWall.scaleX + viewWall.tx;
  viewWall.panX += sx - newSx;
  fitViews();
}

export function panView(view, dx, dy) {
  view.panX += dx;
  view.panY += dy;
  fitViews();
}

export function resetZoomPan() {
  viewFloor.zoom = 1; viewFloor.panX = 0; viewFloor.panY = 0;
  viewWall.zoom  = 1; viewWall.zoomX = 1;
  viewWall.panX  = 0; viewWall.panY  = 0;
  fitViews();
  state.hoveredRoute = null;
  callDraw();
  flashStatus(T("msgViewReset"), "ok");
}

/* ==========================================================================
   RESIZE
   ========================================================================== */

export function resize() {
  env.dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * env.dpr);
  canvas.height = Math.round(ch * env.dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);

  layout.floorH   = computeTargetFloorH(cw, ch);
  layout.dividerY = layout.floorH;
  layout.wallY    = layout.floorH + 4;
  layout.wallH    = ch - layout.wallY;

  fitViews();
  callDraw();
}
window.addEventListener("resize", resize);
"""
