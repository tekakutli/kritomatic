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
    • the selection model — selectedBox, hoveredBox
    • tryApplyBoxChange — the wall gate every mutation passes through

Nothing here draws; the draw calls are made by bx_dispatch once the
mutation has landed.

Translation
-----------
The two wall-gate flash messages this module emits — one for a
panel-driven mutation blocked by a wall, one for a blocked rotation —
come from T() in bx_i18n.py.

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
"""


EDIT_JS = r"""
/* ==========================================================================
   WALL-SLIDING BODY DRAG
   ==========================================================================
   Swept path: divide the drag into ~50 mm steps, and at each step try
   the full move; if that collides, try x-only; then y-only; then stop.
   A single 500 mm jump into a wall would be rejected by the plain
   collision test, so without the sweep a fast drag would refuse to
   slide along a wall. */

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

   The drag math is unchanged from the corner-rotate the previous
   revision used: record the angle from box centre to the initial
   cursor position, then on each move apply the delta angle to the
   box's original rotation.  Shift snaps the result to 15°. */

function startRotate(b, wx, wy) {
  handleDrag = {
    type: "rotate",
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
    startAngle: Math.atan2(wy - b.y, wx - b.x),
  };
}

function updateRotate(wx, wy, shift) {
  const hd = handleDrag, b = hd.b;
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
  handleDrag = {
    type: "resize",
    edge: edgeIndex,
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
  };
}

function updateResize(wx, wy) {
  const hd = handleDrag, b = hd.b, o = hd.orig;
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
   See the module docstring for the derivation.  In one sentence: pin
   the opposite corner, project the cursor onto the box's two local
   axes, take the absolutes as the new dimensions, and place the new
   centre at the midpoint between the anchor and the dragged corner.
   The projections' signs, preserved through MIN_BOX_MM clamping, give
   the box the ability to flip through its anchor corner. */

function startResizeCorner(b, cornerIndex) {
  handleDrag = {
    type: "resize-corner",
    corner: cornerIndex,
    b,
    orig: { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot },
  };
}

function updateResizeCorner(wx, wy) {
  const hd = handleDrag, b = hd.b, o = hd.orig;
  const r = o.rot * Math.PI / 180;
  const ux = Math.cos(r),  uy = Math.sin(r);
  const vx = -Math.sin(r), vy = Math.cos(r);

  /* The anchor is the diagonally opposite corner in the box's local
     frame.  Local corner positions, in the same order the boxCorners
     helper uses:

         corner 0  (-w/2, -h/2)
         corner 1  (+w/2, -h/2)
         corner 2  (+w/2, +h/2)
         corner 3  (-w/2, +h/2)

     so the anchor for dragged corner `i` is at index (i + 2) % 4. */
  const oppLocal = [
    [-o.w / 2, -o.h / 2],
    [ o.w / 2, -o.h / 2],
    [ o.w / 2,  o.h / 2],
    [-o.w / 2,  o.h / 2],
  ][(hd.corner + 2) % 4];

  /* Anchor in world space. */
  const ax = o.x + ux * oppLocal[0] + vx * oppLocal[1];
  const ay = o.y + uy * oppLocal[0] + vy * oppLocal[1];

  /* Cursor relative to the anchor, projected onto the box's local
     axes.  localU / localV are signed distances in box-local mm. */
  const dx = wx - ax, dy = wy - ay;
  const localU = dx * ux + dy * uy;
  const localV = dx * vx + dy * vy;

  const signU = localU >= 0 ? 1 : -1;
  const signV = localV >= 0 ? 1 : -1;
  const newW = Math.max(MIN_BOX_MM, Math.abs(localU));
  const newH = Math.max(MIN_BOX_MM, Math.abs(localV));

  /* New centre = anchor + half the new extent along each axis, with
     the axis direction determined by the projection's sign. */
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

function setSelected(b) {
  selectedBox = b;
  syncSelectionInputs();
  draw();
}

/* ==========================================================================
   WALL GATE
   ==========================================================================
   Every panel-driven edit passes through here.  An invalid box is
   editable freely; a valid box cannot be committed to a wall-overlap
   state. */

function tryApplyBoxChange(b, changes) {
  const test = { x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot };
  Object.assign(test, changes);
  const curInvalid = boxIsInvalid(b);
  if (!curInvalid &&
      boxOverlapsWalls(test.x, test.y, test.w, test.h, test.rot)) {
    flashStatus(T("msgWallBlocked"), "bad");
    return false;
  }
  Object.assign(b, changes);
  invalidateBox(b);
  return true;
}

/* ==========================================================================
   ROTATION HELPERS
   ========================================================================== */

function rotateSelection(deg) {
  const b = hoveredBox || selectedBox;
  if (b) {
    const newRot = ((b.rot + deg) % 360 + 360) % 360;
    const curInvalid = boxIsInvalid(b);
    if (!curInvalid && boxOverlapsWalls(b.x, b.y, b.w, b.h, newRot)) {
      flashStatus(T("msgRotationBlocked"), "bad");
      return;
    }
    b.rot = newRot;
    invalidateBox(b);
    if (b === selectedBox) syncSelectionInputs();
  } else {
    const cur = parseFloat(document.getElementById("boxRot").value) || 0;
    setRotation(cur + deg);
  }
}
"""
