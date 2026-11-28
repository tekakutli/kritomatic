"""Context rectangle helpers.

Same as inpaint's `pg_inpaint.js` minus rotation, plus a
boundary clamp on all drags and on the mask-from-rect
construction — the rectangle is strictly bounded to the
alpha-content region of the image.

NO ROTATION
===========
No rotation math.  No rotated payload construction.  The context
rectangle's coords are the on-screen image coords the display
works in (== boundary coords).  When the user drags a handle,
the pure math here clamps to `[0, board.imageWidth] ×
[0, board.imageHeight]` — which are the boundary dims, so the
rect stays within the alpha-content region by construction.

DRAG MATH — CLAMP SEMANTICS
===========================
Three drag modes, three different clamping rules.  The
distinctions matter: using one rule for all three is what
caused the "opposite side keeps growing past the boundary"
bug in the first cut.

  · move — both edges translate together.  Clamp is a
    translation: if the whole rect would exit `[0,W]×[0,H]`,
    shift it back inside (width/height unchanged).

  · non-shift resize — only the dragged edge moves; the
    opposite edge stays put.  Clamp is a cap on the *dragged
    edge only*.  The opposite edge is never touched, so
    dragging east cannot grow west.

  · shift resize — both edges of the dragged axis move
    symmetrically around the start rect's centre.  Growth is
    capped so neither side crosses a boundary; the resulting
    half-width/half-height is `min(desired, boundary-allowed)`.

Min-size enforcement follows the same discipline: push the
moving edge inward to reach MIN_SIZE; if the boundary prevents
that, accept a smaller-than-MIN rect rather than stretching the
opposite side (which would break the boundary invariant).

FULL-CANVAS MASK PRODUCERS
==========================
`buildWhiteMaskB64()` and `buildMaskFromContextRectB64()` both
produce FULL-CANVAS-sized PNGs so the sync payload matches
`canvas_b64`'s dims.  The context rectangle's coords are in
boundary space; the mask builders translate to full-image
coords via `board.boundary.x/y` before drawing.  This is what
lets `pg_sync.js` send a boundary-sized logic in a full-sized
mask to the daemon."""

