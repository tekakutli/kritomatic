"""
pg_view_floor.py — the plan band.

Draws, in order:

    • a faint forward-slash hatch over every hole in the room's
      footprint (plan interior)
    • the floor grid lines, dashed, with their ruled pills
    • a very faint light-ink tint over the step footprints, with a
      dashed magenta riser outline
    • a solid fill for the room's walls (inner faces at cut height)
    • column markers
    • the hovered wall's plan bar
    • the cables, floor-side, plus vertex balloons and dimension
      chains for the selection
    • redline notes
    • the drawing preview (dashed ghost of the cable being drawn)
    • the cross-view boundary markers for wall-side anchors at floor
      level
    • the ruler's measurements and any pending preview

The scale bar (top-right of the band) is NOT drawn here — it used to
be the last item in this pass, but the wall→plan escape arrows are
drawn from `draw()` AFTER this pass, and they were painting over the
bar's text.  `draw()` calls `drawScaleBar()` explicitly, after the
arrow pass, so the bar's paper backing plate sits on top of the
arrows.  The function itself lives here because it is a plan-band
widget.

`_gridLabel` and `_drawGridPill` are imported from `pg_view_sheet.js`
where they live as generic grid-rendering helpers used by both views.

The old `_gridLabel` call in `drawFloorView` was a latent
ReferenceError: the helper was never defined anywhere in the
concatenation bundle.  In the ES-module model it is defined once in
`pg_view_sheet.js` and shared.
"""

