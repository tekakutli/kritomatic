"""
bx_view.py — plan-band rendering.

Everything the top half of the sheet draws.  Split into two passes:

    drawStaticScene(target)    paper background, room-interior
                               hatches, step footprints, walls,
                               columns, sheet frame, registration
                               marks.  Nothing here depends on the
                               boxes or on the mouse, so it can be
                               cached (see bx_dispatch's
                               `_ensureStaticLayer`) and reused
                               across many frames.

    drawDynamicScene(target)   the boxes, their labels, the invalid
                               chips, the handles on the selected
                               box, the ghost preview.  Every part
                               depends on the model, the selection,
                               or the cursor, so this pass runs on
                               every paint.

Both functions take a `target` 2D context.  The static pass is
typically called with the offscreen layer's context and the
dynamic pass with the main canvas's context; the compat wrapper
`drawFloorView` calls both against the module-level `ctx` for
callers that want the old one-shot behaviour.

Every drawing primitive that composes the static scene —
`pathFace`, `drawColumnMarkers`, `drawSheetFrame`,
`drawRegistrationMark` — accepts a `target` parameter for exactly
this reason.  The primitives that compose only the dynamic scene
(`pathBox`, `drawBoxShape`, `drawBoxLabel`, `drawInvalidChip`,
`drawHandles`, `drawGhost`) keep the module-level `ctx`; there is
only one caller and it always paints to the main canvas.

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
                    the cursor.  Drawn at GHOST_ALPHA_PRESET.

    drag ghost      the rectangle the cursor is actively defining.
                    Drawn at GHOST_ALPHA_DRAG — noticeably stronger,
                    because it is a concrete gesture the user is
                    completing right now.

The whole shape is drawn under one `globalAlpha`.  The collision
pill is drawn AFTER restoring the alpha, so the "✗ collision" warning
stays at full opacity even when its shape does not.
"""

