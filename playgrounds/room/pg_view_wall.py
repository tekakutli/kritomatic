"""
pg_view_wall.py — the unfolded-wall band.

drawWallView renders the bottom half of the sheet: the strip itself,
which is a common x-axis (the unfolded perimeter) with height as its
y-axis.  Its layers, in order:

    • the band's own paperTint background
    • a backslash void hatch inside the strip outline
    • a paper-coloured block for each wall/step/column chunk, at its
      own z-range
    • a crosshatch highlight over the wall under the cursor
    • faint vertical rules at every chunk boundary, and faint
      horizontal rules at the ceiling line
    • the dashed magenta z_hi rules on every step face
    • the strip's footprint ruler (dimension chain of all edges,
      above the strip, with its own multi-row collision solver)
    • wall grid lines and their u/v ruled pills
    • the step-bridge bars
    • the cables, wall-side, plus vertex balloons and dimension
      chains for the selection
    • notes
    • the drawing preview for wall-side drawing
    • the cross-view boundary markers for floor-side anchors that
      sit on the wall's bottom edge
    • step dimension chains (the h_XXX lines beside each step chunk)
    • the focus-mode banner, when focus mode is active
    • the floor-cursor projection dots and their distance labels,
      when the cursor is over the floor band

The wall band's coordinate system is set by pg_navigation's fitViews,
which also reserves the ruler gutter above the strip.  The band's
own scroll and pan are handled there.

Wall grid lines under focus mode
--------------------------------
A wall grid line drawn outside focus mode spans the whole strip — a
horizontal line at v = Y runs u = [0, totalU], a vertical line at
u = X runs v = [0, WALL_HEIGHT].  Both remain valid outside focus.

Focus mode (entered by addDrawPoint when a wall-edge click changes
the focused segment) remaps the focused wall and its immediate
neighbours onto a compact u-range and hides everything else.  The
absolute u of a vertical grid line no longer points at a visible
surface, and a horizontal line's span no longer corresponds to any
single wall's u-extent.  What the user needs to see during focus is
the PROJECTION of the line onto the visible walls:

    horizontal line at v = Y
        drawn across the u-extent of every visible wall whose z-range
        contains Y.  This is the "crosses over" relationship: the
        level Y physically intersects the wall.

    vertical line at u = X
        drawn only on the visible wall whose ORIGINAL u-range contains
        X, at the remapped position within that wall.  If X falls on a
        hidden segment, there is no visible surface to project onto
        and the line is skipped.

The lookup uses focusSavedU, which holds each visible segment's
PRE-remap u-range; the live WALL.segments[i].u0/u1 for those segments
hold the post-remap values.  Hidden segments keep their original
u-range because enterFocus never rewrites them — they are only marked
_hidden.  The containment test must therefore run against the saved
map, not against the live segment.
"""


