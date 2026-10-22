"""
pg_view_wall.py — the unfolded-wall band.

`drawWallView` renders the bottom half of the sheet: the strip itself,
which is a common x-axis (the unfolded perimeter) with height as its
y-axis.  Its layers, in order:

    • the band's own paperTint background
    • a backslash void hatch inside the strip outline
    • a paper-coloured block for each wall/step/column chunk
    • a crosshatch highlight over the wall under the cursor
    • faint vertical rules at every chunk boundary and the ceiling line
    • the dashed magenta z_hi rules on every step face
    • the strip's footprint ruler (multi-row dimension chain above the
      strip, with its own collision solver)
    • wall grid lines and their u/v ruled pills (focus-aware)
    • the step-bridge bars (dashed connectors between step-top anchors
      that are floor-side but sit on a raised surface)
    • the cables, wall-side, plus vertex balloons and dimension chains
    • redlines
    • the drawing preview for wall-side drawing
    • the cross-view boundary markers for floor-side anchors at floor
      level
    • step dimension chains (the h_XXX lines beside each step chunk)
    • the focus-mode banner
    • floor-cursor projection dots and their distance labels, when the
      cursor is over the floor band

Focus-mode grid projection
--------------------------
A wall grid line drawn outside focus spans the whole strip.  Inside
focus, the strip has been remapped; a horizontal line is projected
onto every visible wall whose z-band contains the line's height; a
vertical line is projected onto the single visible wall whose
ORIGINAL u-range contains the line's u — the lookup goes through
`_segAtOriginalU` (from `pg_state.js`, the leaf) so `pg_snap.js` does
not need to import this file.  A vertical line whose original u sits
on a hidden segment has no visible surface to draw on and is skipped.
"""

