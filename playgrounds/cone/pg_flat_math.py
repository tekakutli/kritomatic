"""
pg_flat_math.py — flat-view parameter-space math.

ES module.  The flat (unfolded-cone) view treats the cone's lateral
surface as the rectangle (φ, s) ∈ [0, 2π] × [0, 1].  This module
carries the projection that maps (φ, s) to screen, and the small
wrap helpers that let a patch straddling the seam appear on both
sides.

WHAT MOVED HERE
===============
`FLAT_PHI_MIN/MAX/SPAN`, `FLAT_S_MIN/MAX`, `flatRect`, `flatToScreen`,
`screenToFlat`, `_flatInsideRect`, `_phiCopies`, `_phiShiftForPatch`,
`_phiToDisplay`, `_displayToPhi`.  In the concat version these were
spread across `pg_core.py` and `pg_panel_core.py`.

CALLERS
=======
    pg_view_flat.py         drawFlatView, _buildFlatChrome, flatHitTest
    pg_view_squares_flat.py squareFlatCornersScreen, hit-test
    pg_view_squares_proj.py squarePlaneIntersectionFlat
    pg_panel_fields.py      _syncPatchCoordInputs (phi display)
    pg_panel_scrubs.py      square phi/s scrub read/write
    pg_export.py            flatToScreen

`_TAU`, `_patchPhiCenter`, `_patchSCenter`, `_setPatchPhiCenter`,
`_setPatchSCenter` are in pg_state, not here — they touch the patch
model.  This module only knows how to map (φ, s) to and from
screen.
"""

import textwrap

FLAT_MATH_JS = r"""
import {
  layout, _TAU,
  _phiToDisplay, _displayToPhi,
} from "./pg_state.js";

export { _phiToDisplay, _displayToPhi };

/* ==========================================================================
   CONSTANTS
   ========================================================================== */

export const FLAT_PHI_MIN  = 0;
export const FLAT_PHI_MAX  = 2 * Math.PI;
export const FLAT_PHI_SPAN = FLAT_PHI_MAX - FLAT_PHI_MIN;
export const FLAT_S_MIN    = 0;
export const FLAT_S_MAX    = 1;

/* ==========================================================================
   FLAT RECT — memoized
   ==========================================================================
   Called ~80–100 times per frame from flatToScreen / screenToFlat /
   _flatInsideRect / _flatVExtentPxForShape.  Five Math.min/max per
   call; cached on (innerWidth, innerHeight, layout.reservedH/Y). */

let _flatRectCache = null;

export function flatRect() {
  const W = window.innerWidth;
  const H = layout.reservedH;
  const y0Base = layout.reservedY;

  if (_flatRectCache &&
      _flatRectCache.W === W && _flatRectCache.H === H &&
      _flatRectCache.y0Base === y0Base) {
    return _flatRectCache.r;
  }

  const MARGIN_X = 44;
  const TOP_PAD  = 26;
  const BOT_PAD  = 26;
  const maxW = W - 2 * MARGIN_X;
  const maxH = H - TOP_PAD - BOT_PAD - 4;

  const w = Math.min(maxW, maxH * 2.5);
  const h = Math.min(maxH, w / 2.5);
  const x0 = (W - w) / 2;
  const y0 = y0Base + TOP_PAD + (maxH - h) / 2;

  const r = { x0, y0, w, h };
  _flatRectCache = { W, H, y0Base, r };
  return r;
}

export function flatToScreen(phi, s) {
  const r = flatRect();
  const u = (phi - FLAT_PHI_MIN) / (FLAT_PHI_MAX - FLAT_PHI_MIN);
  const v = s;
  return [r.x0 + u * r.w, r.y0 + (1 - v) * r.h];
}

export function screenToFlat(sx, sy) {
  const r = flatRect();
  return {
    phi: FLAT_PHI_MIN +
         (sx - r.x0) / r.w * (FLAT_PHI_MAX - FLAT_PHI_MIN),
    s:   1 - (sy - r.y0) / r.h,
  };
}

export function _flatInsideRect(sx, sy) {
  const r = flatRect();
  return sx >= r.x0 && sx <= r.x0 + r.w &&
         sy >= r.y0 && sy <= r.y0 + r.h;
}

/* ==========================================================================
   PHI WRAP HELPERS
   ========================================================================== */

export function _phiCopies(phi0, phi1) {
  const kMin = Math.floor((FLAT_PHI_MIN - phi1) / FLAT_PHI_SPAN);
  const kMax = Math.ceil ((FLAT_PHI_MAX - phi0) / FLAT_PHI_SPAN);
  const out = [];
  for (let k = kMin; k <= kMax; k++) out.push(k * FLAT_PHI_SPAN);
  return out;
}

export function _phiShiftForPatch(phiCursor, phi0, phi1) {
  const phiMid = (phi0 + phi1) / 2;
  return Math.round((phiMid - phiCursor) / FLAT_PHI_SPAN) * FLAT_PHI_SPAN;
}
"""

FLAT_MATH_JS = textwrap.dedent(FLAT_MATH_JS)
