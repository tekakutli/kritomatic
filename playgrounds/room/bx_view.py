"""
bx_view.py — plan-band rendering.

Everything the top half of the sheet draws.  Static content (walls,
steps) is drawn on every frame but the geometry itself is trivial, so
there is no offscreen cache — a floor plan with ~20 faces and ~100
boxes is well under any frame budget on modern hardware.

The three interactive accents are wired to the palette:

    valid box         transit (cyan)
    overlapping box   step (magenta)
    invalid box       inkSoft (grey dashed)
    selected box      highlight (yellow) thick outline
    ghost OK          highlight (yellow) translucent
    ghost invalid     step (magenta) translucent

Handles
-------
A selected box exposes three handle kinds, drawn by drawHandles:

    corner      small filled square at each box corner.
                Dragging one resizes BOTH axes, pinning the
                diagonally opposite corner.
    edge        small filled square at each edge midpoint.
                Dragging one resizes ONE axis, pinning the
                opposite edge.
    rotate      a single floating circle above the box's top edge,
                connected by a dashed leader line.  Dragging it spins
                the box about its own centre.

The rotation handle's position comes from rotateHandleScreenPos in
bx_core — the same function the hit test uses — so the handle the user
sees is exactly the handle the hit test finds.

Ghost previews
--------------
The ghost — the "phantom" box that follows the cursor in draw-box
mode — comes in two flavours with distinctly different visual
weights:

    preset ghost    the box a plain click would place: current
                    W / H / Rot from the GUIDES strip, centred on
                    the cursor.  Drawn at GHOST_ALPHA_PRESET — a
                    very low alpha, because it is a suggestion about
                    what a click would do, not an action in progress.
                    A user can see it and glance past it; it must not
                    compete with the drawing underneath.

    drag ghost      the rectangle the cursor is actively defining:
                    the down point is one corner, the cursor the
                    opposite one, axis-aligned.  Drawn at
                    GHOST_ALPHA_DRAG — noticeably stronger, because
                    it is a concrete gesture the user is completing
                    right now and wants to see clearly.

Both ghosts wrap their fill, paper backing, and outline in a single
globalAlpha so the whole shape fades together.  The collision pill is
drawn AFTER restoring the alpha, so the "✗ collision" warning stays
at full opacity even when its shape does not.

Translation
-----------
The two on-canvas labels this module writes — the invalid-box chip
and the ghost's collision pill — come from T() in bx_i18n.py, so
they follow the playground's language switch alongside the panel.
"""


