"""
pg_view_sheet.py — the drafting-sheet decoration layer.

Everything that makes the two-view canvas look like one sheet rather
than two stacked canvases, plus the hover highlights that ride on top
of the wall and plan bands:

    • the ruled sheet border, registration crosshairs, margin ticks
    • the section ribbon on the divider (PLAN @ z / UNFOLDED WALL)
    • scale-bar padding (the widget itself lives in pg_view_floor)
    • redline notes and their connector leaders
    • the selected vertex's dimension chain
    • the drawing preview: a dashed ghost of the cable being drawn,
      its vertex markers, the snap crosshair, the sticky-anchor ring,
      the merge-target ring
    • the cross-view ghost marker
    • the hover highlights: crosshatch on the wall under the cursor,
      a solid bar on the plan under the cursor, and the tag callout
      near the cursor

This is the layer the eye reads as "the drawing itself" — every mark
it makes is a drafting idiom rather than a UI affordance.
"""


SHEET_VIEW_JS = r"""
/* ==========================================================================
   HOVER ROUTE HIGHLIGHTS
   ==========================================================================
   Three markers, each drawn in its own layer so the cables pass over
   the highlight rather than the reverse:

     crossHatch on the strip's wall under the cursor
     ink bar on the plan under the cursor
     tag callout next to the cursor

   The crosshatch and the bar are drawn inside drawWallView /
   drawFloorView, before the cables.  The callout is drawn last, on
   top of everything, so it never disappears under a cable. */

function drawHoverStripHatch() {
  const hl = state.hoveredRoute;
  if (!hl) return;
  const seg = WALL.segments[hl.segIdx];
  if (!seg || isSegHidden(hl.segIdx)) return;

  const [x0, yTop] = w2sWall(seg.u0, segTop(seg));
  const [x1, yBot] = w2sWall(seg.u1, segBottom(seg));
  ctx.save();
  ctx.fillStyle = _screenPat("crossHatch");
  ctx.fillRect(x0, yTop, x1 - x0, yBot - yTop);
  ctx.restore();
}

function drawHoverPlanBar() {
  const hl = state.hoveredRoute;
  if (!hl) return;
  const seg = WALL.segments[hl.segIdx];
  if (!seg || isSegHidden(hl.segIdx)) return;

  const [ax, ay] = w2sFloor(seg.a[0], seg.a[1]);
  const [bx, by] = w2sFloor(seg.b[0], seg.b[1]);
  ctx.save();
  ctx.lineCap = "round";
  ctx.strokeStyle = PALETTE.paper; ctx.lineWidth = 10;
  ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
  ctx.stroke();
  ctx.strokeStyle = PALETTE.ink; ctx.lineWidth = 5.5;
  ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
  ctx.stroke();
  ctx.restore();
}

function drawHoverCallout() {
  const hl = state.hoveredRoute;
  if (!hl || drawing) return;
  const seg = WALL.segments[hl.segIdx];
  if (!seg || isSegHidden(hl.segIdx)) return;
  if (mouse.view === null) return;

  const tag = (seg.tag) ? seg.tag : ("seg " + (hl.segIdx + 1));
  const text = tag + "  ·  " + fmtCm(seg.len) + " cm"
             + "  ·  h " + fmtCm(segBottom(seg)) + "–" + fmtCm(segTop(seg));

  let mx, my;
  if (mouse.view === "floor") {
    [mx, my] = w2sFloor((seg.a[0] + seg.b[0]) / 2,
                        (seg.a[1] + seg.b[1]) / 2);
  } else {
    [mx, my] = w2sWall((seg.u0 + seg.u1) / 2,
                       (segBottom(seg) + segTop(seg)) / 2);
  }

  ctx.save();
  ctx.font = "600 11px " + FONT_MONO;
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  const tw = ctx.measureText(text).width;
  const pw = tw + 16;
  const ph = 20;
  let px = mx + 22;
  let py = my - 30;
  const cw = window.innerWidth;
  if (px + pw > cw - SHEET_MARGIN - 8) px = mx - 22 - pw;
  if (py - ph / 2 < SHEET_MARGIN + 8) py = SHEET_MARGIN + 8 + ph / 2;

  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(px, py - ph / 2, pw, ph);
  ctx.strokeStyle = PALETTE.ink;
  ctx.lineWidth = 0.9;
  ctx.strokeRect(px + 0.4, py - ph / 2 + 0.4, pw - 0.8, ph - 0.8);

  ctx.fillStyle = PALETTE.ink;
  ctx.fillText(text, px + 8, py + 0.5);
  ctx.restore();
}

/* ==========================================================================
   SHEET FRAME, MARGIN TICKS, REGISTRATION MARKS
   ========================================================================== */

function drawSheetFrame(cw, ch) {
  ctx.save();
  ctx.strokeStyle = PALETTE.ink;
  ctx.lineWidth = 0.8;
  ctx.strokeRect(SHEET_MARGIN + 0.4, SHEET_MARGIN + 0.4,
                 cw - 2 * SHEET_MARGIN - 0.8,
                 ch - 2 * SHEET_MARGIN - 0.8);
  ctx.restore();
  drawMarginTicks(cw, ch);
  drawRegistrationMark(SHEET_MARGIN + 4, SHEET_MARGIN + 4);
  drawRegistrationMark(cw - SHEET_MARGIN - 4, SHEET_MARGIN + 4);
  drawRegistrationMark(SHEET_MARGIN + 4, ch - SHEET_MARGIN - 4);
  drawRegistrationMark(cw - SHEET_MARGIN - 4, ch - SHEET_MARGIN - 4);
}

function drawRegistrationMark(x, y) {
  ctx.save();
  ctx.strokeStyle = PALETTE.inkFaint;
  ctx.lineWidth = 0.8;
  const r = REG_CROSS_R;
  ctx.beginPath();
  ctx.moveTo(x - r, y); ctx.lineTo(x + r, y);
  ctx.moveTo(x, y - r); ctx.lineTo(x, y + r);
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(x, y, r * 0.55, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawMarginTicks(cw, ch) {
  const b = GEOMETRY.bounds;

  const pxPerMmX = viewFloor.scale;
  let stepX = 1000;
  while (stepX * pxPerMmX < 40) stepX *= 2;

  ctx.save();
  ctx.font = "600 9px " + FONT_MONO;
  ctx.fillStyle = PALETTE.inkFaint;
  ctx.strokeStyle = PALETTE.inkFaint;
  ctx.lineWidth = 0.8;

  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  const x0 = Math.ceil(b.minX / stepX) * stepX;
  for (let wx = x0; wx <= b.maxX; wx += stepX) {
    const [sx] = w2sFloor(wx, 0);
    if (sx < SHEET_MARGIN + 16 || sx > cw - SHEET_MARGIN - 16) continue;
    /* Skip ticks under the scale bar (top-right of the plan band). */
    if (sx > cw - SHEET_MARGIN - 260 && sx < cw - SHEET_MARGIN - 10) continue;
    const y = SHEET_MARGIN;
    ctx.beginPath();
    ctx.moveTo(sx, y); ctx.lineTo(sx, y + MARGIN_TICK_LEN);
    ctx.stroke();
    ctx.fillText(String(Math.round(wx / 100)), sx, y + MARGIN_TICK_LEN + 1);
  }

  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const pyPerMmY = viewFloor.scale;
  let stepY = 1000;
  while (stepY * pyPerMmY < 40) stepY *= 2;
  const y0 = Math.ceil(b.minY / stepY) * stepY;
  for (let wy = y0; wy <= b.maxY; wy += stepY) {
    const [, sy] = w2sFloor(0, wy);
    if (sy < SHEET_MARGIN + 16) continue;
    if (sy > layout.dividerY - 12) continue;
    const x = SHEET_MARGIN;
    ctx.beginPath();
    ctx.moveTo(x, sy); ctx.lineTo(x + MARGIN_TICK_LEN, sy);
    ctx.stroke();
    ctx.fillText(String(Math.round(wy / 100)),
                 x + MARGIN_TICK_LEN + 1, sy);
  }
  ctx.restore();
}

/* ==========================================================================
   SECTION RIBBON
   ==========================================================================
   The ribbon at the divider names the two bands and shows their
   current zoom, if it is not 1:1.  It is drawn twice — once for the
   plan, once for the wall — so the two captions never overlap even
   when the canvas is narrow. */

function drawSectionRibbon(cw) {
  const yMid = layout.dividerY + 1;
  const bandH = RIBBON_BAND_H;
  const yTop = yMid - bandH / 2;
  const pad = 14;

  ctx.save();
  ctx.font = "600 10px " + FONT_MONO;
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";

  const leftText  = "PLAN  @  " + Math.round(GEOMETRY.cutZ) + " mm"
                  + (Math.abs(viewFloor.zoom - 1) > 0.02
                     ? "  ·  " + Math.round(viewFloor.zoom * 100) + " %" : "");
  const leftW     = ctx.measureText(leftText).width;
  const rightText = "UNFOLDED WALL  ·  PERIMETER  " +
                    Math.round(WALL.totalU) + " mm"
                  + (Math.abs(viewWall.zoom - 1) > 0.02
                     ? "  ·  " + Math.round(viewWall.zoom * 100) + " %" : "");
  const rightW    = ctx.measureText(rightText).width;

  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(0, yTop, leftW + 2 * pad, bandH);

  const rx = cw - rightW - 2 * pad;
  ctx.fillRect(rx, yTop, rightW + 2 * pad, bandH);

  ctx.fillStyle = PALETTE.ink;
  ctx.fillText(leftText, pad, yMid + 0.5);
  ctx.fillText(rightText, rx + pad, yMid + 0.5);

  ctx.restore();
}

/* ==========================================================================
   NOTES / REDLINES
   ==========================================================================
   Session-scoped notes anchored to a world point in either view.
   Each note is a paper-backed label hung off a diagonal leader at
   -45°, with a small ring at the anchor.  Text is truncated to a
   maximum width; a note that outgrows its box keeps its head and
   loses its tail with an ellipsis. */

const NOTE_MAX_W = 220;

function drawNotes(view) {
  if (!NOTES.length) return;
  ctx.save();
  ctx.font = "600 11px " + FONT_MONO;
  ctx.textBaseline = "middle";

  for (const note of NOTES) {
    if (note.space !== view) continue;
    let sx, sy;
    if (view === "floor") {
      [sx, sy] = w2sFloor(note.x, note.y);
    } else {
      [sx, sy] = w2sWall(note.u, note.v);
    }

    let text = note.text;
    while (ctx.measureText(text).width + 16 > NOTE_MAX_W &&
           text.length > 6) {
      text = text.slice(0, -2);
    }
    if (text !== note.text) text = text + "…";

    const tw = ctx.measureText(text).width;
    const pw = tw + 14;
    const ph = 18;
    const angle = -Math.PI / 4;
    const dist = 30;
    const px = sx + Math.cos(angle) * dist;
    const py = sy + Math.sin(angle) * dist;

    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 0.9;
    ctx.beginPath();
    ctx.moveTo(sx, sy);
    ctx.lineTo(px, py);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(sx, sy, 3.6, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.paper; ctx.fill();
    ctx.strokeStyle = PALETTE.ink; ctx.lineWidth = 1.2; ctx.stroke();

    ctx.fillStyle = PALETTE.paper;
    ctx.fillRect(px - 4, py - ph / 2, pw, ph);
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 0.9;
    ctx.strokeRect(px - 4 + 0.4, py - ph / 2 + 0.4, pw - 0.8, ph - 0.8);

    ctx.fillStyle = PALETTE.ink;
    ctx.textAlign = "left";
    ctx.fillText(text, px + 3, py + 0.5);
  }
  ctx.restore();
}

function addNote() {
  if (!mouse.inside || !mouse.view) return;
  const text = prompt("Note:");
  if (!text || !text.trim()) return;
  if (mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    NOTES.push({ space: "floor", x: wx, y: wy, text: text.trim() });
  } else {
    const [u, v] = s2wWall(mouse.sx, mouse.sy);
    NOTES.push({ space: "wall", u, v, text: text.trim() });
  }
  draw();
}

/* ==========================================================================
   DIMENSION CHAIN ON SELECTION
   ==========================================================================
   When a wall-edge vertex is selected, a short dimension chain runs
   from it to each of its neighbours on the same segment.  The chain
   is offset eighteen screen px perpendicular to the segment, with
   forty-five-degree tick marks and a value pill at each midpoint. */

function drawDimensionChain(view) {
  const sv = state.selectedVertex;
  if (!sv) return;
  const sel = anchors.get(sv.cable.anchorIds[sv.index]);
  if (!sel || sel.space !== "wall-edge") return;

  const sibs = [];
  for (const a of anchors.values()) {
    if (a.space !== "wall-edge") continue;
    if (a.segIdx !== sel.segIdx) continue;
    sibs.push(a);
  }
  if (sibs.length < 2) return;
  sibs.sort((a, b) => a.t - b.t);
  const idx = sibs.findIndex(a => a.id === sel.id);
  if (idx < 0) return;
  const neighbours = [];
  if (idx > 0) neighbours.push(sibs[idx - 1]);
  if (idx < sibs.length - 1) neighbours.push(sibs[idx + 1]);
  if (!neighbours.length) return;

  const seg = WALL.segments[sel.segIdx];
  if (!seg) return;

  const toScreen = (view === "floor") ? w2sFloor : w2sWall;
  const project  = (view === "floor") ? anchorPlan : anchorWall;

  for (const nb of neighbours) {
    const ps = project(sel), pn = project(nb);
    if (!ps || !pn) continue;
    const [sx0, sy0] = toScreen(ps[0], ps[1]);
    const [sx1, sy1] = toScreen(pn[0], pn[1]);

    const dx = sx1 - sx0, dy = sy1 - sy0;
    const len = Math.hypot(dx, dy);
    if (len < 4) continue;
    const nx = -dy / len, ny = dx / len;
    const off = 18;
    const ox = nx * off, oy = ny * off;

    const qx0 = sx0 + ox, qy0 = sy0 + oy;
    const qx1 = sx1 + ox, qy1 = sy1 + oy;

    ctx.save();
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 0.7;

    ctx.beginPath();
    ctx.moveTo(sx0, sy0); ctx.lineTo(qx0, qy0);
    ctx.moveTo(sx1, sy1); ctx.lineTo(qx1, qy1);
    ctx.moveTo(qx0, qy0); ctx.lineTo(qx1, qy1);
    ctx.stroke();

    const T = DIM_TICK_LEN;
    const ux = dx / len, uy = dy / len;
    const t1x = (ux + nx) / Math.SQRT2, t1y = (uy + ny) / Math.SQRT2;
    const t2x = (ux - nx) / Math.SQRT2, t2y = (uy - ny) / Math.SQRT2;
    for (const [cx, cy] of [[qx0, qy0], [qx1, qy1]]) {
      ctx.beginPath();
      ctx.moveTo(cx - t1x * T, cy - t1y * T);
      ctx.lineTo(cx + t1x * T, cy + t1y * T);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx - t2x * T, cy - t2y * T);
      ctx.lineTo(cx + t2x * T, cy + t2y * T);
      ctx.stroke();
    }
    ctx.restore();

    const du = Math.abs(nb.t - sel.t) * seg.len;
    _drawValuePill((qx0 + qx1) / 2, (qy0 + qy1) / 2, fmtCm(du));
  }
}

/* ==========================================================================
   DRAWING PREVIEW
   ==========================================================================
   The dashed ghost of a cable being drawn, plus the live crosshair
   that tracks the cursor and the several rings the different snap
   states use:

     plain crosshair + a small dot — free cursor, no snap
     a filled dot inside a halo    — a snap spec, but no anchor reuse
     a teal sticky ring            — reuse of an existing anchor
     a translucent teal ring       — a merge target (Alt+click)
     a dashed amber ring           — a ghost marker on the other view

   Only one of these can be active per frame, chosen by the same
   reuse policy addDrawPoint uses, so what the user sees during
   hover is exactly what the next click will commit.

   Snap-candidate feedback
   -----------------------
   drawSnapCandidates shows every anchor the current click would
   reuse, so the user can see what they are about to snap to before
   committing.  Three tiers, from loudest to quietest:

       primary in range     the anchor that would actually be picked
                            — a solid teal ring with a filled centre,
                            the same visual the earlier single-target
                            merge-target preview used.
       secondary in range   other candidates within the reuse
                            threshold — thin teal rings.  They would
                            be picked if the cursor moved closer.
       out of range         candidates the cursor is near but not
                            close enough to actually reuse — faint
                            dashed grey rings.  "Move closer and this
                            becomes selectable."

   The candidate set is exactly what isReusableAnchor(a, mouse.alt,
   view) accepts, minus the anchors already in the current drawing
   (those are the ghost's own vertices, not fresh snap targets).
   The in-range threshold matches ANCHOR_NEARBY_PX and
   findMergeTarget's 14 px, so the visuals never lie about what a
   click will do.

   drawSnapCandidates runs whenever the cursor is over the view it
   is called for — INCLUDING before the first point is placed, when
   drawing.view is still null.  The earlier design gated the whole
   preview on drawing.view === view, which meant no snap feedback
   appeared until after the first click; that gate is now split,
   with the ghost line still gated but the candidate feedback not.

   The old single-target block that used findMergeTarget is gone —
   drawSnapCandidates covers that case and shows the full candidate
   set at once.  findMergeTarget itself is unchanged and still used
   by addDrawPoint; only the preview stopped calling it. */

/* Reuse thresholds, in SCREEN pixels.  SNAP_IN_RANGE_PX matches
   ANCHOR_NEARBY_PX in pg_core and findMergeTarget's own 14; a click
   at or under this distance will reuse the anchor.  SNAP_VISIBLE_PX
   is the outer halo where a ring is still drawn but the click would
   not reuse — the "almost, move closer" band. */
const SNAP_IN_RANGE_PX = 14;
const SNAP_VISIBLE_PX  = 26;

function drawSnapCandidates(view) {
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor   : w2sWall;

  const candidates = [];
  for (const a of anchors.values()) {
    if (view === "wall" && a.space === "wall-edge" && isSegHidden(a.segIdx))
      continue;
    if (!isReusableAnchor(a, mouse.alt, view)) continue;
    if (drawing.anchorIds.indexOf(a.id) >= 0) continue;

    const p = project(a);
    if (!p) continue;
    const [sx, sy] = toScreen(p[0], p[1]);
    const d = Math.hypot(sx - mouse.sx, sy - mouse.sy);
    if (d > SNAP_VISIBLE_PX) continue;

    candidates.push({ sx, sy, d });
  }
  if (!candidates.length) return;

  candidates.sort((x, y) => x.d - y.d);
  const primary = candidates[0];

  ctx.save();
  ctx.lineCap = "round";

  /* Secondary rings, drawn first so the primary overlays them. */
  for (let i = candidates.length - 1; i >= 1; i--) {
    const c = candidates[i];
    const inRange = c.d <= SNAP_IN_RANGE_PX;
    ctx.beginPath();
    ctx.arc(c.sx, c.sy, 8, 0, Math.PI * 2);
    if (inRange) {
      ctx.strokeStyle = PALETTE.transitSoft;
      ctx.lineWidth = 1.4;
      ctx.setLineDash([]);
    } else {
      ctx.strokeStyle = PALETTE.inkFaint;
      ctx.lineWidth = 1.0;
      ctx.setLineDash([2, 2]);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  /* Primary ring — solid teal when in range, dashed grey when the
     cursor is still outside the reuse threshold.  The centre dot
     only appears when the click would actually reuse this anchor,
     so "does clicking do anything" is answerable at a glance. */
  const inRange = primary.d <= SNAP_IN_RANGE_PX;
  const ringR   = inRange ? 12 : 10;
  ctx.beginPath();
  ctx.arc(primary.sx, primary.sy, ringR, 0, Math.PI * 2);
  if (inRange) {
    ctx.strokeStyle = PALETTE.transit;
    ctx.lineWidth = 2.6;
    ctx.setLineDash([]);
  } else {
    ctx.strokeStyle = PALETTE.inkSoft;
    ctx.lineWidth = 1.8;
    ctx.setLineDash([3, 3]);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  if (inRange) {
    ctx.beginPath();
    ctx.arc(primary.sx, primary.sy, 5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.transitFaint;
    ctx.fill();
  }

  ctx.restore();
}

function drawDrawingPreview(view) {
  if (!drawing) return;

  /* Snap-candidate feedback fires whenever the cursor is over the
     view this call is for, regardless of whether the drawing owns
     that view yet.  Two cases:

       drawing.view === null   no first point placed; the cursor
                               could go to either band, so whichever
                               band the cursor is over shows its
                               candidates.
       drawing.view === view   drawing locked to this band; show its
                               candidates.

     Any other combination means the drawing is committed to the
     other band and the cursor is here — a click would be rejected
     by addDrawPoint, so no rings are shown. */
  const candidateViewOK =
    (drawing.view === null) || (drawing.view === view);
  if (candidateViewOK && mouse.inside && mouse.view === view) {
    drawSnapCandidates(view);
  }

  if (drawing.view !== view) return;

  const toScreen = (view === "floor") ? w2sFloor : w2sWall;
  const s2w      = (view === "floor") ? s2wFloor : s2wWall;
  const project  = (view === "floor") ? anchorPlan : anchorWall;

  const visAnchor = (a) => {
    if (!a) return false;
    if (view !== "wall") return true;
    if (a.space !== "wall-edge") return true;
    return !isSegHidden(a.segIdx);
  };

  if (drawing.anchorIds.length > 0) {
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 2.2;
    ctx.setLineDash([6, 4]);
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    let started = false;
    let lastA = null;
    const jumps = [];
    for (const id of drawing.anchorIds) {
      const a = anchors.get(id);
      if (!visAnchor(a)) { started = false; lastA = null; continue; }
      const p = a ? project(a) : null;
      if (!p) { started = false; lastA = null; continue; }
      const [sx, sy] = toScreen(p[0], p[1]);
      if (started && lastA && isHopTransition(lastA, a)) {
        const lp = project(lastA);
        const [lx, ly] = toScreen(lp[0], lp[1]);
        jumps.push({ fromX: lx, fromY: ly, toX: sx, toY: sy });
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(sx, sy);
      } else if (!started) {
        ctx.moveTo(sx, sy);
      } else {
        ctx.lineTo(sx, sy);
      }
      started = true;
      lastA = a;
    }
    ctx.stroke();
    ctx.setLineDash([]);
    for (const j of jumps) {
      drawHopArc(j.fromX, j.fromY, j.toX, j.toY, false);
    }

    for (const id of drawing.anchorIds) {
      const a = anchors.get(id);
      if (!visAnchor(a)) continue;
      const p = a ? project(a) : null;
      if (!p) continue;
      const [vx, vy] = toScreen(p[0], p[1]);
      const isStep = anchorIsStepFace(a);
      const side   = anchorSide(a);

      ctx.beginPath();
      ctx.arc(vx, vy, (isStep ? 6 : 5) + 2, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.paper;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(vx, vy, isStep ? 6 : 5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.ink;
      ctx.fill();
      ctx.strokeStyle = PALETTE.highlight;
      ctx.lineWidth = 1.8;
      ctx.stroke();

      if (isStep) {
        ctx.beginPath();
        ctx.arc(vx, vy, 8.5, 0, Math.PI * 2);
        ctx.strokeStyle = (side === "top") ? PALETTE.step : PALETTE.transit;
        ctx.lineWidth = 1.4;
        ctx.stroke();
      }
    }
  }

  if (drawingPreview) {
    const [pwx, pwy] = s2w(drawingPreview.sx, drawingPreview.sy);

    let sticky   = null;
    let snapSpec = null;

    if (snapEnabled || mouse.alt) {
      const reuseFilter = (a) => isReusableAnchor(a, mouse.alt, view);
      sticky = findAnchorAtScreen(view, drawingPreview.sx, drawingPreview.sy,
                                  reuseFilter);

      if (!sticky) {
        snapSpec = (view === "floor")
          ? snapFloorSpec(pwx, pwy, true)
          : snapWallSpec(pwx, pwy, true);

        if (snapSpec && snapSpec.space === "wall-edge") {
          const scale = (view === "floor")
            ? viewFloor.scale
            : Math.min(viewWall.scaleX, viewWall.scaleY);
          const tolMm = ANCHOR_NEARBY_PX / scale;
          const s = WALL.segments[snapSpec.segIdx];
          if (s) {
            for (const a of anchors.values()) {
              if (a.space !== "wall-edge") continue;
              if (a.segIdx !== snapSpec.segIdx) continue;
              if (!isReusableAnchor(a, mouse.alt, view)) continue;
              if (Math.abs(a.v - snapSpec.v) > tolMm) continue;
              if (Math.abs(a.t - snapSpec.t) * s.len < tolMm) {
                sticky = a; snapSpec = null; break;
              }
            }
          }
        }
      }
    }

    let showX = pwx, showY = pwy;
    let show = true;
    if (sticky) {
      const p = project(sticky);
      if (p) { showX = p[0]; showY = p[1]; }
    } else if (snapSpec) {
      if (snapSpec.space === "floor") {
        showX = snapSpec.x; showY = snapSpec.y;
      } else if (snapSpec.space === "wall-edge") {
        const [px, py] = wallAttachToPlan(snapSpec.segIdx, snapSpec.t);
        if (view === "floor") { showX = px; showY = py; }
        else {
          showX = wallAttachToU(snapSpec.segIdx, snapSpec.t);
          showY = snapSpec.v;
        }
      }
    } else if (!snapEnabled && !mouse.alt && view === "wall") {
      const attach = uToWallAttach(pwx, pwy);
      if (attach) {
        showX = wallAttachToU(attach.segIdx, attach.t);
        showY = pwy;
      } else {
        show = false;
      }
    }
    const [px, py] = show ? toScreen(showX, showY) : [0, 0];

    if (show) {
      if (drawing.anchorIds.length > 0) {
        const lastId = drawing.anchorIds[drawing.anchorIds.length - 1];
        const lastA  = anchors.get(lastId);
        if (visAnchor(lastA)) {
          const lastP  = lastA ? project(lastA) : null;
          if (lastP) {
            const [lx, ly] = toScreen(lastP[0], lastP[1]);
            ctx.beginPath();
            ctx.moveTo(lx, ly); ctx.lineTo(px, py);
            ctx.setLineDash([6, 4]);
            ctx.strokeStyle = sticky ? PALETTE.transit
                            : (snapEnabled || mouse.alt) ? PALETTE.highlight
                            : PALETTE.inkSoft;
            ctx.lineWidth = 1.8;
            ctx.stroke(); ctx.setLineDash([]);
          }
        }
      }

      drawSnapCrosshair(px, py);

      ctx.beginPath();
      ctx.arc(px, py, sticky ? 6 : (snapSpec ? 5 : 4), 0, Math.PI * 2);
      ctx.fillStyle = sticky ? PALETTE.transit
                    : snapSpec ? PALETTE.highlight
                    : (snapEnabled || mouse.alt) ? PALETTE.ink
                    : PALETTE.inkSoft;
      ctx.fill();
      ctx.strokeStyle = PALETTE.paper;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      if (sticky) {
        ctx.beginPath();
        ctx.arc(px, py, 12, 0, Math.PI * 2);
        ctx.strokeStyle = PALETTE.transit;
        ctx.lineWidth = 2;
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }
}

function drawCrossViewGhosts() {
  if (!drawing || !drawingPreview) return;
  if (drawing.view === null) return;
  if (!snapEnabled) return;
  const view = drawing.view;
  const s2w = (view === "floor") ? s2wFloor : s2wWall;
  const [pwx, pwy] = s2w(drawingPreview.sx, drawingPreview.sy);
  if (view === "floor") {
    const wall = findWallAttachSpec(pwx, pwy);
    if (!wall) return;
    const [gx, gy] = w2sWall(wallAttachToU(wall.segIdx, wall.t), wall.v);
    drawGhostMarker(gx, gy);
  } else {
    const edge = findWallEdgeSnapSpec(pwx, pwy);
    if (!edge) return;
    const [px, py] = wallAttachToPlan(edge.segIdx, edge.t);
    const [gx, gy] = w2sFloor(px, py);
    drawGhostMarker(gx, gy);
  }
}

function drawGhostMarker(sx, sy) {
  ctx.save();
  ctx.beginPath();
  ctx.arc(sx, sy, 8, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.highlightBg;
  ctx.fill();
  ctx.strokeStyle = PALETTE.highlight;
  ctx.lineWidth = 1.6;
  ctx.setLineDash([3, 3]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.arc(sx, sy, 2.6, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.highlight;
  ctx.fill();
  ctx.restore();
}
"""