FLOOR_VIEW_JS = r"""
import {
  GEOMETRY, WALL, WALL_HEIGHT,
  ctx, PALETTE, FONT_MONO, FONT_SANS,
  state, viewFloor, layout,
  SHEET_MARGIN,
  segTop, segBottom, isStepFace,
  _screenPat,
  w2sFloor,
} from "./pg_state.js";
import {
  drawHoverPlanBar,
  drawDimensionChain, drawNotes, drawDrawingPreview,
  _gridLabel, _drawGridPill,
} from "./pg_view_sheet.js";
import {
  drawCable, drawVertexBalloons, drawCrossMarkersFromWallCables,
} from "./pg_view_cables.js";
import { drawRulers } from "./pg_ruler.js";

/* ==========================================================================
   FACE PATH BUILDER
   ========================================================================== */

function pathFaceFloor(face) {
  ctx.beginPath();
  const o = face.outer;
  if (!o.length) return;
  let [x0, y0] = w2sFloor(o[0][0], o[0][1]);
  ctx.moveTo(x0, y0);
  for (let i = 1; i < o.length; i++) {
    const [x, y] = w2sFloor(o[i][0], o[i][1]);
    ctx.lineTo(x, y);
  }
  ctx.closePath();
  for (const h of face.holes) {
    if (!h.length) continue;
    [x0, y0] = w2sFloor(h[0][0], h[0][1]);
    ctx.moveTo(x0, y0);
    for (let i = 1; i < h.length; i++) {
      const [x, y] = w2sFloor(h[i][0], h[i][1]);
      ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
}

/* ==========================================================================
   COLUMN MARKERS
   ========================================================================== */

export function drawColumnMarkers() {
  const cols = GEOMETRY.columns || [];
  if (!cols.length) return;
  for (const col of cols) {
    const [x0, y0, x1, y1] = col.box;
    const [sxa, sya] = w2sFloor(x0, y0);
    const [sxb, syb] = w2sFloor(x1, y1);
    const bx = Math.min(sxa, sxb), by = Math.min(sya, syb);
    const bw = Math.abs(sxb - sxa), bh = Math.abs(syb - sya);

    ctx.save();

    const cx2 = bx + bw / 2, cy2 = by + bh / 2;
    ctx.strokeStyle = PALETTE.inkFaint;
    ctx.lineWidth = 0.8;
    ctx.setLineDash([6, 3, 1.5, 3]);
    ctx.beginPath();
    ctx.moveTo(bx - 6, cy2); ctx.lineTo(bx + bw + 6, cy2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx2, by - 6); ctx.lineTo(cx2, by + bh + 6);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.strokeStyle = PALETTE.inkGuide;
    ctx.lineWidth = 1.4;
    ctx.setLineDash([3, 3]);
    ctx.strokeRect(bx, by, bw, bh);
    ctx.setLineDash([]);

    ctx.beginPath();
    ctx.arc(cx2, cy2, 5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.ink;
    ctx.fill();
    ctx.strokeStyle = PALETTE.paper;
    ctx.lineWidth = 1.6;
    ctx.stroke();

    ctx.font = "700 10px " + FONT_MONO;
    ctx.fillStyle = PALETTE.ink;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(col.tag, cx2 + 8, cy2);
    ctx.restore();
  }
}

/* ==========================================================================
   SCALE BAR (drawn from draw() AFTER the arrow pass)
   ========================================================================== */

export function drawScaleBar() {
  const pxPerM = 1000 * viewFloor.scale;
  if (pxPerM < 0.01) return;

  const ladder = [0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500];
  let target = ladder[0];
  for (const c of ladder) {
    const w = c * pxPerM;
    if (w >= 60 && w <= 240) { target = c; break; }
    if (w > 240) { target = c / 2; break; }
    target = c;
  }

  const barW = target * pxPerM;
  if (barW < 20 || barW > 400) return;

  const barH = 10;
  const cw = window.innerWidth;
  const x0 = cw - SHEET_MARGIN - 16 - barW;
  const y0 = SHEET_MARGIN + 16;

  ctx.save();
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(x0 - 6, y0 - 4, barW + 12, barH + 22);

  const halfW = barW / 2;
  ctx.fillStyle = PALETTE.ink;
  ctx.fillRect(x0, y0, halfW, barH);
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(x0 + halfW, y0, halfW, barH);
  ctx.strokeStyle = PALETTE.ink;
  ctx.lineWidth = 1;
  ctx.strokeRect(x0 + 0.5, y0 + 0.5, barW - 1, barH - 1);

  const label = (target < 1)
    ? Math.round(target * 100) + " cm"
    : target + " m";

  ctx.font = "600 10px " + FONT_MONO;
  ctx.fillStyle = PALETTE.ink;
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  ctx.fillText("0", x0, y0 + barH + 3);
  ctx.textAlign = "right";
  ctx.fillText(label, x0 + barW, y0 + barH + 3);
  ctx.restore();
}

/* ==========================================================================
   FLOOR VIEW — MAIN
   ========================================================================== */

export function drawFloorView() {
  const cw = window.innerWidth;

  /* ---- 1. plan interior hatches (holes in the room's footprint) ---- */
  for (const face of GEOMETRY.faces) {
    for (const h of face.holes) {
      if (!h.length) continue;
      ctx.beginPath();
      const [x0, y0] = w2sFloor(h[0][0], h[0][1]);
      ctx.moveTo(x0, y0);
      for (let i = 1; i < h.length; i++) {
        const [x, y] = w2sFloor(h[i][0], h[i][1]);
        ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fillStyle = _screenPat("planInterior");
      ctx.fill();
    }
  }

  /* ---- 2. floor grid lines ---- */
  for (const g of state.floorGrids) {
    let x0, y0, x1, y1;
    if (g.type === "ns") {
      [x0, y0] = w2sFloor(g.pos, 0); y0 = 0; y1 = layout.floorH; x1 = x0;
    } else {
      [x0, y0] = w2sFloor(0, g.pos); x0 = 0; x1 = cw; y1 = y0;
    }
    const active = (state.dragGrid && state.dragGrid.grid === g);
    ctx.strokeStyle = active ? PALETTE.ink : PALETTE.inkGuide;
    ctx.lineWidth = active ? 2.2 : 1.2;
    ctx.setLineDash(active ? [8, 4] : [10, 6]);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
    ctx.stroke(); ctx.setLineDash([]);
  }

  /* ---- 3. floor grid pills ---- */
  for (const g of state.floorGrids) {
    const active = (state.dragGrid && state.dragGrid.grid === g);
    const label  = _gridLabel(g, state.floorGrids);
    if (g.type === "ns") {
      const [gx] = w2sFloor(g.pos, 0);
      _drawGridPill(gx, SHEET_MARGIN + 18, label, active);
    } else {
      const [, gy] = w2sFloor(0, g.pos);
      _drawGridPill(SHEET_MARGIN + 22, gy, label, active);
    }
  }

  /* ---- 4. step-face tint + riser outline ---- */
  for (const face of GEOMETRY.stepFaces) {
    pathFaceFloor(face);
    ctx.fillStyle = "rgba(220, 230, 242, 0.05)";
    ctx.fill("evenodd");
    ctx.strokeStyle = PALETTE.stepSoft;
    ctx.lineWidth = 1.2;
    ctx.setLineDash([6, 4]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  /* ---- 5. wall silhouettes ---- */
  for (const face of GEOMETRY.faces) {
    pathFaceFloor(face);
    ctx.fillStyle = PALETTE.ink;
    ctx.fill("evenodd");
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  drawColumnMarkers();

  /* ---- 6. hover plan bar (crosshatch of the wall under the cursor) ---- */
  drawHoverPlanBar();

  /* ---- 7. floor cables + selection chrome ---- */
  for (const c of state.floorCables) drawCable(c, "floor");
  drawVertexBalloons("floor");
  drawDimensionChain("floor");

  /* ---- 8. redlines ---- */
  drawNotes("floor");

  /* ---- 9. drawing preview ---- */
  drawDrawingPreview("floor");

  /* ---- 10. cross-view boundary markers (from wall-side cables) ---- */
  drawCrossMarkersFromWallCables();

  /* ---- 11. ruler measurements + pending preview ---- */
  drawRulers();
}
"""