VIEW_JS = r"""
/* ==========================================================================
   PATH BUILDERS
   ========================================================================== */

function pathFace(face) {
  ctx.beginPath();
  const o = face.outer;
  if (!o.length) return;
  let [x0, y0] = w2s(o[0][0], o[0][1]);
  ctx.moveTo(x0, y0);
  for (let i = 1; i < o.length; i++) {
    const [x, y] = w2s(o[i][0], o[i][1]);
    ctx.lineTo(x, y);
  }
  ctx.closePath();
  for (const h of face.holes) {
    if (!h.length) continue;
    [x0, y0] = w2s(h[0][0], h[0][1]);
    ctx.moveTo(x0, y0);
    for (let i = 1; i < h.length; i++) {
      const [x, y] = w2s(h[i][0], h[i][1]);
      ctx.lineTo(x, y);
    }
    ctx.closePath();
  }
}

function pathBox(b) {
  const c = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  ctx.beginPath();
  let [sx, sy] = w2s(c[0][0], c[0][1]);
  ctx.moveTo(sx, sy);
  for (let i = 1; i < 4; i++) {
    [sx, sy] = w2s(c[i][0], c[i][1]);
    ctx.lineTo(sx, sy);
  }
  ctx.closePath();
}

/* ==========================================================================
   BOX SHAPES AND LABELS
   ========================================================================== */

function drawBoxShape(b, stroke, fill, lw, dashed) {
  pathBox(b);
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) {
    if (dashed) ctx.setLineDash([7, 5]);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = lw || 2;
    ctx.stroke();
    if (dashed) ctx.setLineDash([]);
  }
}

function drawBoxLabel(b, faded) {
  if (!b.name) return;
  const c = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const sc = c.map(([x, y]) => w2s(x, y));
  const screenW = Math.abs(sc[1][0] - sc[0][0]) + Math.abs(sc[2][0] - sc[1][0]);
  const screenH = Math.abs(sc[3][1] - sc[0][1]) + Math.abs(sc[2][1] - sc[1][1]);
  const minDim = Math.min(screenW, screenH);
  const fontSize = Math.max(8, Math.min(16, minDim * 0.13));
  if (fontSize < 8) return;

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(sc[0][0], sc[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(sc[i][0], sc[i][1]);
  ctx.closePath();
  ctx.clip();

  const [cxs, cys] = w2s(b.x, b.y);
  let tRot = b.rot;
  while (tRot >  90) tRot -= 180;
  while (tRot < -90) tRot += 180;

  ctx.translate(cxs, cys);
  ctx.rotate(-tRot * Math.PI / 180);
  ctx.fillStyle = faded ? PALETTE.inkSoft : PALETTE.ink;
  ctx.font = `600 ${fontSize.toFixed(1)}px ${FONT_MONO}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(b.name, 0, 0);
  ctx.restore();
}

function drawInvalidChip(b) {
  const sc = boxCorners(b.x, b.y, b.w, b.h, b.rot).map(([x, y]) => w2s(x, y));
  const minY = Math.min(sc[0][1], sc[1][1], sc[2][1], sc[3][1]);
  const cxs = (sc[0][0] + sc[1][0] + sc[2][0] + sc[3][0]) / 4;
  const label = T("chipInvalid");
  ctx.save();
  ctx.translate(cxs, minY - 11);
  ctx.font = "700 10px " + FONT_MONO;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const tw = ctx.measureText(label).width;
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(-tw / 2 - 6, -9, tw + 12, 18);
  ctx.strokeStyle = PALETTE.step;
  ctx.lineWidth = 1;
  ctx.strokeRect(-tw / 2 - 6 + 0.5, -9 + 0.5, tw + 12 - 1, 18 - 1);
  ctx.fillStyle = PALETTE.step;
  ctx.fillText(label, 0, 0);
  ctx.restore();
}

/* ==========================================================================
   HANDLES
   ==========================================================================
   Three visual kinds, ordered least-intrusive to most:

     edge        8 × 8 square, thin outline — one-axis resize
     corner     11 × 11 square, thicker outline — two-axis resize
     rotate     18 px circle with a ↻ glyph, floating above the top
                 edge, connected by a dashed leader line

   Corner squares are deliberately larger than edge squares: a bigger
   handle reads as "this does more" without any caption, and a user
   reaching for a two-axis resize almost always goes for the corner
   first. */

function drawHandles(b) {
  const corners = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const edges   = boxEdgeMidpoints(b.x, b.y, b.w, b.h, b.rot);

  /* Edge handles — smaller squares, resize one axis. */
  for (let i = 0; i < 4; i++) {
    const [sx, sy] = w2s(edges[i][0], edges[i][1]);
    ctx.beginPath();
    ctx.rect(sx - 4, sy - 4, 8, 8);
    ctx.fillStyle = PALETTE.paper; ctx.fill();
    ctx.strokeStyle = PALETTE.highlight; ctx.lineWidth = 1.6; ctx.stroke();
  }

  /* Corner handles — larger squares, resize both axes at once. */
  for (let i = 0; i < 4; i++) {
    const [sx, sy] = w2s(corners[i][0], corners[i][1]);
    ctx.beginPath();
    ctx.rect(sx - 5.5, sy - 5.5, 11, 11);
    ctx.fillStyle = PALETTE.paper; ctx.fill();
    ctx.strokeStyle = PALETTE.highlight; ctx.lineWidth = 2; ctx.stroke();
  }

  /* Rotation handle — a floating circle above the box's top edge,
     connected by a dashed leader line.  The offset is in screen
     pixels (see rotateHandleWorldPos in bx_core), so the handle sits
     the same distance from the box edge at every zoom level. */
  const r = b.rot * Math.PI / 180;
  const topCx = b.x - Math.sin(r) * (b.h / 2);
  const topCy = b.y + Math.cos(r) * (b.h / 2);
  const [tcx, tcy] = w2s(topCx, topCy);
  const [rhx, rhy] = rotateHandleScreenPos(b);

  ctx.save();

  ctx.beginPath();
  ctx.moveTo(tcx, tcy);
  ctx.lineTo(rhx, rhy);
  ctx.strokeStyle = PALETTE.highlight;
  ctx.lineWidth = 1.4;
  ctx.setLineDash([3, 3]);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.beginPath();
  ctx.arc(rhx, rhy, ROTATE_HANDLE_R, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.paper;
  ctx.fill();
  ctx.strokeStyle = PALETTE.highlight;
  ctx.lineWidth = 1.8;
  ctx.stroke();

  /* ↻ glyph inside the circle.  U+21BB is well-supported in every
     monospace stack the playground falls back to. */
  ctx.font = "700 12px " + FONT_MONO;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = PALETTE.highlight;
  ctx.fillText("\u21BB", rhx, rhy + 0.5);

  ctx.restore();
}

/* ==========================================================================
   GHOST
   ==========================================================================
   Two alphas, deliberately different:

     GHOST_ALPHA_PRESET   the box that follows the cursor in draw-box
                          mode.  A suggestion, not an action — drawn
                          at a very low alpha so it can be seen but
                          does not compete with the drawing beneath.
     GHOST_ALPHA_DRAG     the rectangle the user is actively drawing.
                          An action in progress — drawn noticeably
                          stronger so the user can see exactly what
                          they are about to commit.

   The whole shape (fill, paper backing, outline) is drawn under one
   globalAlpha; the collision pill is drawn after restoring the
   alpha, so a warning stays full-strength even when its shape is
   faint. */

const GHOST_ALPHA_PRESET = 0.15;
const GHOST_ALPHA_DRAG   = 0.40;

function drawGhost(b, invalid, alpha) {
  const stroke = invalid ? PALETTE.step : PALETTE.highlight;
  const fill   = invalid
    ? "rgba(255, 43, 214, 0.18)"
    : "rgba(255, 230, 0, 0.18)";

  ctx.save();
  ctx.globalAlpha = alpha;

  pathBox(b);
  ctx.fillStyle = fill;
  ctx.fill();

  pathBox(b);
  ctx.strokeStyle = PALETTE.paper;
  ctx.lineWidth = 6;
  ctx.stroke();

  pathBox(b);
  if (invalid) ctx.setLineDash([7, 5]);
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2.2;
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.restore();

  if (invalid) {
    const sc = boxCorners(b.x, b.y, b.w, b.h, b.rot).map(([x, y]) => w2s(x, y));
    const edgeW = Math.hypot(sc[1][0] - sc[0][0], sc[1][1] - sc[0][1]);
    const edgeH = Math.hypot(sc[3][0] - sc[0][0], sc[3][1] - sc[0][1]);
    if (Math.min(edgeW, edgeH) > 46) {
      const [cx, cy] = w2s(b.x, b.y);
      _drawValuePill(cx, cy, T("ghostCollision"), PALETTE.step);
    }
  }
}

/* ==========================================================================
   OVERLAP FLAGS
   ========================================================================== */

function computeOverlapFlags(validFlags) {
  const n = placedBoxes.length;
  const flags = new Array(n).fill(false);
  const corners = placedBoxes.map(b =>
    boxCorners(b.x, b.y, b.w, b.h, b.rot));
  for (let i = 0; i < n; i++) {
    if (!validFlags[i]) continue;
    for (let j = i + 1; j < n; j++) {
      if (!validFlags[j]) continue;
      if (polygonsOverlap(corners[i], corners[j])) {
        flags[i] = true;
        flags[j] = true;
      }
    }
  }
  return flags;
}

/* ==========================================================================
   SHEET DECORATION
   ========================================================================== */

function drawSheetFrame(cw, ch) {
  ctx.save();
  ctx.strokeStyle = PALETTE.inkFaint;
  ctx.lineWidth = 0.8;
  ctx.strokeRect(SHEET_MARGIN + 0.4, SHEET_MARGIN + 0.4,
                 cw - 2 * SHEET_MARGIN - 0.8,
                 ch - 2 * SHEET_MARGIN - 0.8);
  ctx.restore();
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

/* ==========================================================================
   MAIN RENDER
   ========================================================================== */

function drawFloorView() {
  const cw = window.innerWidth, ch = window.innerHeight;

  /* Step footprints — light hatch */
  for (const face of GEOMETRY.stepFaces) {
    pathFace(face);
    ctx.fillStyle = _screenPat("stepHatch");
    ctx.fill("evenodd");
    ctx.strokeStyle = PALETTE.stepSoft;
    ctx.lineWidth = 1;
    ctx.setLineDash([7, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  /* Walls and columns — solid ink fill */
  for (const face of GEOMETRY.faces) {
    pathFace(face);
    ctx.fillStyle = PALETTE.ink;
    ctx.fill("evenodd");
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  /* Placed boxes */
  const validFlags = placedBoxes.map(b => !boxIsInvalid(b));
  const overlapFlags = computeOverlapFlags(validFlags);

  for (let i = 0; i < placedBoxes.length; i++) {
    const b = placedBoxes[i];
    const isSel   = (b === selectedBox);
    const isHover = (b === hoveredBox);
    const valid = validFlags[i];
    const ov    = overlapFlags[i];

    let stroke, fill, dashed = false;

    if (!valid) {
      stroke = PALETTE.inkSoft;
      fill   = "rgba(220, 230, 242, 0.05)";
      dashed = true;
    } else if (ov) {
      stroke = PALETTE.step;
      fill   = "rgba(255, 43, 214, 0.18)";
    } else {
      stroke = PALETTE.transit;
      fill   = "rgba(0, 229, 255, 0.14)";
    }

    if (isSel) {
      if (valid && ov)      { stroke = PALETTE.highlight;
                              fill   = "rgba(255, 43, 214, 0.24)"; }
      else if (valid)       { stroke = PALETTE.highlight;
                              fill   = "rgba(0, 229, 255, 0.20)"; }
      else                  { stroke = PALETTE.inkSoft;
                              fill   = "rgba(220, 230, 242, 0.10)"; }
    }
    const lw = isSel ? 3 : (isHover ? 2.6 : 1.8);

    drawBoxShape(b, stroke, fill, lw, dashed);
  }

  /* Labels */
  for (let i = 0; i < placedBoxes.length; i++) {
    drawBoxLabel(placedBoxes[i], !validFlags[i]);
  }

  /* Invalid chips */
  for (let i = 0; i < placedBoxes.length; i++) {
    if (!validFlags[i]) drawInvalidChip(placedBoxes[i]);
  }

  /* Handles on the selected box.  In draw-box mode a selected box
     CAN exist — placing a box inside the mode selects it — so this
     line runs in both modes and the handles are visible in both.
     That visibility is what makes the "click a handle to exit draw
     mode" gesture discoverable. */
  if (selectedBox) drawHandles(selectedBox);

  /* Ghost.  Only in draw-box mode, and only over the canvas. */
  const overHandle = !!hitTestHandle(mouse.sx, mouse.sy);
  const canGhost =
    drawBoxMode &&
    mouse.inside && !panning && !drag && !handleDrag && !overHandle;

  if (canGhost) {
    if (pendingPlace && pendingPlace.drag) {
      const x0 = Math.min(pendingPlace.wx, pendingPlace.dragWx);
      const x1 = Math.max(pendingPlace.wx, pendingPlace.dragWx);
      const y0 = Math.min(pendingPlace.wy, pendingPlace.dragWy);
      const y1 = Math.max(pendingPlace.wy, pendingPlace.dragWy);
      const dw = x1 - x0;
      const dh = y1 - y0;
      if (dw >= 1 && dh >= 1) {
        const dcx = (x0 + x1) / 2;
        const dcy = (y0 + y1) / 2;
        const invalid = boxOverlapsWalls(dcx, dcy, dw, dh, 0);
        drawGhost({ x: dcx, y: dcy, w: dw, h: dh, rot: 0 },
                  invalid, GHOST_ALPHA_DRAG);
      }
    } else {
      const [wx, wy] = s2w(mouse.sx, mouse.sy);
      const { w, h, rot } = getBoxParams();
      const invalid = boxOverlapsWalls(wx, wy, w, h, rot);
      drawGhost({ x: wx, y: wy, w, h, rot },
                invalid, GHOST_ALPHA_PRESET);
    }
  }

  updateStatus(validFlags);
  updateTitleBlock();
}
"""
