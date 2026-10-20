"""
bx_edit.py — box lifecycle: place, drag, resize, rotate, delete.

The interaction layer.  Owns:

    • the place-on-empty-floor gesture
    • the wall-sliding body drag (swept path with an x-only / y-only
      fallback)
    • the three handle drags:
        resize-corner   a box corner — resize BOTH axes, pinning the
                        diagonally opposite corner
        resize          a box edge   — resize ONE axis, pinning the
                        opposite edge
        rotate          the floating rotation handle — spin the box
                        about its own centre
    • the selection model — selectedBox, hoveredBox (via `state`)
    • tryApplyBoxChange — the wall gate every mutation passes through
    • tryApplySizeChange — the same gate for the panel's W / H fields,
                           with an asymmetric fallback and a last-
                           resort permissive branch

Nothing here draws; the draw calls are made by bx_dispatch once the
mutation has landed.

Resize-corner math
------------------
For a box with rotation R, the box's own axes in world space are:

    U = (cos R,  sin R)     the width  direction
    V = (-sin R, cos R)     the height direction

Dragging a corner whose index in the box's local frame is `i` pins
the corner at index (i + 2) % 4 — the diagonally opposite one.  The
cursor is projected onto U and V relative to that anchor:

    localU = dot(cursor − anchor, U)
    localV = dot(cursor − anchor, V)

The new width and height are |localU| and |localV|, clamped to
MIN_BOX_MM.  The new centre sits at the midpoint between the anchor
and the dragged corner, computed in the local frame and rotated back
to world.  Because the projection's sign is preserved when the cursor
crosses the anchor, the box can be dragged through the anchor and
flipped — the standard behaviour of every design tool's corner drag.

Two resize gates
----------------
`tryApplyBoxChange` is the strict wall gate: rotation and any other
single-axis mutation that is not a W / H change goes through it, and
it rejects valid→invalid transitions.

`tryApplySizeChange` handles the two size fields specifically.  Its
argument for existing separately is UX, not soundness: growing the
box symmetrically around its centre reaches only half as far as
growing it from a pinned edge, so a user dragging a corner can reach
sizes the panel field cannot.  The fix tries symmetric growth first
(least surprising), then pin-left / pin-right (or pin-top /
pin-bottom), then — as a last resort — allows the box to become
invalid so the user can always set the value they asked for and drag
the box out of the wall afterwards.  The visual language already
supports this: an invalid box renders dashed grey with a warning
chip, so an overreach that ends in a wall is impossible to miss.
"""

