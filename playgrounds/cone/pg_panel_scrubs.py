"""
pg_panel_scrubs.py — scrub-input bindings + every number field.

ES module.  Installs the 10 scrub text inputs the cone panel has
(patch φ / s / mirror ∠ / plane warp, square φ / s / W / H / slope,
plus the global standing-height multiplier) and registers every
number field with `pgkitRegisterField`.

Also registers the three read-only-ish number fields that have no
scrub input but *do* need remote access: `cone.apexX`, `cone.apexY`,
and the view tuple `view.zoom/panX/panY`.  The range sliders
(`cone.depth`, `cone.halfAngle`, `cone.ringCount`,
`cone.meridianCount`, `shape.depthCone`) live in
`pg_panel_bindings.py` because they're `<input type="range">`,
not scrub inputs.

`cone.apexX` / `cone.apexY` expose the *intent* (`axRaw` / `ayRaw`),
not the clamped display.  A remote client that writes an out-of-
bounds apex gets its own value back, so the field behaves like the
number it is: the constraint is applied to the display at render
time, not silently folded into the stored value.  If depth later
grows, the display drifts to meet the intent.  See pg_state.py's
DUAL APEX section.

SCRUB RATES
===========
Inlined here.  In the concatenation version they lived in
`pg_panel_core.py` — that file is gone; this is where the bindings
that use them are.
"""