CONTEXT_JS = r"""
import { board } from "./pg_state.js";

/* ============================================================
   Tolerances
   ============================================================ */

export const CONTEXT_MIN_SIZE     = 16;   // image px
export const CONTEXT_HANDLE_HALF  = 10;   // screen px
export const CONTEXT_BORDER_HALF  = 6;    // screen px

/* ============================================================
   Lazy default
   ============================================================ */

export function ensureDefaultContext() {
  const c = board.context;
  if (c.w > 0 && c.h > 0) return;
  if (!board.imageWidth || !board.imageHeight) return;
  c.x = Math.round(board.imageWidth  * 0.25);
  c.y = Math.round(board.imageHeight * 0.25);
  c.w = Math.round(board.imageWidth  * 0.50);
  c.h = Math.round(board.imageHeight * 0.50);
}

/* ============================================================
   Projections (boundary coords → screen)
   ============================================================ */

export function contextRectScreen(r) {
  const c = board.context;
  if (!r || c.w === 0 || c.h === 0) return null;
  return {
    x0: r.x0 + c.x * r.s,
    y0: r.y0 + c.y * r.s,
    x1: r.x0 + (c.x + c.w) * r.s,
    y1: r.y0 + (c.y + c.h) * r.s,
  };
}

export function contextHandles(r) {
  const rect = contextRectScreen(r);
  if (!rect) return null;
  const { x0, y0, x1, y1 } = rect;
  return [
    { name: 'nw', x: x0, y: y0 },
    { name: 'ne', x: x1, y: y0 },
    { name: 'sw', x: x0, y: y1 },
    { name: 'se', x: x1, y: y1 },
    { name: 'n',  x: (x0 + x1) / 2, y: y0 },
    { name: 's',  x: (x0 + x1) / 2, y: y1 },
    { name: 'w',  x: x0, y: (y0 + y1) / 2 },
    { name: 'e',  x: x1, y: (y0 + y1) / 2 },
  ];
}

/* ============================================================
   Cursor
   ============================================================ */

export function handleCursor(name) {
  switch (name) {
    case 'nw': case 'se': return 'nwse-resize';
    case 'ne': case 'sw': return 'nesw-resize';
    case 'n':  case 's':  return 'ns-resize';
    case 'e':  case 'w':  return 'ew-resize';
    case 'move':          return 'move';
    default:              return 'default';
  }
}

/* ============================================================
   Click classification
   ============================================================ */

export function hitContextRect(r, ix, iy) {
  if (!r) return null;
  const c = board.context;
  if (c.w === 0 || c.h === 0) return null;

  const handleTol = CONTEXT_HANDLE_HALF / r.s;
  const borderTol = CONTEXT_BORDER_HALF / r.s;

  const handles = [
    ['nw', c.x,          c.y],
    ['ne', c.x + c.w,    c.y],
    ['sw', c.x,          c.y + c.h],
    ['se', c.x + c.w,    c.y + c.h],
    ['n',  c.x + c.w/2,  c.y],
    ['s',  c.x + c.w/2,  c.y + c.h],
    ['w',  c.x,          c.y + c.h/2],
    ['e',  c.x + c.w,    c.y + c.h/2],
  ];
  for (const [name, hx, hy] of handles) {
    if (Math.abs(ix - hx) <= handleTol && Math.abs(iy - hy) <= handleTol) {
      return { kind: 'handle', handle: name };
    }
  }

  if (ix >= c.x - borderTol && ix <= c.x + c.w + borderTol &&
      iy >= c.y - borderTol && iy <= c.y + c.h + borderTol) {
    return { kind: 'move' };
  }
  return null;
}

/* ============================================================
   Pure drag math

   `board.imageWidth/Height` are the boundary dims — that is
   the display frame, which is what the context rectangle is
   expressed in.  Clamping to `[0, W] × [0, H]` therefore
   clamps to the boundary.

   Three modes; three clamp disciplines.  See module docstring
   above, DRAG MATH — CLAMP SEMANTICS. */

export function applyContextDrag(startRect, startIx, startIy,
                                  currentIx, currentIy,
                                  handle, shiftKey) {
  const dx = currentIx - startIx;
  const dy = currentIy - startIy;
  const { x, y, w, h } = startRect;
  const W = board.imageWidth  || 0;
  const H = board.imageHeight || 0;

  let x0 = x, x1 = x + w, y0 = y, y1 = y + h;

  if (handle === 'move') {
    /* Translate both edges.  Clamp is a translation (shift the
       whole rect back inside if it tried to leave); width and
       height are preserved.  This is the only case where the
       opposite edge legitimately moves with the dragged one. */
    x0 += dx; x1 += dx;
    y0 += dy; y1 += dy;
    if (W > 0) {
      if (x0 < 0) { x1 -= x0; x0 = 0; }
      if (x1 > W) { x0 -= (x1 - W); x1 = W; }
    }
    if (H > 0) {
      if (y0 < 0) { y1 -= y0; y0 = 0; }
      if (y1 > H) { y0 -= (y1 - H); y1 = H; }
    }
  } else if (shiftKey) {
    /* Symmetric resize around the start rect's centre.  Both
       edges of the dragged axis move by equal amounts.  Growth
       is capped so neither side crosses a boundary; the
       resulting half-extent is min(desired, boundary-allowed). */
    const cx = (x0 + x1) / 2;
    const cy = (y0 + y1) / 2;
    const halfW0 = (x1 - x0) / 2;
    const halfH0 = (y1 - y0) / 2;

    const movesW = handle.includes('w');
    const movesE = handle.includes('e');
    const movesN = handle.includes('n');
    const movesS = handle.includes('s');

    /* Per axis, at most one of {movesW, movesE} is true for a
       corner handle; the same for {movesN, movesS}.  So each of
       the next two conditionals is single-sided. */
    let halfW = halfW0;
    let halfH = halfH0;
    if (movesE) halfW = halfW0 + dx;
    if (movesW) halfW = halfW0 - dx;
    if (movesS) halfH = halfH0 + dy;
    if (movesN) halfH = halfH0 - dy;

    /* Min-size (the centre is fixed; grow the half-extent). */
    if (2 * halfW < CONTEXT_MIN_SIZE) halfW = CONTEXT_MIN_SIZE / 2;
    if (2 * halfH < CONTEXT_MIN_SIZE) halfH = CONTEXT_MIN_SIZE / 2;

    /* Boundary cap: the symmetric growth may not cross either
       edge.  `min(cx, W - cx)` is the largest half-width that
       keeps `[cx-halfW, cx+halfW]` inside `[0, W]`; same for H. */
    if (W > 0) {
      const maxHalfW = Math.min(cx, W - cx);
      if (halfW > maxHalfW) halfW = maxHalfW;
    }
    if (H > 0) {
      const maxHalfH = Math.min(cy, H - cy);
      if (halfH > maxHalfH) halfH = maxHalfH;
    }

    x0 = cx - halfW; x1 = cx + halfW;
    y0 = cy - halfH; y1 = cy + halfH;
  } else {
    /* Independent-edge resize.  Only the dragged edge(s) move;
       the opposite edges stay put.  This is the key fix: the
       dragged edge is clamped at the boundary, and the opposite
       edge is NOT touched — so dragging east cannot grow west. */
    const movesW = handle.includes('w');
    const movesE = handle.includes('e');
    const movesN = handle.includes('n');
    const movesS = handle.includes('s');

    if (movesW) x0 += dx;
    if (movesE) x1 += dx;
    if (movesN) y0 += dy;
    if (movesS) y1 += dy;

    /* Cap the moving edge at the boundary.  Do NOT shift the
       opposite edge to make room — that was the bug. */
    if (W > 0) {
      if (movesW && x0 < 0) x0 = 0;
      if (movesE && x1 > W) x1 = W;
    }
    if (H > 0) {
      if (movesN && y0 < 0) y0 = 0;
      if (movesS && y1 > H) y1 = H;
    }

    /* Min-size: push the MOVING edge inward to reach MIN_SIZE;
       never push the opposite one.  If the boundary prevents
       the push (dragged edge is already at a boundary, so
       further inward push would cross it), accept the
       smaller-than-MIN rect rather than break the boundary
       invariant.  The boundary is a hard invariant; MIN_SIZE
       is a convenience. */
    if (x1 - x0 < CONTEXT_MIN_SIZE) {
      if (movesW) {
        x0 = x1 - CONTEXT_MIN_SIZE;
        if (W > 0 && x0 < 0) x0 = 0;
      }
      if (movesE) {
        x1 = x0 + CONTEXT_MIN_SIZE;
        if (W > 0 && x1 > W) x1 = W;
      }
    }
    if (y1 - y0 < CONTEXT_MIN_SIZE) {
      if (movesN) {
        y0 = y1 - CONTEXT_MIN_SIZE;
        if (H > 0 && y0 < 0) y0 = 0;
      }
      if (movesS) {
        y1 = y0 + CONTEXT_MIN_SIZE;
        if (H > 0 && y1 > H) y1 = H;
      }
    }
  }

  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/* ============================================================
   Full-canvas mask producers
   ============================================================
   Both build a W×H PNG where W,H are `board.fullImageWidth/Height`
   — the daemon needs the mask in the same frame as `canvas_b64`
   (which is the full image).  Content lands at
   `board.boundary.x/y` so the daemon's later crop to
   `boundary ∩ context` picks up the intended pixels.

   For rmbg's typical case, boundary == (0,0,W,H) (fully-opaque
   image), so these reduce to plain fills.  The offset machinery
   only matters when the loaded layer has transparency. */

export function buildWhiteMaskB64() {
  const W = board.fullImageWidth;
  const H = board.fullImageHeight;
  if (!W || !H) return null;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  cx.fillStyle = "#fff";
  cx.fillRect(0, 0, W, H);
  return c.toDataURL("image/png")
    .replace(/^data:image\/png;base64,/, "");
}

export function buildContextMaskB64() {
  const W = board.fullImageWidth;
  const H = board.fullImageHeight;
  if (!W || !H) return null;

  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");

  if (!board.contextEnabled) {
    cx.fillStyle = "#fff";
    cx.fillRect(0, 0, W, H);
    return c.toDataURL("image/png")
      .replace(/^data:image\/png;base64,/, "");
  }

  ensureDefaultContext();
  cx.fillStyle = "#000";
  cx.fillRect(0, 0, W, H);

  const ctxRect = board.context;
  const bx = board.boundary.x;
  const by = board.boundary.y;
  cx.fillStyle = "#fff";
  cx.fillRect(bx + ctxRect.x, by + ctxRect.y, ctxRect.w, ctxRect.h);

  return c.toDataURL("image/png")
    .replace(/^data:image\/png;base64,/, "");
}

/* ============================================================
   Mask-from-context-rect for the "+ As mask" button
   ============================================================
   Produces a BOUNDARY-SIZED mask (matches `board.imageWidth` ×
   `board.imageHeight`, the display dims).  This is what lives in
   `board.masks` — a boundary-frame mask.  `pg_sync.js` later
   pads it out to full-canvas size for the daemon. */

export function buildMaskFromContextRectB64() {
  const W = board.imageWidth;
  const H = board.imageHeight;
  if (!board.contextEnabled) return null;
  const c = board.context;
  if (!c || c.w <= 0 || c.h <= 0) return null;

  const cvs = document.createElement("canvas");
  cvs.width = W; cvs.height = H;
  const ccx = cvs.getContext("2d");
  ccx.fillStyle = "#000";
  ccx.fillRect(0, 0, W, H);
  ccx.fillStyle = "#fff";
  ccx.fillRect(c.x, c.y, c.w, c.h);

  return cvs.toDataURL("image/png")
    .replace(/^data:image\/png;base64,/, "");
}

/* ============================================================
   Mask resize
   ============================================================ */

export function resizeMaskB64ToCanvas(b64, img) {
  if (img.width === board.imageWidth && img.height === board.imageHeight) {
    return b64;
  }
  const c = document.createElement("canvas");
  c.width = board.imageWidth; c.height = board.imageHeight;
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}
"""