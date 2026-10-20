"""
pg_panel_fields.py — setters and per-field sync functions.

Everything that knows what a specific patch- or square-field MEANS
lives here: the patch φ/s centre setters, the square W/H setters,
the two field-sync functions that push the model's current state
into the DOM each frame, and the mirror / warp / slope / visual-
bottom fields' semantics.

STAGE 1 OPTIMIZATION
====================
Both sync functions (`_syncPatchCoordInputs`, `_syncSquareSizeInputs`)
used to call `document.getElementById` five to ten times each,
every frame — including during every drag.  During an active drag,
that's ~20 `getElementById` scans per frame against a 30-element
panel.

Both functions now:
    · cache their element references on first call (they never
      change — the panel DOM is built once at page load);
    · equality-check each write, so the DOM is only mutated when
      the value actually differs.  During a drag, most fields
      don't change; the ones that do get one write apiece.

The visual result is identical; DOM cost per frame drops from
~20 lookups + ~10 writes to 0 lookups + ~1–2 writes.
"""

PANEL_FIELDS_JS = r"""
/* ==========================================================================
   PATCH-POSITION SETTERS
   ========================================================================== */

function _patchPhiCenter(q) {
  let c = (q.phi0 + q.phi1) / 2;
  c = ((c % _TAU) + _TAU) % _TAU;
  return c;
}

function _patchSCenter(q) {
  return (q.s0 + q.s1) / 2;
}

function _setPatchPhiCenter(q, target) {
  const cur = _patchPhiCenter(q);
  let delta = target - cur;
  while (delta >  Math.PI) delta -= _TAU;
  while (delta < -Math.PI) delta += _TAU;
  q.phi0 += delta;
  q.phi1 += delta;
  _reclampShapesOnPatch(q);
}

function _setPatchSCenter(q, target) {
  const cur = _patchSCenter(q);
  const halfSpan = (q.s1 - q.s0) / 2;
  const lo = FLAT_S_MIN + halfSpan;
  const hi = FLAT_S_MAX - halfSpan;
  let t;
  if (hi < lo) {
    t = (FLAT_S_MIN + FLAT_S_MAX) / 2;
  } else {
    t = Math.max(lo, Math.min(hi, target));
  }
  const delta = t - cur;
  q.s0 += delta;
  q.s1 += delta;
  _reclampShapesOnPatch(q);
}

/* ==========================================================================
   SQUARE-SIZE SETTERS
   ========================================================================== */

function _setSquareWidth(sq, target) {
  const sU = target / SHAPE_REL_SIZE;
  sq.scaleU = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sU));
}

function _setSquareHeight(sq, target) {
  const sV = target / SHAPE_REL_SIZE;
  sq.scaleV = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sV));
}

/* ==========================================================================
   PATCH-POSITION FIELDS — ELEMENT CACHE + WRITE GUARDS
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

function _syncPatchCoordInputs() {
  const els = _getPatchEls();
  const q = (selectedQuad >= 0 && selectedQuad < quads.length)
    ? quads[selectedQuad] : null;

  // Patch φ and s.
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

  // Mirror checkbox.
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

  // Mirror angle (degrees in the field, radians in the model).
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

  // Mirror flip checkbox.
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

  // Plane warp.
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
   SQUARE-SIZE AND -POSITION FIELDS — ELEMENT CACHE + WRITE GUARDS
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

function _syncSquareSizeInputs() {
  const els = _getSquareEls();
  if (!els.w || !els.h || !els.slope) return;

  const sq = (selectedSquare >= 0 && selectedSquare < floatSquares.length)
    ? floatSquares[selectedSquare] : null;

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

  // The global multiplier follows no selection; always shown.
  if (els.mult && document.activeElement !== els.mult) {
    const t = SHAPE_STANDING_HEIGHT_MULT.toFixed(2);
    if (els.mult.value !== t) els.mult.value = t;
  }
}
"""