PANEL_SCRUBS_JS = r"""
import {
  quads, floatSquares, coneState, SHAPE_STATE,
  view, cone,
  _TAU,
  _phiToDisplay, _displayToPhi,
  _patchPhiCenter, _patchSCenter,
  _setPatchPhiCenter, _setPatchSCenter,
  clampApex,
  syncPanelSliders, callDraw,
} from "./pg_state.js";
import {
  squareWorldWidth, squareWorldHeight,
  squareFlatCenter, setSquareFlatCenter,
  SHAPE_MIN_SCALE, SHAPE_MAX_SCALE,
  SHAPE_SLOPE_MIN, SHAPE_SLOPE_MAX,
  SHAPE_REL_SIZE,
} from "./pg_view_squares_core.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

/* ==========================================================================
   SCRUB RATES
   ========================================================================== */

const PATCH_PHI_SCRUB_RATE = 0.008 / Math.PI;
const PATCH_S_SCRUB_RATE   = 0.0015;
const PATCH_SNAP_PHI_NORM  = 1 / 12;

const SQUARE_SIZE_SCRUB_RATE = 0.005;
const SQUARE_SIZE_SNAP_STEP  = 0.25;

const SQUARE_PHI_SCRUB_RATE = 0.008;
const SQUARE_S_SCRUB_RATE   = 0.0015;

const SLOPE_SCRUB_RATE_PER_PX = 0.005;
const SLOPE_SNAP_STEP         = 0.1;

const MIRROR_ANGLE_SCRUB_RATE_DEG_PER_PX = 1.0;

const PATCH_WARP_SCRUB_RATE = 0.005;
const PATCH_WARP_SNAP_STEP  = 0.1;

/* ==========================================================================
   SCRUB-INPUT BINDINGS
   ========================================================================== */

(function installScrubs() {

  /* ---- patch position ------------------------------------------- */

  const phiInput = document.getElementById("patchPhiVal");
  if (phiInput) _installScrubInput(phiInput, {
    rate: PATCH_PHI_SCRUB_RATE,
    snapStep: PATCH_SNAP_PHI_NORM,
    read: () => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return 0;
      return _phiToDisplay(_patchPhiCenter(quads[i]));
    },
    write: (raw) => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return;
      _setPatchPhiCenter(quads[i], _displayToPhi(raw));
      pgkitNotifyLocal("patch.phi");
    },
  });

  const sInput = document.getElementById("patchSVal");
  if (sInput) _installScrubInput(sInput, {
    rate: PATCH_S_SCRUB_RATE,
    read: () => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return 0;
      return _patchSCenter(quads[i]);
    },
    write: (raw) => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return;
      _setPatchSCenter(quads[i], raw);
      pgkitNotifyLocal("patch.s");
    },
  });

  /* ---- mirror angle (with symmetric-on-shift) ------------------- */

  const mirrorAngleInput = document.getElementById("patchMirrorAngle");
  if (mirrorAngleInput) _installScrubInput(mirrorAngleInput, {
    rate: MIRROR_ANGLE_SCRUB_RATE_DEG_PER_PX * Math.PI / 180,
    symmetricOnShift: true,

    read: () => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return Math.PI;
      const q = quads[i];
      return (typeof q.mirrorAngle === "number") ? q.mirrorAngle : Math.PI;
    },

    onDragStart: (drag) => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return;
      drag.startPhi = _patchPhiCenter(quads[i]);
    },

    write: (raw, drag) => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return;
      const q = quads[i];
      if (drag && typeof drag.startPhi === "number") {
        _setPatchPhiCenter(q, drag.startPhi);
        pgkitNotifyLocal("patch.phi");
      }
      q.mirrorAngle = raw;
      pgkitNotifyLocal("patch.mirrorAngle");
    },

    writeSymmetric: (raw, drag) => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return;
      const q = quads[i];
      const startAngle = drag.startValue;
      const startPhi = (typeof drag.startPhi === "number")
                         ? drag.startPhi
                         : _patchPhiCenter(q);
      const delta = raw - startAngle;
      q.mirrorAngle = startAngle + delta;
      _setPatchPhiCenter(q, startPhi - delta / 2);
      pgkitNotifyLocal("patch.mirrorAngle");
      pgkitNotifyLocal("patch.phi");
    },
  });

  /* ---- plane warp ----------------------------------------------- */

  const warpInput = document.getElementById("patchWarp");
  if (warpInput) _installScrubInput(warpInput, {
    rate: PATCH_WARP_SCRUB_RATE,
    snapStep: PATCH_WARP_SNAP_STEP,
    read: () => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return 0;
      const q = quads[i];
      return (typeof q.warp === "number") ? q.warp : 0;
    },
    write: (raw) => {
      const i = coneState.selectedQuad;
      if (i < 0 || i >= quads.length) return;
      const q = quads[i];
      q.warp = Math.max(0, Math.min(1, raw));
      pgkitNotifyLocal("patch.warp");
    },
  });

  /* ---- square width / height ------------------------------------ */

  const wInput = document.getElementById("squareWVal");
  if (wInput) _installScrubInput(wInput, {
    rate: SQUARE_SIZE_SCRUB_RATE,
    snapStep: SQUARE_SIZE_SNAP_STEP,
    read: () => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return 0;
      return squareWorldWidth(floatSquares[i]);
    },
    write: (raw) => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return;
      const sq = floatSquares[i];
      sq.scaleU = Math.max(SHAPE_MIN_SCALE,
                  Math.min(SHAPE_MAX_SCALE, raw / SHAPE_REL_SIZE));
      pgkitNotifyLocal("square.width");
    },
  });

  const hInput = document.getElementById("squareHVal");
  if (hInput) _installScrubInput(hInput, {
    rate: SQUARE_SIZE_SCRUB_RATE,
    snapStep: SQUARE_SIZE_SNAP_STEP,
    read: () => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return 0;
      return squareWorldHeight(floatSquares[i]);
    },
    write: (raw) => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return;
      const sq = floatSquares[i];
      sq.scaleV = Math.max(SHAPE_MIN_SCALE,
                  Math.min(SHAPE_MAX_SCALE, raw / SHAPE_REL_SIZE));
      pgkitNotifyLocal("square.height");
    },
  });

  /* ---- slope ---------------------------------------------------- */

  const slopeInput = document.getElementById("squareSlopeVal");
  if (slopeInput) _installScrubInput(slopeInput, {
    rate: SLOPE_SCRUB_RATE_PER_PX,
    snapStep: SLOPE_SNAP_STEP,
    read: () => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return 0;
      return floatSquares[i].slope || 0;
    },
    write: (raw) => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return;
      const s = Math.max(SHAPE_SLOPE_MIN, Math.min(SHAPE_SLOPE_MAX, raw));
      floatSquares[i].slope = s;
      pgkitNotifyLocal("square.slope");
    },
  });

  /* ---- global standing multiplier ------------------------------- */

  const multInput = document.getElementById("shapeStandingMultVal");
  if (multInput) _installScrubInput(multInput, {
    rate: 0.005,
    snapStep: 0.1,
    read: () => SHAPE_STATE.standingMult,
    write: (raw) => {
      SHAPE_STATE.standingMult = Math.max(0.05, Math.min(10, raw));
      pgkitNotifyLocal("shape.standingMult");
    },
  });

  /* ---- square flat-view φ / s ----------------------------------- */

  const squarePhiInput = document.getElementById("squarePhiVal");
  if (squarePhiInput) _installScrubInput(squarePhiInput, {
    rate: SQUARE_PHI_SCRUB_RATE,
    read: () => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return 0;
      const c = squareFlatCenter(floatSquares[i]);
      if (!c) return 0;
      let p = c.phi;
      p = ((p % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
      return p;
    },
    write: (raw) => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return;
      const sq = floatSquares[i];
      const c = squareFlatCenter(sq);
      if (!c) return;
      setSquareFlatCenter(sq, raw, c.s);
      pgkitNotifyLocal("square.phi");
    },
  });

  const squareSInput = document.getElementById("squareSVal");
  if (squareSInput) _installScrubInput(squareSInput, {
    rate: SQUARE_S_SCRUB_RATE,
    read: () => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return 0;
      const c = squareFlatCenter(floatSquares[i]);
      if (!c) return 0;
      return c.s;
    },
    write: (raw) => {
      const i = coneState.selectedSquare;
      if (i < 0 || i >= floatSquares.length) return;
      const sq = floatSquares[i];
      const c = squareFlatCenter(sq);
      if (!c) return;
      setSquareFlatCenter(sq, c.phi, raw);
      pgkitNotifyLocal("square.s");
    },
  });

})();

/* ==========================================================================
   NUMBER FIELD REGISTRATIONS
   ==========================================================================
   Every number the remote panel sees.  `patch.phi` and
   `patch.mirrorAngle` are exposed in DISPLAY units (normalized for φ,
   degrees for the angle) — matching pair's `pair.inclination` in
   degrees.  The getter/setter convert to/from radians internally. */

/* ---- patch fields (selection-scoped) -------------------------- */

pgkitRegisterField("patch.phi", {
  get: () => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return 0;
    return _phiToDisplay(_patchPhiCenter(quads[i]));
  },
  set: (v) => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    _setPatchPhiCenter(quads[i], _displayToPhi(r));
    callDraw();
  },
  meta: { kind: "number", label: "Patch \u03C6 (normalized, -1..1)" },
});

pgkitRegisterField("patch.s", {
  get: () => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return 0;
    return _patchSCenter(quads[i]);
  },
  set: (v) => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    _setPatchSCenter(quads[i], r);
    callDraw();
  },
  meta: { kind: "number", label: "Patch s", min: 0, max: 1 },
});

pgkitRegisterField("patch.mirrorAngle", {
  get: () => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return 180;
    const q = quads[i];
    const ang = (typeof q.mirrorAngle === "number") ? q.mirrorAngle : Math.PI;
    return ang * 180 / Math.PI;
  },
  set: (v) => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    quads[i].mirrorAngle = r * Math.PI / 180;
    callDraw();
  },
  meta: { kind: "number", label: "Mirror \u2220 (deg)" },
});

pgkitRegisterField("patch.warp", {
  get: () => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return 0;
    return (typeof quads[i].warp === "number") ? quads[i].warp : 0;
  },
  set: (v) => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    quads[i].warp = Math.max(0, Math.min(1, r));
    callDraw();
  },
  meta: { kind: "number", label: "Plane warp", min: 0, max: 1 },
});

/* ---- square fields (selection-scoped) ------------------------- */

pgkitRegisterField("square.phi", {
  get: () => {
    const i = coneState.selectedSquare;
    if (i < 0 || i >= floatSquares.length) return 0;
    const c = squareFlatCenter(floatSquares[i]);
    if (!c) return 0;
    return ((c.phi % _TAU) + _TAU) % _TAU;
  },
  set: (v) => {
    const i = coneState.selectedSquare;
    if (i < 0 || i >= floatSquares.length) return;
    const sq = floatSquares[i];
    const c = squareFlatCenter(sq);
    if (!c) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    setSquareFlatCenter(sq, r, c.s);
    callDraw();
  },
  meta: { kind: "number", label: "Square \u03C6 (rad)" },
});

pgkitRegisterField("square.s", {
  get: () => {
    const i = coneState.selectedSquare;
    if (i < 0 || i >= floatSquares.length) return 0;
    const c = squareFlatCenter(floatSquares[i]);
    if (!c) return 0;
    return c.s;
  },
  set: (v) => {
    const i = coneState.selectedSquare;
    if (i < 0 || i >= floatSquares.length) return;
    const sq = floatSquares[i];
    const c = squareFlatCenter(sq);
    if (!c) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    setSquareFlatCenter(sq, c.phi, r);
    callDraw();
  },
  meta: { kind: "number", label: "Square s" },
});

pgkitRegisterField("square.width", {
  get: () => {
    const i = coneState.selectedSquare;
    return (i < 0) ? 0 : squareWorldWidth(floatSquares[i]);
  },
  set: (v) => {
    const i = coneState.selectedSquare;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    floatSquares[i].scaleU = Math.max(SHAPE_MIN_SCALE,
      Math.min(SHAPE_MAX_SCALE, r / SHAPE_REL_SIZE));
    callDraw();
  },
  meta: { kind: "number", label: "Square W" },
});

pgkitRegisterField("square.height", {
  get: () => {
    const i = coneState.selectedSquare;
    return (i < 0) ? 0 : squareWorldHeight(floatSquares[i]);
  },
  set: (v) => {
    const i = coneState.selectedSquare;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    floatSquares[i].scaleV = Math.max(SHAPE_MIN_SCALE,
      Math.min(SHAPE_MAX_SCALE, r / SHAPE_REL_SIZE));
    callDraw();
  },
  meta: { kind: "number", label: "Square H" },
});

pgkitRegisterField("square.slope", {
  get: () => {
    const i = coneState.selectedSquare;
    return (i < 0) ? 0 : (floatSquares[i].slope || 0);
  },
  set: (v) => {
    const i = coneState.selectedSquare;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    floatSquares[i].slope = Math.max(SHAPE_SLOPE_MIN, Math.min(SHAPE_SLOPE_MAX, r));
    callDraw();
  },
  meta: { kind: "number", label: "Slope", min: 0, max: 1 },
});

/* ---- shape global --------------------------------------------- */

pgkitRegisterField("shape.standingMult", {
  get: () => SHAPE_STATE.standingMult,
  set: (v) => {
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    SHAPE_STATE.standingMult = Math.max(0.05, Math.min(10, r));
    callDraw();
  },
  meta: { kind: "number", label: "Standing multiplier", min: 0.05, max: 10 },
});

/* ---- cone apex ------------------------------------------------ */

pgkitRegisterField("cone.apexX", {
  // Expose the INTENT (axRaw), not the clamped display. A remote
  // client that writes 10 reads back 10 even at a depth where the
  // displayed apex is clamped to 8.49; if depth grows the displayed
  // apex will drift to meet the intent. Exposing the clamped value
  // would make every remote edit silently lossy at low depth.
  get: () => cone.axRaw,
  set: (v) => {
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    cone.axRaw = r;
    clampApex();
    callDraw();
  },
  meta: { kind: "number", label: "Apex X" },
});

pgkitRegisterField("cone.apexY", {
  get: () => cone.ayRaw,
  set: (v) => {
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    cone.ayRaw = r;
    clampApex();
    callDraw();
  },
  meta: { kind: "number", label: "Apex Y" },
});

/* ---- view ----------------------------------------------------- */

pgkitRegisterField("view.zoom", {
  get: () => view.zoom,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.zoom = Math.max(0.05, Math.min(8, n));
    callDraw();
  },
  meta: { kind: "number", label: "Zoom", min: 0.05, max: 8 },
});

pgkitRegisterField("view.panX", {
  get: () => view.panX,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panX = n;
    callDraw();
  },
  meta: { kind: "number", label: "Pan X" },
});

pgkitRegisterField("view.panY", {
  get: () => view.panY,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    view.panY = n;
    callDraw();
  },
  meta: { kind: "number", label: "Pan Y" },
});
"""
