"""
pg_navigation.py — layout split, zoom, pan, resize.

The two-band split (plan above, unfolded wall below) is computed from
the room's aspect ratio, clamped to [35 %, 65 %] of the available
height, and *snapped* into place — no tween.  The floor band's fit is
the plan's bounding box centred in the band with a floor pad on all
sides.  The wall band's fit reserves a ruler gutter above the strip
whose height is estimated by running the strip's own multi-row ruler
layout on the current footprint set — so the gutter is exactly as
tall as the ruler will actually need for the current scale.

Zoom is per-view, clamped to [MIN_ZOOM, MAX_ZOOM], and applied as an
additive pan on top of the base fit.  The wheel zooms about the
cursor: the world point under the cursor is computed before the zoom
and the pan is adjusted so it lands under the cursor again after.  A
resize recomputes the base fit but keeps the user's zoom and pan
relative to it, so the adjustment survives a viewport change.

The wall band additionally has a horizontal-only stretch factor,
viewWall.zoomX, which feeds viewWall.scaleX alone.  Shift+wheel over
the wall band adjusts it; Shift+wheel over the floor band is left as
a uniform zoom, because the plan is isotropic and there is no X-only
case to expose.  Both factors compose with the uniform zoom, so a
single reset (Home, or the panel's zoom-reset button) clears them
together.

The Home key resets both views to their base fits — uniform zoom, X
stretch, and pan all at once.
"""


NAVIGATION_JS = r"""
/* ==========================================================================
   LAYOUT
   ==========================================================================
   The floor band's height is chosen so the room's aspect ratio sits
   comfortably in it: a room taller than it is wide gets a taller
   floor band, up to a 65 % cap; a room wider than it is tall gets a
   shorter floor band, down to a 35 % floor.  The band split between
   the two views is a straight snap — there is no transition. */

function computeTargetFloorH(cw, ch) {
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

function fitViews() {
  const cw = window.innerWidth;

  /* ---- Floor ---- */
  {
    const b = GEOMETRY.bounds;
    const bw = (b.maxX - b.minX) || 1;
    const bh = (b.maxY - b.minY) || 1;
    const sf = Math.min((cw - 2 * FLOOR_PAD) / bw,
                        (layout.floorH - 2 * FLOOR_PAD) / bh);
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

         viewWall.zoom   uniform — applied to both axes.  This is what
                         the plain wheel adjusts.
         viewWall.zoomX  horizontal-only — applied to scaleX alone.
                         This is what Shift+wheel adjusts: it dilates
                         the unfolded strip sideways without touching
                         its height, which is what you want when a
                         narrow wall's cable geometry is packed too
                         tightly to read but you do not want to also
                         stretch every vertical dimension.

     zoomX = 1 is a no-op.  The two compose: an overall zoom of 1.5
     and an X-stretch of 2.0 gives scaleX a factor of 3.0 and scaleY
     a factor of 1.5.  The router base Xs are read from scaleX, so
     they track the dilation automatically — arrows and hit testing
     stay honest. */
  {
    const topPad = 20;
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

/* Estimate the height the strip's footprint ruler will need at the
   current width, so the wall band's fit can reserve a gutter above
   the strip.  Runs the same row-packing pass the ruler itself runs,
   with one row's worth of margin — the ruler will then fit in the
   reserved gutter without a second pass. */
function estimateRulerGutter(cw) {
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
   ==========================================================================
   The wheel zooms the view under the cursor about the cursor: the
   world point under the cursor is captured before the zoom, and the
   pan is adjusted so the same world point lands under the cursor
   again after.  The two fitViews calls are what make the adjustment
   stick — the base transform is recomputed at the new zoom first,
   then the pan correction is applied on top of it. */

function zoomViewAt(view, s2w, w2s, sx, sy, factor) {
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

/* Horizontal-only stretch for the unfolded-wall band.

   The wall band's x-axis is the strip's u coordinate (unfolded
   perimeter), y-axis is height.  This function dilates only the x
   axis, leaving every vertical dimension untouched, and does so
   about the cursor — the wall's u coordinate under the cursor stays
   put while everything else slides horizontally around it.

   Derivation is the same as zoomViewAt's, with two differences:

     • the scale being changed is viewWall.zoomX, which feeds scaleX
       only, so the on-screen shift it produces is purely horizontal;
     • s2wWall(w, 0)[0] / w2sWall(u, 0)[0] are used to read/write the
       u pixel position, since there is no y component to worry about.

   Bounds share MIN_ZOOM / MAX_ZOOM with the uniform zoom.  That means
   a 1.0 uniform zoom can be X-stretched up to 8× and compressed down
   to 0.25×, or combined with a uniform zoom for more reach.  If you
   ever want a wider X-only range, split the bounds into
   MIN_ZOOM_X / MAX_ZOOM_X rather than loosening these.

   The seed guard is defensive: if viewWall.zoomX is somehow
   undefined or NaN (a partially-applied edit, an old saved state),
   the function self-heals to zoomX = 1 for one tick rather than
   propagating NaN through scaleX and every downstream renderer.

   The two fitViews() calls bracket the panX adjustment; the first
   recomputes tx at the new scaleX, the second re-applies tx with the
   corrected panX.  This is exactly the pattern zoomViewAt uses, and
   it is why the second fitViews cannot be elided. */

function stretchWallX(sx, factor) {
  const [wu] = s2wWall(sx, 0);
  const seed = (typeof viewWall.zoomX === "number" &&
                isFinite(viewWall.zoomX)) ? viewWall.zoomX : 1;
  const newZoomX = Math.max(MIN_ZOOM,
                            Math.min(MAX_ZOOM, seed * factor));
  if (newZoomX === viewWall.zoomX) return;
  viewWall.zoomX = newZoomX;
  fitViews();
  const [newSx] = w2sWall(wu, 0);
  viewWall.panX += sx - newSx;
  fitViews();
}

function panView(view, dx, dy) {
  view.panX += dx;
  view.panY += dy;
  fitViews();
}

function resetZoomPan() {
  viewFloor.zoom = 1; viewFloor.panX = 0; viewFloor.panY = 0;
  viewWall.zoom  = 1; viewWall.zoomX = 1;
  viewWall.panX  = 0; viewWall.panY  = 0;
  fitViews();
  state.hoveredRoute = null;
  draw();
  flashStatus(T("msgViewReset"), "ok");
}

/* ==========================================================================
   RESIZE
   ========================================================================== */

function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  layout.floorH   = computeTargetFloorH(cw, ch);
  layout.dividerY = layout.floorH;
  layout.wallY    = layout.floorH + 4;
  layout.wallH    = ch - layout.wallY;

  fitViews();
  draw();
}
window.addEventListener("resize", resize);
"""