WALL_VIEW_JS = r"""
/* ==========================================================================
   WALL VIEW
   ========================================================================== */

function drawStepDimensions() {
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
   WALL GRID LINES — with focus-mode projection
   ==========================================================================
   See the module docstring for the reasoning.  Briefly:

     outside focus   the line spans the full strip, exactly as before
     inside focus    a horizontal line is projected onto every visible
                     wall whose z-band contains the line's height; a
                     vertical line is projected onto the single visible
                     wall whose ORIGINAL u-range contains the line's u

   _segAtOriginalU maps an absolute u back to a segment index.  During
   focus it consults focusSavedU (the pre-remap ranges); outside focus
   it reads the live WALL.segments table directly.  A u that falls on a
   hidden segment resolves to -1 and the line is skipped — there is no
   visible surface to draw it on. */

function _segAtOriginalU(u) {
  if (focusSavedU) {
    for (const [segIdx, saved] of focusSavedU) {
      if (saved.u0 <= u && u <= saved.u1) return segIdx;
    }
    return -1;              // u falls on a hidden segment
  }
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (s.u0 <= u && u <= s.u1) return i;
  }
  return -1;
}

function drawWallGridLines() {
  /* --- Lines ------------------------------------------------------- */
  for (const g of state.wallGrids) {
    const active = (state.dragGrid && state.dragGrid.grid === g);
    ctx.strokeStyle = active ? PALETTE.ink : PALETTE.inkGuide;
    ctx.lineWidth   = active ? 2.2 : 1.2;
    ctx.setLineDash(active ? [8, 4] : [10, 6]);

    if (g.type === "v") {
      if (focusSavedU) {
        const segIdx = _segAtOriginalU(g.pos);
        if (segIdx >= 0) {
          const saved = focusSavedU.get(segIdx);
          const cur   = WALL.segments[segIdx];
          const uNow  = cur.u0 + (g.pos - saved.u0);
          const [gx, gy0] = w2sWall(uNow, segBottom(cur));
          const [,  gy1] = w2sWall(uNow, segTop(cur));
          ctx.beginPath();
          ctx.moveTo(gx, gy0); ctx.lineTo(gx, gy1);
          ctx.stroke();
        }
      } else {
        const [gx, gy0] = w2sWall(g.pos, 0);
        const [,  gy1] = w2sWall(g.pos, WALL_HEIGHT);
        ctx.beginPath();
        ctx.moveTo(gx, gy0); ctx.lineTo(gx, gy1);
        ctx.stroke();
      }
    } else {
      if (focusSavedU) {
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

  /* --- Pill labels ------------------------------------------------- */
  /* A pill is drawn only where the corresponding line actually exists.
     A vertical line whose original u sits on a hidden segment has no
     visible surface to label, so it is skipped.  A horizontal line's
     pill stays on the left margin at its height, which remains
     meaningful during focus — the level exists, the visible walls
     simply clip where it gets drawn. */
  for (const g of state.wallGrids) {
    const active = (state.dragGrid && state.dragGrid.grid === g);
    const sibs = state.wallGrids.filter(x => x.type === g.type)
                                .sort((a, b) => a.pos - b.pos);
    const idx = sibs.indexOf(g);
    const label = g.type === "v" ? "u" + (idx + 1) : "v" + (idx + 1);

    if (g.type === "v") {
      let uForPill = g.pos;
      if (focusSavedU) {
        const segIdx = _segAtOriginalU(g.pos);
        if (segIdx < 0) continue;
        const saved = focusSavedU.get(segIdx);
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


function drawWallView() {
  const cw = window.innerWidth;
  ctx.fillStyle = PALETTE.paperTint;
  ctx.fillRect(0, layout.wallY, cw, layout.wallH);

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

  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, syHi] = w2sWall(s.u0, s.z_hi);
    const [sx1, syLo] = w2sWall(s.u1, s.z_lo);
    ctx.fillStyle = PALETTE.paper;
    ctx.fillRect(sx0, syHi, sx1 - sx0, syLo - syHi);
  }

  drawHoverStripHatch();

  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, syTop] = w2sWall(s.u0, 0);
    const [sx1, syBot] = w2sWall(s.u0, 0);
    ctx.strokeStyle = PALETTE.inkFaint;
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(sx0, syTop); ctx.lineTo(sx1, syBot);
    ctx.moveTo(sx1, syTop); ctx.lineTo(sx1, syBot);
    ctx.stroke();
  }
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const [sx0, sy0] = w2sWall(s.u1, 0);
    const [sx1, sy1] = w2sWall(s.u1, WALL_HEIGHT);
    ctx.beginPath(); ctx.moveTo(sx0, sy0); ctx.lineTo(sx1, sy1);
    ctx.strokeStyle = PALETTE.inkFaint;
    ctx.lineWidth = 0.8; ctx.stroke();
  }

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

  if (!focusSavedU) drawWallRulers();

  drawWallGridLines();

  drawStepBridgeBars();

  for (const c of state.wallCables) drawCable(c, "wall");
  drawVertexBalloons("wall");
  drawDimensionChain("wall");
  drawNotes("wall");
  drawDrawingPreview("wall");
  drawCrossMarkersFromFloorCables();

  drawStepDimensions();

  if (focusSavedU && focusSeg >= 0) {
    const seg = WALL.segments[focusSeg];
    const tag = (seg && seg.tag) ? seg.tag : ("segment " + (focusSeg + 1));
    ctx.font = "700 11px " + FONT_MONO;
    ctx.fillStyle = PALETTE.ink;
    ctx.textAlign = "left"; ctx.textBaseline = "bottom";
    ctx.fillText("FOCUS  ·  " + tag + "  ·  neighbours shown adjacent",
                 12, layout.wallY + 30);
  }

  if (mouse.inside && mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    const proj = projectOntoWalls(wx, wy);

    /* Which wall is the cursor's nearest neighbour?  Ties are won by
       the first entry in `proj`, which projectOntoWalls returns in
       segment-index order — deterministic enough that the highlight
       does not flicker between two walls at the same distance. */
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

      /* Paper halo, then the dot.  Every qualifying wall gets the
         same base marker; the nearest one is drawn a touch larger
         and in the highlight accent. */
      ctx.beginPath();
      ctx.arc(sx, sy, isNearest ? 5.5 : 4.5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.paper;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(sx, sy, isNearest ? 4 : 3, 0, Math.PI * 2);
      ctx.fillStyle = ink;
      ctx.fill();

      /* Extra outer ring on the nearest one — the "this is the one
         you're actually closest to" cue, the same idiom the ruler
         uses for its locked second endpoint. */
      if (isNearest) {
        ctx.beginPath();
        ctx.arc(sx, sy, 8.5, 0, Math.PI * 2);
        ctx.strokeStyle = PALETTE.highlight;
        ctx.lineWidth = 1.6;
        ctx.setLineDash([3, 2]);
        ctx.stroke();
        ctx.setLineDash([]);
      }

      /* Distance label below the dot.  Paper-backed so it stays
         legible over the void hatch and the arrow runway; mono so
         it reads as a measurement; inked in the same colour as the
         dot it belongs to. */
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

function drawWallRulers() {
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
"""