EDIT_JS = r"""
import { MIN_BOX_MM } from "./bx_base.js";
import {
  state, board, view,
  b2s, s2b, boxCorners, boxEdgeMidpoints, boxOverlapsWalls,
  boxIsInvalid, invalidateBox, selectedItem,
} from "./bx_core.js";
import { T } from "./bx_i18n.js";

/* ==========================================================================
   WALL-SLIDING BODY DRAG
   ==========================================================================
   Swept path: divide the drag into ~50 mm steps, and at each step
   try the full move; if that collides, try x-only; then y-only; then
   stop. */

function moveBoxSafely(b, targetX, targetY) {
  if (boxIsInvalid(b)) {
    b.x = targetX;
    b.y = targetY;
    invalidateBox(b);
    return;
  }

  const totalDx = targetX - b.x, totalDy = targetY - b.y;
  const dist = Math.hypot(totalDx, totalDy);
  if (dist < 1e-6) return;
  const steps = Math.max(1, Math.ceil(dist / 50));
  const stepDx = totalDx / steps, stepDy = totalDy / steps;
  let moved = false;
  for (let i = 0; i < steps; i++) {
    const tx = b.x + stepDx;
    const ty = b.y + stepDy;
    if      (!boxOverlapsWalls(tx, ty, b.w, b.h, b.rot)) { b.x = tx; b.y = ty; moved = true; }
    else if (!boxOverlapsWalls(tx, b.y, b.w, b.h, b.rot)) { b.x = tx;       moved = true; }
    else if (!boxOverlapsWalls(b.x, ty, b.w, b.h, b.rot)) { b.y = ty;       moved = true; }
    else break;
  }
  if (moved) invalidateBox(b);
}

/* ==========================================================================
   ROTATE HANDLE DRAG
   ==========================================================================
   The rotation handle spins the box about its own centre.  Its
   position is computed by rotateHandleScreenPos in bx_core; both the
   renderer and the hit test share that function, so the handle the
   user sees is exactly the handle the hit test finds.

   The drag math: record the angle from box centre to the initial
   cursor position, then on each move apply the delta angle to the
   box's original rotation.  Shift snaps the result to 15°. */

function startRotate(b, wx, wy) {
  state.handleDrag = {
    type: "rotate",
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
    startAngle: Math.atan2(wy - b.y, wx - b.x),
  };
}

function updateRotate(wx, wy, shift) {
  const hd = state.handleDrag, b = hd.b;
  const phi = Math.atan2(wy - b.y, wx - b.x);
  let delta = (phi - hd.startAngle) * 180 / Math.PI;
  let newRot = hd.orig.rot + delta;
  if (shift) newRot = Math.round(newRot / 15) * 15;
  newRot = ((newRot % 360) + 360) % 360;

  const curInvalid = boxIsInvalid(b);
  if (curInvalid || !boxOverlapsWalls(b.x, b.y, b.w, b.h, newRot)) {
    b.rot = newRot;
    invalidateBox(b);
    return true;
  }
  return false;
}

/* ==========================================================================
   EDGE RESIZE — one axis
   ==========================================================================
   Dragging an edge handle resizes that edge, keeping the opposite
   edge fixed.  Both edges of the pair share the box's local frame,
   so the math is a projection onto one of the box's two axes.

   The four edges, indexed by their position in boxEdgeMidpoints:

       edge 0   the "bottom" edge in the box's local frame
       edge 1   the "right"  edge
       edge 2   the "top"    edge
       edge 3   the "left"   edge */

function startResize(b, edgeIndex) {
  state.handleDrag = {
    type: "resize",
    edge: edgeIndex,
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
  };
}

function updateResize(wx, wy) {
  const hd = state.handleDrag, b = hd.b, o = hd.orig;
  const r = o.rot * Math.PI / 180;
  const ux = Math.cos(r),  uy = Math.sin(r);
  const vx = -Math.sin(r), vy = Math.cos(r);

  let newW = o.w, newH = o.h, newCx = o.x, newCy = o.y;

  if (hd.edge === 1) {
    const fx = o.x - ux * o.w / 2, fy = o.y - uy * o.w / 2;
    const t  = (wx - fx) * ux + (wy - fy) * uy;
    newW = Math.max(MIN_BOX_MM, t);
    newCx = fx + ux * newW / 2; newCy = fy + uy * newW / 2;
  } else if (hd.edge === 3) {
    const fx = o.x + ux * o.w / 2, fy = o.y + uy * o.w / 2;
    const t  = (wx - fx) * ux + (wy - fy) * uy;
    newW = Math.max(MIN_BOX_MM, -t);
    newCx = fx - ux * newW / 2; newCy = fy - uy * newW / 2;
  } else if (hd.edge === 2) {
    const fx = o.x - vx * o.h / 2, fy = o.y - vy * o.h / 2;
    const t  = (wx - fx) * vx + (wy - fy) * vy;
    newH = Math.max(MIN_BOX_MM, t);
    newCx = fx + vx * newH / 2; newCy = fy + vy * newH / 2;
  } else if (hd.edge === 0) {
    const fx = o.x + vx * o.h / 2, fy = o.y + vy * o.h / 2;
    const t  = (wx - fx) * vx + (wy - fy) * vy;
    newH = Math.max(MIN_BOX_MM, -t);
    newCx = fx - vx * newH / 2; newCy = fy - vy * newH / 2;
  }

  const curInvalid = boxIsInvalid(b);
  if (curInvalid ||
      !boxOverlapsWalls(newCx, newCy, newW, newH, o.rot)) {
    b.x = newCx; b.y = newCy; b.w = newW; b.h = newH;
    invalidateBox(b);
    return true;
  }
  return false;
}

/* ==========================================================================
   CORNER RESIZE — both axes
   ==========================================================================
   Pin the opposite corner, project the cursor onto the box's two
   local axes, take the absolutes as the new dimensions, and place the
   new centre at the midpoint between the anchor and the dragged
   corner.  The projections' signs, preserved through MIN_BOX_MM
   clamping, give the box the ability to flip through its anchor
   corner. */

function startResizeCorner(b, cornerIndex) {
  state.handleDrag = {
    type: "resize-corner",
    corner: cornerIndex,
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
  };
}

function updateResizeCorner(wx, wy) {
  const hd = state.handleDrag, b = hd.b, o = hd.orig;
  const r = o.rot * Math.PI / 180;
  const ux = Math.cos(r),  uy = Math.sin(r);
  const vx = -Math.sin(r), vy = Math.cos(r);

  const oppLocal = [
    [-o.w / 2, -o.h / 2],
    [ o.w / 2, -o.h / 2],
    [ o.w / 2,  o.h / 2],
    [-o.w / 2,  o.h / 2],
  ][(hd.corner + 2) % 4];

  const ax = o.x + ux * oppLocal[0] + vx * oppLocal[1];
  const ay = o.y + uy * oppLocal[0] + vy * oppLocal[1];

  const dx = wx - ax, dy = wy - ay;
  const localU = dx * ux + dy * uy;
  const localV = dx * vx + dy * vy;

  const signU = localU >= 0 ? 1 : -1;
  const signV = localV >= 0 ? 1 : -1;
  const newW = Math.max(MIN_BOX_MM, Math.abs(localU));
  const newH = Math.max(MIN_BOX_MM, Math.abs(localV));

  const uOffset = signU * newW / 2;
  const vOffset = signV * newH / 2;

  const newCx = ax + ux * uOffset + vx * vOffset;
  const newCy = ay + uy * uOffset + vy * vOffset;

  const curInvalid = boxIsInvalid(b);
  if (curInvalid ||
      !boxOverlapsWalls(newCx, newCy, newW, newH, o.rot)) {
    b.x = newCx; b.y = newCy; b.w = newW; b.h = newH;
    invalidateBox(b);
    return true;
  }
  return false;
}

/* ==========================================================================
   SELECTION
   ========================================================================== */

function setSelectedLocal(b) {
  board.selectedIdx = (b == null) ? -1 : board.items.indexOf(b);
}

/* ==========================================================================
   WALL GATE — strict
   ==========================================================================
   The strict gate.  Every panel-driven edit that is not a W / H
   change (rotation in particular) goes through here.  An invalid
   box is editable freely; a valid box cannot be committed to a
   wall-overlap state. */

function tryApplyBoxChange(b, changes) {
  const test = { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot };
  Object.assign(test, changes);
  const curInvalid = boxIsInvalid(b);
  if (!curInvalid &&
      boxOverlapsWalls(test.x, test.y, test.w, test.h, test.rot)) {
    // flashStatus message is deferred to the caller so this file
    // stays clear of DOM; the caller uses T("msgWallBlocked").
    return false;
  }
  Object.assign(b, changes);
  invalidateBox(b);
  return true;
}

/* ==========================================================================
   WALL GATE — size-specific
   ==========================================================================
   Handles the two size fields specifically, with a fallback chain
   that matches what corner-drag does:

     1.  Symmetric growth (box grows around its current centre).
         Least surprising, matches previous behaviour, tried first.

     2.  Asymmetric growth (pin one edge, grow from the other).  This
         is what corner-drag does, and is why drag reaches sizes the
         symmetric field cannot: it grows in one direction, not both.

     3.  Permissive.  If neither side has room, allow the box to
         become invalid — the user gets the value they typed, the box
         renders dashed grey with a warning chip, and they can drag
         it out of the wall.

   Rotation continues to use the strict gate.  A rotation that
   would spin a valid box into a wall is a genuinely surprising
   move; a size change that overreaches by a bit is a normal
   intermediate state during editing. */

export function tryApplySizeChange(b, dim, newSize) {
  newSize = Math.max(MIN_BOX_MM, newSize);
  if (dim !== "w" && dim !== "h") return false;
  if (newSize === b[dim]) return true;

  const curInvalid = boxIsInvalid(b);

  /* 1. Symmetric.  If the box is already invalid, apply freely;
     otherwise require the symmetric result to stay valid. */
  const testW = dim === "w" ? newSize : b.w;
  const testH = dim === "h" ? newSize : b.h;
  if (curInvalid ||
      !boxOverlapsWalls(b.x, b.y, testW, testH, b.rot)) {
    if (dim === "w") b.w = newSize;
    else            b.h = newSize;
    invalidateBox(b);
    return true;
  }

  /* 2. Asymmetric.  Pin each of the two edges on the changed axis
     and check whether the other direction has room. */
  const delta = newSize - b[dim];
  if (dim === "w") {
    /* Pin left edge: centre shifts right by delta/2. */
    const cxL = b.x + delta / 2;
    if (!boxOverlapsWalls(cxL, b.y, newSize, b.h, b.rot)) {
      b.x = cxL; b.w = newSize; invalidateBox(b); return true;
    }
    /* Pin right edge: centre shifts left by delta/2. */
    const cxR = b.x - delta / 2;
    if (!boxOverlapsWalls(cxR, b.y, newSize, b.h, b.rot)) {
      b.x = cxR; b.w = newSize; invalidateBox(b); return true;
    }
  } else {
    const cyA = b.y + delta / 2;
    if (!boxOverlapsWalls(b.x, cyA, b.w, newSize, b.rot)) {
      b.y = cyA; b.h = newSize; invalidateBox(b); return true;
    }
    const cyB = b.y - delta / 2;
    if (!boxOverlapsWalls(b.x, cyB, b.w, newSize, b.rot)) {
      b.y = cyB; b.h = newSize; invalidateBox(b); return true;
    }
  }

  /* 3. Permissive.  The box becomes invalid; visual feedback comes
     from the dashed-grey style and the "invalid" chip in bx_view. */
  if (dim === "w") b.w = newSize;
  else            b.h = newSize;
  invalidateBox(b);
  return true;
}

/* ==========================================================================
   ROTATION HELPERS
   ========================================================================== */

function rotateSelection(deg) {
  const b = state.hoveredBox || selectedItem();
  if (b) {
    const newRot = ((b.rot + deg) % 360 + 360) % 360;
    const curInvalid = boxIsInvalid(b);
    if (!curInvalid && boxOverlapsWalls(b.x, b.y, b.w, b.h, newRot)) {
      return false;
    }
    b.rot = newRot;
    invalidateBox(b);
    return true;
  } else {
    const cur = parseFloat(document.getElementById("boxRot").value) || 0;
    const v = ((cur + deg) % 360 + 360) % 360;
    document.getElementById("boxRot").value = v;
    return true;
  }
}

/* Public re-exports — bx_dispatch and bx_panel_bindings import
   these by name. */
export {
  startRotate, updateRotate, startResize, updateResize,
  startResizeCorner, updateResizeCorner,
  moveBoxSafely, setSelectedLocal, tryApplyBoxChange, rotateSelection,
};
"""
