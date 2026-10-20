"""
pg_export.py — JSON export of the current visual state.

ES module.  Pure read-only walk.  Writes `cone_visual_state.json`.

Per patch and per shape, dumps raw stored fields, derived dimensions
(flat-view and cone-view), world and screen positions, and — for
hinged shapes — the plane-intersection hint.

DIAGNOSTIC USE ONLY
===================
Nothing else reads this file.  `shapeDepthCone` is read live from
`SHAPE_STATE.depthCone` on every export, so slider moves between
exports are reflected accurately.

APEX — BOTH INTENT AND DISPLAY
==============================
`cone.ax` / `cone.ay` are the clamped display values the renderer
and every projection in the file above use.  `cone.axRaw` /
`cone.ayRaw` are the user's intent.  The two differ only when the
intended apex lies outside the base radius (98% clamp); the diff is
the visible signature of the clamp.  When diagnosing "why does this
scene look shifted after a zoom change", compare the two — a raw
value outside the displayed radius means the constraint, not a bug,
is doing the shifting.

Schema version moved from 10 to 11 for the two new fields.  A
consumer that keyed on the version to parse older dumps is expected
to treat 11 as a superset of 10 — the older fields are unchanged.
"""

EXPORT_JS = r"""
import {
  quads, floatSquares, cone, view, layout, SHAPE_STATE,
  quadCorners, surfacePoint, quadIdxById, coneR, w2s,
} from "./pg_state.js";
import {
  patchFrame,
  squareCornersLocal,
  squareDims, squareDimsCone,
  shapePerspCentre, shapeVMax, shapeVExtentNorm,
  squareDisplayName, squareFlatCorners, frameToWorld,
} from "./pg_view_squares_core.js";
import { squareCornersScreen } from "./pg_view_squares_proj.js";
import { flatToScreen } from "./pg_flat_math.js";
import { flashStatus } from "./pgkit/panel_core.js";

function _round3(v) { return Math.round(v * 1000) / 1000; }

function _screenSideLengths(pts) {
  const out = [];
  for (let i = 0; i < 4; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % 4];
    out.push(_round3(Math.hypot(b[0] - a[0], b[1] - a[1])));
  }
  return out;
}

function _worldSideLengths(pts) {
  const out = [];
  for (let i = 0; i < 4; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % 4];
    out.push(_round3(Math.hypot(b[0] - a[0], b[1] - a[1])));
  }
  return out;
}

export function buildVisualStateExport() {
  const out = {
    schemaVersion: 11,
    generatedAt:   new Date().toISOString(),

    cone: {
      ax:            _round3(cone.ax),
      ay:            _round3(cone.ay),
      axRaw:         _round3(cone.axRaw),
      ayRaw:         _round3(cone.ayRaw),
      depth:         _round3(cone.depth),
      halfAngleRad:  _round3(cone.halfAngle),
      halfAngleDeg:  _round3(cone.halfAngle * 180 / Math.PI),
      ringCount:     cone.ringCount,
      meridianCount: cone.meridianCount,
      R_world:       _round3(coneR()),
    },

    shapeDepthCone: SHAPE_STATE.depthCone,
    shapeDepthFlat: 1.00,

    view: {
      scale: _round3(view.scale),
      tx:    _round3(view.tx),
      ty:    _round3(view.ty),
      zoom:  _round3(view.zoom),
    },

    layout: {
      coneH:     layout.coneH,
      dividerY:  layout.dividerY,
      reservedY: layout.reservedY,
      reservedH: layout.reservedH,
    },

    patches: [],
    shapes:  [],
  };

  for (const q of quads) {
    const qi = quads.indexOf(q);
    const f = patchFrame(qi);

    const entry = {
      id:   q.id,
      name: q.name,
      phi0: _round3(q.phi0),
      phi1: _round3(q.phi1),
      s0:   _round3(q.s0),
      s1:   _round3(q.s1),
      warp: _round3((typeof q.warp === "number") ? q.warp : 0),
    };

    if (f) {
      entry.frame = {
        Cx:   _round3(f.Cx),
        Cy:   _round3(f.Cy),
        Ux:   _round3(f.Ux),
        Uy:   _round3(f.Uy),
        Vx:   _round3(f.Vx),
        Vy:   _round3(f.Vy),
        uLen: _round3(f.uLen),
        vLen: _round3(f.vLen),
        det:  _round3(f.det),
      };

      entry.cornersScreenCone = quadCorners(q).map(c => {
        const [wx, wy] = surfacePoint(c.phi, c.s);
        const [sx, sy] = w2s(wx, wy);
        return [_round3(sx), _round3(sy)];
      });

      entry.cornersScreenFlat = quadCorners(q).map(c => {
        const [sx, sy] = flatToScreen(c.phi, c.s);
        return [_round3(sx), _round3(sy)];
      });
    }

    out.patches.push(entry);
  }

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi < 0) continue;
    const f = patchFrame(qi);

    const entry = {
      id:     sq.id,
      name:   squareDisplayName(sq),
      quadId: sq.quadId,
      index:  i,
      u:      _round3(sq.u),
      v:      _round3(sq.v),
      scaleU: _round3(sq.scaleU),
      scaleV: _round3(sq.scaleV),
      thetaRad: _round3(sq.theta || 0),
      thetaDeg: _round3((sq.theta || 0) * 180 / Math.PI),
      slope: _round3(sq.slope || 0),
      visualBottom: !!sq.visualBottom,
    };

    if (!f) {
      out.shapes.push(entry);
      continue;
    }

    entry.uLen = _round3(f.uLen);
    entry.vLen = _round3(f.vLen);
    entry.extentVNorm = _round3(shapeVExtentNorm(sq));

    const local = squareCornersLocal(sq);
    const world = local.map(([u, v]) => frameToWorld(f, u, v));
    const dims  = squareDims(sq);

    entry.dims     = { w: _round3(dims.w), h: _round3(dims.h) };
    entry.dimsCone = (function () {
      const d = squareDimsCone(sq);
      return { w: _round3(d.w), h: _round3(d.h) };
    })();

    entry.cornersLocal = local.map(p => [_round3(p[0]), _round3(p[1])]);
    entry.cornersWorld = world.map(p => [_round3(p[0]), _round3(p[1])]);
    entry.worldSideLengths = _worldSideLengths(world);

    entry.perspCentre = _round3(shapePerspCentre(sq));
    entry.vMax        = _round3(shapeVMax(sq));

    const screenCone = squareCornersScreen(sq);
    if (screenCone) {
      entry.cornersScreenCone = screenCone.map(p => [_round3(p[0]), _round3(p[1])]);
      entry.screenSideLengthsCone = _screenSideLengths(screenCone);
    }

    const flatCorners = squareFlatCorners(sq);
    if (flatCorners) {
      const screenFlat = flatCorners.map(([phi, s]) => flatToScreen(phi, s));
      entry.cornersFlatParam = flatCorners.map(p => [_round3(p[0]), _round3(p[1])]);
      entry.cornersScreenFlat = screenFlat.map(p => [_round3(p[0]), _round3(p[1])]);
      entry.screenSideLengthsFlat = _screenSideLengths(screenFlat);
    }

    out.shapes.push(entry);
  }

  return out;
}

export function exportVisualStateJSON() {
  const data = buildVisualStateExport();
  const text = JSON.stringify(data, null, 2);
  const url  = "data:application/json;charset=utf-8,"
             + encodeURIComponent(text);

  const a = document.createElement("a");
  a.href = url;
  a.download = "cone_visual_state.json";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  flashStatus("Exported " + quads.length + " patch(es) and " +
              floatSquares.length + " shape(s)", "ok");
}
"""