VIEW_JS = r"""
import {
  ctx, PALETTE, _screenPat, FONT_MONO,
  _roundRect, _drawValuePill, _drawValuePillColored,
  SHEET_MARGIN, REG_CROSS_R,
  ROTATE_HANDLE_R,
} from "./bx_base.js";
import { GEOMETRY } from "./bx_geometry.js";
import {
  board, view, state,
  b2s, boxCorners, boxEdgeMidpoints, rotateHandleScreenPos,
  boxIsInvalid, boxOverlapsWalls, polygonsOverlap,
  setSelected,
} from "./bx_core.js";
import { T } from "./bx_i18n.js";

/* ==========================================================================
   PATH BUILDERS
   ========================================================================== */

function pathFace(target, face) {
  target.beginPath();
  const o = face.outer;
  if (!o.length) return;
  let [x0, y0] = b2s(o[0][0], o[0][1]);
  target.moveTo(x0, y0);
  for (let i = 1; i < o.length; i++) {
    const [x, y] = b2s(o[i][0], o[i][1]);
    target.lineTo(x, y);
  }
  target.closePath();
  for (const h of face.holes) {
    if (!h.length) continue;
    [x0, y0] = b2s(h[0][0], h[0][1]);
    target.moveTo(x0, y0);
    for (let i = 1; i < h.length; i++) {
      const [x, y] = b2s(h[i][0], h[i][1]);
      target.lineTo(x, y);
    }
    target.closePath();
  }
}

function pathBox(b) {
  const c = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  ctx.beginPath();
  let [sx, sy] = b2s(c[0][0], c[0][1]);
  ctx.moveTo(sx, sy);
  for (let i = 1; i < 4; i++) {
    [sx, sy] = b2s(c[i][0], c[i][1]);
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
  const sc = c.map(([x, y]) => b2s(x, y));
  const screenW = Math.abs(sc[1][0] - sc[0][0])
                + Math.abs(sc[2][0] - sc[1][0]);
  const screenH = Math.abs(sc[3][1] - sc[0][1])
                + Math.abs(sc[2][1] - sc[1][1]);
  const minDim = Math.min(screenW, screenH);
  const fontSize = Math.max(8, Math.min(16, minDim * 0.13));
  if (fontSize < 8) return;

  ctx.save();
  ctx.beginPath();
  ctx.moveTo(sc[0][0], sc[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(sc[i][0], sc[i][1]);
  ctx.closePath();
  ctx.clip();

  const [cxs, cys] = b2s(b.x, b.y);
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
  const sc = boxCorners(b.x, b.y, b.w, b.h, b.rot).map(([x, y]) => b2s(x, y));
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
                 edge, connected by a dashed leader line */

function drawHandles(b) {
  const corners = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const edges   = boxEdgeMidpoints(b.x, b.y, b.w, b.h, b.rot);

  /* Edge handles — smaller squares. */
  for (let i = 0; i < 4; i++) {
    const [sx, sy] = b2s(edges[i][0], edges[i][1]);
    ctx.beginPath();
    ctx.rect(sx - 4, sy - 4, 8, 8);
    ctx.fillStyle = PALETTE.paper; ctx.fill();
    ctx.strokeStyle = PALETTE.highlight; ctx.lineWidth = 1.6; ctx.stroke();
  }

  /* Corner handles — larger squares. */
  for (let i = 0; i < 4; i++) {
    const [sx, sy] = b2s(corners[i][0], corners[i][1]);
    ctx.beginPath();
    ctx.rect(sx - 5.5, sy - 5.5, 11, 11);
    ctx.fillStyle = PALETTE.paper; ctx.fill();
    ctx.strokeStyle = PALETTE.highlight; ctx.lineWidth = 2; ctx.stroke();
  }

  /* Rotation handle — a floating circle above the box's top edge,
     connected by a dashed leader line. */
  const r = b.rot * Math.PI / 180;
  const topCx = b.x - Math.sin(r) * (b.h / 2);
  const topCy = b.y + Math.cos(r) * (b.h / 2);
  const [tcx, tcy] = b2s(topCx, topCy);
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

  /* ↻ glyph inside the circle. */
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
                          mode.
     GHOST_ALPHA_DRAG     the rectangle the user is actively drawing. */

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
    const sc = boxCorners(b.x, b.y, b.w, b.h, b.rot).map(([x, y]) => b2s(x, y));
    const edgeW = Math.hypot(sc[1][0] - sc[0][0], sc[1][1] - sc[0][1]);
    const edgeH = Math.hypot(sc[3][0] - sc[0][0], sc[3][1] - sc[0][1]);
    if (Math.min(edgeW, edgeH) > 46) {
      const [cx, cy] = b2s(b.x, b.y);
      _drawValuePillColored(cx, cy, T("ghostCollision"), PALETTE.step);
    }
  }
}

/* ==========================================================================
   OVERLAP FLAGS
   ========================================================================== */

export function computeOverlapFlags(validFlags) {
  const n = board.items.length;
  const flags = new Array(n).fill(false);
  const corners = board.items.map(b =>
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
   STATIC SCENE — paper, plan hatches, steps, walls, columns, sheet frame
   ==========================================================================
   Everything here depends only on the room geometry and on the view
   transform (zoom + pan).  Nothing depends on the model, the
   selection, or the cursor, so this pass can be baked once into an
   offscreen canvas and re-blitted across many frames.  bx_dispatch's
   `_ensureStaticLayer` does exactly that.

   All four primitives that compose the pass — `pathFace`,
   `drawColumnMarkers`, `drawSheetFrame`, `drawRegistrationMark` —
   take a `target` so the bake can happen against the offscreen
   context, not the main one. */

function drawColumnMarkers(target) {
  const cols = GEOMETRY.columns || [];
  if (!cols.length) return;
  for (const col of cols) {
    const [x0, y0, x1, y1] = col.box;
    const [sxa, sya] = b2s(x0, y0);
    const [sxb, syb] = b2s(x1, y1);
    const bx = Math.min(sxa, sxb), by = Math.min(sya, syb);
    const bw = Math.abs(sxb - sxa), bh = Math.abs(syb - sya);

    target.save();

    const cx2 = bx + bw / 2, cy2 = by + bh / 2;
    target.strokeStyle = PALETTE.inkFaint;
    target.lineWidth = 0.8;
    target.setLineDash([6, 3, 1.5, 3]);
    target.beginPath();
    target.moveTo(bx - 6, cy2); target.lineTo(bx + bw + 6, cy2);
    target.stroke();
    target.beginPath();
    target.moveTo(cx2, by - 6); target.lineTo(cx2, by + bh + 6);
    target.stroke();
    target.setLineDash([]);

    target.strokeStyle = PALETTE.inkGuide;
    target.lineWidth = 1.4;
    target.setLineDash([3, 3]);
    target.strokeRect(bx, by, bw, bh);
    target.setLineDash([]);

    target.beginPath();
    target.arc(cx2, cy2, 5, 0, Math.PI * 2);
    target.fillStyle = PALETTE.ink;
    target.fill();
    target.strokeStyle = PALETTE.paper;
    target.lineWidth = 1.6;
    target.stroke();

    target.font = "700 10px " + FONT_MONO;
    target.fillStyle = PALETTE.ink;
    target.textAlign = "left";
    target.textBaseline = "middle";
    target.fillText(col.tag, cx2 + 8, cy2);
    target.restore();
  }
}

export function drawSheetFrame(target, cw, ch) {
  target.save();
  target.strokeStyle = PALETTE.inkFaint;
  target.lineWidth = 0.8;
  target.strokeRect(SHEET_MARGIN + 0.4, SHEET_MARGIN + 0.4,
                    cw - 2 * SHEET_MARGIN - 0.8,
                    ch - 2 * SHEET_MARGIN - 0.8);
  target.restore();
  drawRegistrationMark(target, SHEET_MARGIN + 4, SHEET_MARGIN + 4);
  drawRegistrationMark(target, cw - SHEET_MARGIN - 4, SHEET_MARGIN + 4);
  drawRegistrationMark(target, SHEET_MARGIN + 4, ch - SHEET_MARGIN - 4);
  drawRegistrationMark(target, cw - SHEET_MARGIN - 4, ch - SHEET_MARGIN - 4);
}

export function drawRegistrationMark(target, x, y) {
  target.save();
  target.strokeStyle = PALETTE.inkFaint;
  target.lineWidth = 0.8;
  const r = REG_CROSS_R;
  target.beginPath();
  target.moveTo(x - r, y); target.lineTo(x + r, y);
  target.moveTo(x, y - r); target.lineTo(x, y + r);
  target.stroke();
  target.beginPath();
  target.arc(x, y, r * 0.55, 0, Math.PI * 2);
  target.stroke();
  target.restore();
}

export function drawStaticScene(target) {
  const cw = window.innerWidth, ch = window.innerHeight;

  /* Paper background — the whole pass assumes a clean paper layer. */
  target.fillStyle = PALETTE.paper;
  target.fillRect(0, 0, cw, ch);

  /* Plan interior hatches — one per hole in the room's footprint. */
  for (const face of GEOMETRY.faces) {
    for (const h of face.holes) {
      if (!h.length) continue;
      target.beginPath();
      const [x0, y0] = b2s(h[0][0], h[0][1]);
      target.moveTo(x0, y0);
      for (let i = 1; i < h.length; i++) {
        const [x, y] = b2s(h[i][0], h[i][1]);
        target.lineTo(x, y);
      }
      target.closePath();
      target.fillStyle = _screenPat("planInterior");
      target.fill();
    }
  }

  /* Step footprints — light hatch. */
  for (const face of GEOMETRY.stepFaces) {
    pathFace(target, face);
    target.fillStyle = _screenPat("stepHatch");
    target.fill("evenodd");
    target.strokeStyle = PALETTE.stepSoft;
    target.lineWidth = 1;
    target.setLineDash([7, 5]);
    target.stroke();
    target.setLineDash([]);
  }

  /* Walls and columns — solid ink fill. */
  for (const face of GEOMETRY.faces) {
    pathFace(target, face);
    target.fillStyle = PALETTE.ink;
    target.fill("evenodd");
    target.strokeStyle = PALETTE.ink;
    target.lineWidth = 1;
    target.stroke();
  }
  drawColumnMarkers(target);

  /* Sheet frame and registration marks. */
  drawSheetFrame(target, cw, ch);
}

/* ==========================================================================
   DYNAMIC SCENE — boxes, labels, chips, handles, ghost
   ==========================================================================
   Everything here depends on the model, the selection, or the
   cursor, so it must be repainted on every frame where any of
   those changed.  All drawing primitives used in this pass write
   to the module-level `ctx` (the main canvas); there is only one
   caller and it always paints to the main canvas. */

export function drawDynamicScene(target) {
  /* Placed boxes.  validFlags is O(n) thanks to `boxIsInvalid`'s
     `_invalid` cache; overlapFlags is O(n²) and reruns every frame
     — fine for typical boards (tens of boxes); can be made
     incremental if you start seeing hundreds. */
  const validFlags = board.items.map(b => !boxIsInvalid(b));
  const overlapFlags = computeOverlapFlags(validFlags);

  for (let i = 0; i < board.items.length; i++) {
    const b = board.items[i];
    const isSel   = (i === board.selectedIdx);
    const isHover = (b === state.hoveredBox);
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

  /* Labels. */
  for (let i = 0; i < board.items.length; i++) {
    drawBoxLabel(board.items[i], !validFlags[i]);
  }

  /* Invalid chips. */
  for (let i = 0; i < board.items.length; i++) {
    if (!validFlags[i]) drawInvalidChip(board.items[i]);
  }

  /* Handles on the selected box. */
  if (board.selectedIdx >= 0 && board.selectedIdx < board.items.length) {
    drawHandles(board.items[board.selectedIdx]);
  }

  /* Ghost.  Only in draw-box mode, and only over the canvas. */
  const overHandle = !!hitTestHandleLocal(state.mouse.sx, state.mouse.sy);
  const canGhost =
    state.dragBoxMode &&
    state.mouse.inside && !state.panning && !state.drag &&
    !state.handleDrag && !overHandle;

  if (canGhost) {
    if (state.pendingPlace && state.pendingPlace.drag) {
      const x0 = Math.min(state.pendingPlace.wx, state.pendingPlace.dragWx);
      const x1 = Math.max(state.pendingPlace.wx, state.pendingPlace.dragWx);
      const y0 = Math.min(state.pendingPlace.wy, state.pendingPlace.dragWy);
      const y1 = Math.max(state.pendingPlace.wy, state.pendingPlace.dragWy);
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
      const [wx, wy] = b2s(state.mouse.sx, state.mouse.sy);
      const w = parseFloat(document.getElementById("boxW").value) || 100;
      const h = parseFloat(document.getElementById("boxH").value) || 100;
      let rot = parseFloat(document.getElementById("boxRot").value) || 0;
      rot = ((rot % 360) + 360) % 360;
      const invalid = boxOverlapsWalls(wx, wy, w, h, rot);
      drawGhost({ x: wx, y: wy, w, h, rot },
                invalid, GHOST_ALPHA_PRESET);
    }
  }
}

/* Local handle hit test.  bx_core exports hitTestHandle; this file
   defines its own copy to avoid a bidirectional import between
   bx_core and bx_view at module-load time. */
function hitTestHandleLocal(sx, sy) {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.items.length) {
    return null;
  }
  const b = board.items[board.selectedIdx];
  const corners = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const edges   = boxEdgeMidpoints(b.x, b.y, b.w, b.h, b.rot);
  const [rhx, rhy] = rotateHandleScreenPos(b);
  if (Math.hypot(sx - rhx, sy - rhy) <= 9 + 4) return { type: "rotate" };
  for (let i = 0; i < 4; i++) {
    const [px, py] = b2s(corners[i][0], corners[i][1]);
    if (Math.abs(sx - px) <= 11 && Math.abs(sy - py) <= 11) {
      return { type: "corner", index: i };
    }
  }
  for (let i = 0; i < 4; i++) {
    const [px, py] = b2s(edges[i][0], edges[i][1]);
    if (Math.abs(sx - px) <= 10 && Math.abs(sy - py) <= 10) {
      return { type: "edge", index: i };
    }
  }
  return null;
}

/* ==========================================================================
   COMPAT SHIM
   ==========================================================================
   Callers that want the old "one-shot paint" behaviour can keep
   using drawFloorView(); the rAF-coalesced path in bx_dispatch
   uses the two split passes directly. */

export function drawFloorView() {
  drawStaticScene(ctx);
  drawDynamicScene(ctx);
}
"""