WALL_VIEW_JS = r"""
import {
  GEOMETRY, WALL, WALL_HEIGHT,
  ctx, PALETTE, FONT_MONO, FONT_SANS,
  state, mouse, viewFloor, viewWall, layout,
  SHEET_MARGIN,
  DIM_TICK_LEN, DIM_ROW_H, DIM_EXT_OVER,
  segTop, segBottom, isStepFace, fmtCm,
  _screenPat, _drawValuePill,
  w2sWall, s2wFloor,
  _segAtOriginalU,
} from "./pg_state.js";
import { isSegHidden } from "./pg_core.js";
import {
  drawHoverStripHatch,
  drawDimensionChain, drawNotes, drawDrawingPreview,
  _drawGridPill,
} from "./pg_view_sheet.js";
import {
  drawCable, drawVertexBalloons, drawCrossMarkersFromFloorCables,
  drawStepBridgeBars,
} from "./pg_view_cables.js";
import { projectOntoWalls } from "./pg_route.js";

/* ==========================================================================
   STEP DIMENSIONS
   ========================================================================== */

export function drawStepDimensions() {
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (!isStepFace(s)) continue;

    const [sxL, syT] = w2sWall(s.u0, segTop(s));
    const [sxR, syB] = w2sWall(s.u1, segBottom(s));
    const segW = Math.abs(sxR - sxL);

    const hMm = segTop(s) - segBottom(s);
    const text = "h " + fmtCm(hMm);

    if (segW < 80) {
      const [cx, cy] = w2sWall((s.u0 + s.u1) / 2,
                               (segTop(s) + segBottom(s)) / 2);
      _drawValuePill(cx, cy, text);
      continue;
    }

    const dimX = sxL + 14;

    ctx.save();
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 0.7;

    ctx.beginPath();
    ctx.moveTo(sxL, syT); ctx.lineTo(dimX + 5, syT); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(sxL, syB); ctx.lineTo(dimX + 5, syB); ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(dimX, syT); ctx.lineTo(dimX, syB); ctx.stroke();

    const T = DIM_TICK_LEN;
    ctx.beginPath();
    ctx.moveTo(dimX - T, syT + T); ctx.lineTo(dimX + T, syT - T); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(dimX - T, syB + T); ctx.lineTo(dimX + T, syB - T); ctx.stroke();
    ctx.restore();

    _drawValuePill(dimX + 22, (syT + syB) / 2, text);
  }
}

/* ==========================================================================
   WALL GRID LINES
   ========================================================================== */

export function drawWallGridLines() {
  for (const g of state.wallGrids) {
    const active = (state.dragGrid && state.dragGrid.grid === g);
    ctx.strokeStyle = active ? PALETTE.ink : PALETTE.inkGuide;
    ctx.lineWidth   = active ? 2.2 : 1.2;
    ctx.setLineDash(active ? [8, 4] : [10, 6]);

    if (g.type === "v") {
      if (state.focusSavedU) {
        const segIdx = _segAtOriginalU(g.pos);
        if (segIdx >= 0) {
          const saved = state.focusSavedU.get(segIdx);
          const cur   = WALL.segments[segIdx];
          const uNow  = cur.u0 + (g.pos - saved.u0);
          const [gx, gy0] = w2sWall(uNow, segBottom(cur));
          const [,  gy1]  = w2sWall(uNow, segTop(cur));
          ctx.beginPath();
          ctx.moveTo(gx, gy0); ctx.lineTo(gx, gy1);
          ctx.stroke();
        }
      } else {
        const [gx, gy0] = w2sWall(g.pos, 0);
        const [,  gy1]  = w2sWall(g.pos, WALL_HEIGHT);
        ctx.beginPath();
        ctx.moveTo(gx, gy0); ctx.lineTo(gx, gy1);
        ctx.stroke();
      }
    } else {
      if (state.focusSavedU) {
        for (let i = 0; i < WALL.segments.length; i++) {
          if (isSegHidden(i)) continue;
          const s = WALL.segments[i];
          if (g.pos < segBottom(s) - 0.5) continue;
          if (g.pos > segTop(s)    + 0.5) continue;
          const [gx0, gy] = w2sWall(s.u0, g.pos);
          const [gx1, _]  = w2sWall(s.u1, g.pos);
          ctx.beginPath();
          ctx.moveTo(gx0, gy); ctx.lineTo(gx1, gy);
          ctx.stroke();
        }
      } else {
        const [gx0, gy] = w2sWall(0, g.pos);
        const [gx1, _]  = w2sWall(WALL.totalU, g.pos);
        ctx.beginPath();
        ctx.moveTo(gx0, gy); ctx.lineTo(gx1, gy);
        ctx.stroke();
      }
    }
  }
  ctx.setLineDash([]);

  for (const g of state.wallGrids) {
    const active = (state.dragGrid && state.dragGrid.grid === g);
    const sibs = state.wallGrids.filter(x => x.type === g.type)
                                .sort((a, b) => a.pos - b.pos);
    const idx = sibs.indexOf(g);
    const label = g.type === "v" ? "u" + (idx + 1) : "v" + (idx + 1);

    if (g.type === "v") {
      let uForPill = g.pos;
      if (state.focusSavedU) {
        const segIdx = _segAtOriginalU(g.pos);
        if (segIdx < 0) continue;
        const saved = state.focusSavedU.get(segIdx);
        const cur   = WALL.segments[segIdx];
        uForPill = cur.u0 + (g.pos - saved.u0);
      }
      const [gx] = w2sWall(uForPill, 0);
      _drawGridPill(gx, layout.wallY + 18, label, active);
    } else {
      const [, gy] = w2sWall(0, g.pos);
      if (gy < layout.wallY - 20)                  continue;
      if (gy > layout.wallY + layout.wallH + 20)   continue;
      _drawGridPill(SHEET_MARGIN + 24, gy, label, active);
    }
  }
}

/* ==========================================================================
   FOOTPRINT RULER (multi-row dimension chain above the strip)
   ========================================================================== */

export function drawWallRulers() {
  if (!WALL.footprints.length) return;

  const FS = 10;
  const ROW_H = DIM_ROW_H;
  const PAD = 8;
  const MAX_ROWS = 8;
  const stripTopY = viewWall.stripTopY;

  ctx.font = `600 ${FS}px ${FONT_MONO}`;

  const items = [];
  for (const fp of WALL.footprints) {
    const [xa] = w2sWall(fp.u0, 0);
    const [xb] = w2sWall(fp.u1, 0);
    const lo = Math.min(xa, xb), hi = Math.max(xa, xb);
    const text = fmtCm(fp.len);
    const textW = ctx.measureText(text).width;
    items.push({
      xLo: lo, xHi: hi, xMid: (lo + hi) / 2,
      text, textW, row: 0, rowY: 0,
    });
  }
  items.sort((a, b) => a.xLo - b.xLo);

  const rowEnds = new Array(MAX_ROWS).fill(-Infinity);
  for (const it of items) {
    let row = 0;
    for (; row < MAX_ROWS; row++) {
      const pillLo = it.xMid - it.textW / 2 - 6;
      if (pillLo > rowEnds[row] + PAD) break;
    }
    if (row === MAX_ROWS) row = MAX_ROWS - 1;
    it.row = row;
    rowEnds[row] = Math.max(rowEnds[row], it.xHi + PAD);
  }
  for (const it of items) {
    it.rowY = stripTopY - (it.row + 1) * ROW_H + ROW_H * 0.4;
  }

  ctx.save();
  ctx.strokeStyle = PALETTE.ink;
  ctx.lineWidth = 0.7;

  for (const it of items) {
    const dimY = it.rowY;
    ctx.beginPath();
    ctx.moveTo(it.xLo, stripTopY);
    ctx.lineTo(it.xLo, dimY - DIM_EXT_OVER);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(it.xHi, stripTopY);
    ctx.lineTo(it.xHi, dimY - DIM_EXT_OVER);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(it.xLo, dimY); ctx.lineTo(it.xHi, dimY);
    ctx.stroke();

    const T = DIM_TICK_LEN;
    ctx.beginPath();
    ctx.moveTo(it.xLo - T, dimY + T); ctx.lineTo(it.xLo + T, dimY - T);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(it.xHi - T, dimY + T); ctx.lineTo(it.xHi + T, dimY - T);
    ctx.stroke();
  }
  ctx.restore();

  ctx.save();
  ctx.font = `600 ${FS}px ${FONT_MONO}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const it of items) {
    const dimY = it.rowY;
    const pillW = it.textW + 8;
    const pillH = 12;
    ctx.fillStyle = PALETTE.paper;
    ctx.fillRect(it.xMid - pillW / 2, dimY - pillH / 2, pillW, pillH);
    ctx.fillStyle = PALETTE.ink;
    ctx.fillText(it.text, it.xMid, dimY + 0.5);
  }
  ctx.restore();
}

/* ==========================================================================
   WALL VIEW — MAIN
   ========================================================================== */

export function drawWallView() {
  const cw = window.innerWidth;
  ctx.fillStyle = PALETTE.paperTint;
  ctx.fillRect(0, layout.wallY, cw, layout.wallH);

  /* ---- 1. void hatch inside the strip outline ---- */
  const [x0, y0] = w2sWall(0, 0);
  const [x1, y1] = w2sWall(WALL.totalU, 0);
  const [x2, y2] = w2sWall(WALL.totalU, WALL_HEIGHT);
  const [x3, y3] = w2sWall(0, WALL_HEIGHT);
  ctx.beginPath();
  ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
  ctx.lineTo(x2, y2); ctx.lineTo(x3, y3); ctx.closePath();
  ctx.fillStyle = _screenPat("voidHatch");
  ctx.fill();
  ctx.strokeStyle = PALETTE.ink;
  ctx.lineWidth = 1.6;
  ctx.stroke();

  /* ---- 2. paper-filled chunks per segment ---- */
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, syHi] = w2sWall(s.u0, s.z_hi);
    const [sx1, syLo] = w2sWall(s.u1, s.z_lo);
    ctx.fillStyle = PALETTE.paper;
    ctx.fillRect(sx0, syHi, sx1 - sx0, syLo - syHi);
  }

  /* ---- 3. hover strip hatch (crosshatch on the wall under the cursor) ---- */
  drawHoverStripHatch();

  /* ---- 4. faint vertical rules at chunk boundaries ---- */
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, syTop] = w2sWall(s.u0, 0);
    const [,  syBot]   = w2sWall(s.u0, WALL_HEIGHT);
    ctx.strokeStyle = PALETTE.inkFaint;
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(sx0, syTop); ctx.lineTo(sx0, syBot);
    ctx.stroke();
  }

  /* ---- 5. step-face z_hi rules (dashed magenta) ---- */
  ctx.save();
  ctx.setLineDash([4, 3]);
  ctx.strokeStyle = PALETTE.step;
  ctx.lineWidth = 1.4;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (!isStepFace(s)) continue;
    const t = segTop(s);
    const [sx0, sy0] = w2sWall(s.u0, t);
    const [sx1, sy1] = w2sWall(s.u1, t);
    ctx.beginPath(); ctx.moveTo(sx0, sy0); ctx.lineTo(sx1, sy1);
    ctx.stroke();
  }
  ctx.setLineDash([]); ctx.restore();

  /* ---- 6. footprint ruler (hidden during focus mode) ---- */
  if (!state.focusSavedU) drawWallRulers();

  /* ---- 7. wall grid lines + pills ---- */
  drawWallGridLines();

  /* ---- 8. step-bridge bars ---- */
  drawStepBridgeBars();

  /* ---- 9. wall-side cables + selection chrome ---- */
  for (const c of state.wallCables) drawCable(c, "wall");
  drawVertexBalloons("wall");
  drawDimensionChain("wall");

  /* ---- 10. redlines ---- */
  drawNotes("wall");

  /* ---- 11. drawing preview ---- */
  drawDrawingPreview("wall");

  /* ---- 12. cross-view boundary markers (from floor-side cables) ---- */
  drawCrossMarkersFromFloorCables();

  /* ---- 13. step dimensions (h_XXX) ---- */
  drawStepDimensions();

  /* ---- 14. focus-mode banner ---- */
  if (state.focusSavedU && state.focusSeg >= 0) {
    const seg = WALL.segments[state.focusSeg];
    const tag = (seg && seg.tag) ? seg.tag : ("segment " + (state.focusSeg + 1));
    ctx.font = "700 11px " + FONT_MONO;
    ctx.fillStyle = PALETTE.ink;
    ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    ctx.fillText("FOCUS  ·  " + tag + "  ·  neighbours shown adjacent",
                 12, layout.wallY + 30);
  }

  /* ---- 15. floor-cursor projection dots + distances ---- */
  if (mouse.inside && mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    const proj = projectOntoWalls(wx, wy);

    let nearestIdx = -1;
    let nearestDist = Infinity;
    for (let i = 0; i < proj.length; i++) {
      if (proj[i].dist < nearestDist) {
        nearestDist = proj[i].dist;
        nearestIdx  = i;
      }
    }

    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.font = "600 9px " + FONT_MONO;

    for (let i = 0; i < proj.length; i++) {
      const p = proj[i];
      const [sx, sy] = w2sWall(p.u, 0);
      const isNearest = (i === nearestIdx);

      const ink = isNearest ? PALETTE.highlight : PALETTE.transit;

      ctx.beginPath();
      ctx.arc(sx, sy, isNearest ? 5.5 : 4.5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.paper;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(sx, sy, isNearest ? 4 : 3, 0, Math.PI * 2);
      ctx.fillStyle = ink;
      ctx.fill();

      if (isNearest) {
        ctx.beginPath();
        ctx.arc(sx, sy, 8.5, 0, Math.PI * 2);
        ctx.strokeStyle = PALETTE.highlight;
        ctx.lineWidth = 1.6;
        ctx.setLineDash([3, 2]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      const label = fmtCm(p.dist) + " cm";
      const tw = ctx.measureText(label).width;
      const padX = 3;
      const labelY = sy + (isNearest ? 9 : 6);
      ctx.fillStyle = PALETTE.paper;
      ctx.fillRect(sx - tw / 2 - padX, labelY, tw + 2 * padX, 12);
      ctx.fillStyle = ink;
      ctx.fillText(label, sx, labelY + 2);
    }

    ctx.restore();
  }
}
"""
