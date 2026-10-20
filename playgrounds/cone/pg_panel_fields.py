"""
pg_panel_fields.py — setters and per-field sync functions.

ES module.  `_syncPatchCoordInputs` and `_syncSquareSizeInputs`.
Both use element caches and write-guards (stage 1), so a stable
panel costs zero DOM writes per frame and zero `getElementById`
lookups.
"""

PANEL_FIELDS_JS = r"""
import {
  quads, floatSquares, coneState, SHAPE_STATE,
  _phiToDisplay, _patchPhiCenter, _patchSCenter,
} from "./pg_state.js";
import {
  squareWorldWidth, squareWorldHeight, squareFlatCenter,
} from "./pg_view_squares_core.js";

/* ==========================================================================
   PATCH FIELDS — element cache + write guards
   ========================================================================== */

let _patchEls = null;

function _getPatchEls() {
  if (_patchEls) return _patchEls;
  _patchEls = {
    phi:        document.getElementById("patchPhiVal"),
    s:          document.getElementById("patchSVal"),
    mirror:     document.getElementById("patchMirror"),
    mirrorAng:  document.getElementById("patchMirrorAngle"),
    mirrorFlip: document.getElementById("patchMirrorFlip"),
    warp:       document.getElementById("patchWarp"),
  };
  return _patchEls;
}

export function _syncPatchCoordInputs() {
  const els = _getPatchEls();
  const q = (coneState.selectedQuad >= 0 &&
             coneState.selectedQuad < quads.length)
    ? quads[coneState.selectedQuad] : null;

  if (els.phi && els.s) {
    if (!q) {
      if (!els.phi.disabled) els.phi.disabled = true;
      if (!els.s.disabled)   els.s.disabled   = true;
      if (document.activeElement !== els.phi && els.phi.value !== "\u2014") {
        els.phi.value = "\u2014";
      }
      if (document.activeElement !== els.s && els.s.value !== "\u2014") {
        els.s.value = "\u2014";
      }
    } else {
      if (els.phi.disabled) els.phi.disabled = false;
      if (els.s.disabled)   els.s.disabled   = false;
      if (document.activeElement !== els.phi) {
        const t = _phiToDisplay(_patchPhiCenter(q)).toFixed(3);
        if (els.phi.value !== t) els.phi.value = t;
      }
      if (document.activeElement !== els.s) {
        const t = _patchSCenter(q).toFixed(3);
        if (els.s.value !== t) els.s.value = t;
      }
    }
  }

  if (els.mirror) {
    if (!q) {
      if (!els.mirror.disabled) els.mirror.disabled = true;
      if (els.mirror.checked)   els.mirror.checked  = false;
    } else {
      if (els.mirror.disabled) els.mirror.disabled = false;
      if (document.activeElement !== els.mirror) {
        const t = !!q.mirror;
        if (els.mirror.checked !== t) els.mirror.checked = t;
      }
    }
  }

  if (els.mirrorAng) {
    if (!q) {
      if (!els.mirrorAng.disabled) els.mirrorAng.disabled = true;
      if (document.activeElement !== els.mirrorAng &&
          els.mirrorAng.value !== "\u2014") {
        els.mirrorAng.value = "\u2014";
      }
    } else {
      if (els.mirrorAng.disabled) els.mirrorAng.disabled = false;
      if (document.activeElement !== els.mirrorAng) {
        const ang = (typeof q.mirrorAngle === "number") ? q.mirrorAngle : Math.PI;
        const t = (ang * 180 / Math.PI).toFixed(1);
        if (els.mirrorAng.value !== t) els.mirrorAng.value = t;
      }
    }
  }

  if (els.mirrorFlip) {
    if (!q) {
      if (!els.mirrorFlip.disabled) els.mirrorFlip.disabled = true;
      if (els.mirrorFlip.checked)   els.mirrorFlip.checked  = false;
    } else {
      if (els.mirrorFlip.disabled) els.mirrorFlip.disabled = false;
      if (document.activeElement !== els.mirrorFlip) {
        const t = q.mirrorFlip !== false;
        if (els.mirrorFlip.checked !== t) els.mirrorFlip.checked = t;
      }
    }
  }

  if (els.warp) {
    if (!q) {
      if (!els.warp.disabled) els.warp.disabled = true;
      if (document.activeElement !== els.warp &&
          els.warp.value !== "\u2014") {
        els.warp.value = "\u2014";
      }
    } else {
      if (els.warp.disabled) els.warp.disabled = false;
      if (document.activeElement !== els.warp) {
        const w = (typeof q.warp === "number") ? q.warp : 0;
        const t = w.toFixed(3);
        if (els.warp.value !== t) els.warp.value = t;
      }
    }
  }

  _syncSquareSizeInputs();
}

/* ==========================================================================
   SQUARE FIELDS — element cache + write guards
   ========================================================================== */

let _squareEls = null;

function _getSquareEls() {
  if (_squareEls) return _squareEls;
  _squareEls = {
    w:      document.getElementById("squareWVal"),
    h:      document.getElementById("squareHVal"),
    slope:  document.getElementById("squareSlopeVal"),
    phi:    document.getElementById("squarePhiVal"),
    s:      document.getElementById("squareSVal"),
    vb:     document.getElementById("squareVisualBottom"),
    mult:   document.getElementById("shapeStandingMultVal"),
  };
  return _squareEls;
}

export function _syncSquareSizeInputs() {
  const els = _getSquareEls();
  if (!els.w || !els.h || !els.slope) return;

  const sq = (coneState.selectedSquare >= 0 &&
              coneState.selectedSquare < floatSquares.length)
    ? floatSquares[coneState.selectedSquare] : null;

  if (!sq) {
    if (!els.w.disabled)     els.w.disabled     = true;
    if (!els.h.disabled)     els.h.disabled     = true;
    if (!els.slope.disabled) els.slope.disabled = true;
    if (els.phi) {
      if (!els.phi.disabled) els.phi.disabled = true;
      if (document.activeElement !== els.phi && els.phi.value !== "\u2014") {
        els.phi.value = "\u2014";
      }
    }
    if (els.s) {
      if (!els.s.disabled) els.s.disabled = true;
      if (document.activeElement !== els.s && els.s.value !== "\u2014") {
        els.s.value = "\u2014";
      }
    }
    if (els.vb) {
      if (!els.vb.disabled) els.vb.disabled = true;
      if (document.activeElement !== els.vb && els.vb.checked) els.vb.checked = false;
    }
    if (document.activeElement !== els.w && els.w.value !== "\u2014") {
      els.w.value = "\u2014";
    }
    if (document.activeElement !== els.h && els.h.value !== "\u2014") {
      els.h.value = "\u2014";
    }
    if (document.activeElement !== els.slope && els.slope.value !== "\u2014") {
      els.slope.value = "\u2014";
    }
  } else {
    if (els.w.disabled)     els.w.disabled     = false;
    if (els.h.disabled)     els.h.disabled     = false;
    if (els.slope.disabled) els.slope.disabled = false;
    if (els.phi)  els.phi.disabled  = false;
    if (els.s)    els.s.disabled    = false;
    if (els.vb) {
      els.vb.disabled = false;
      if (document.activeElement !== els.vb) {
        const t = !!sq.visualBottom;
        if (els.vb.checked !== t) els.vb.checked = t;
      }
    }

    if (document.activeElement !== els.w) {
      const t = squareWorldWidth(sq).toFixed(3);
      if (els.w.value !== t) els.w.value = t;
    }
    if (document.activeElement !== els.h) {
      const t = squareWorldHeight(sq).toFixed(3);
      if (els.h.value !== t) els.h.value = t;
    }
    if (document.activeElement !== els.slope) {
      const t = (sq.slope || 0).toFixed(3);
      if (els.slope.value !== t) els.slope.value = t;
    }

    const centre = squareFlatCenter(sq);
    if (centre) {
      if (els.phi && document.activeElement !== els.phi) {
        let p = centre.phi;
        p = ((p % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
        const t = p.toFixed(3);
        if (els.phi.value !== t) els.phi.value = t;
      }
      if (els.s && document.activeElement !== els.s) {
        const t = centre.s.toFixed(3);
        if (els.s.value !== t) els.s.value = t;
      }
    }
  }

  if (els.mult && document.activeElement !== els.mult) {
    const t = SHAPE_STATE.standingMult.toFixed(2);
    if (els.mult.value !== t) els.mult.value = t;
  }
}
"""
