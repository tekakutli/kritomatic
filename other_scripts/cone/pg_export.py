"""
pg_export.py — JSON export of the current visual state.

For each shape, dumps the raw stored fields, the single array both
views read (cornersLocal, rotated and depth-scaled in (u, v)), and
the derived screen positions and side lengths in BOTH views.

Diagnostic use: if screenSideLengthsCone for a rotated shape is a
permutation of the unrotated shape's screenSideLengthsCone, the two
render as the same screen polygon turned by θ.

Schema v4
=========
Removed `perspStrength` (the panel no longer exposes that toggle;
the constant was removed from pg_view_squares.py).  Added per-shape
`dimsSource` describing whether the shape's height is anchored to
the patch's uLen or vLen (currently vLen — see the note in
pg_view_squares.py), and `extentVNorm`, the shape's half-extent
along the patch's V axis in normalized coordinates.  Both fields
exist for diagnosing the horizon-clamp behaviour; nothing else reads
them.

Schema v5
=========
The single `shapeDepth` field is replaced by `shapeDepthCone` and
`shapeDepthFlat`.  The two views now scale the shape's height
independently: the cone view through `SHAPE_DEPTH_CONE` (driven by
the panel's "Shape depth" slider) and the flat view through the
fixed `SHAPE_DEPTH_FLAT`.  Per-shape, `dims` reports the FLAT-view
dimensions (the ones the flat renderer reads) and `dimsCone` reports
the CONE-view dimensions, so the export describes what each band
actually draws.

Schema v6
=========
Each shape gained a `slopeRad` / `slopeDeg` pair: a pseudo-3D tilt
about the shape's own reference U axis.  Superseded by schema v8.

Schema v7
=========
Each shape now carries a `visualBottom` boolean: the per-square flag
that switches the KRA export's text-anchor and corner-sense logic
from "longest edge" (false / unset) to "visual bottom of the scene"
(true).  Nothing on the render path reads it; it is included here so
the visual-state dump matches the scene file and the KRA export's
own inputs.  See pg_kra.py's `_shapeLabelScreenAngleDeg` and the
VISUAL BOTTOM note in pg_panel.py.

Schema v8
=========
The per-shape `slope` field is now a unitless hinge parameter in
[0, 1] rather than a tilt angle in radians.  At 0 the shape lies
flat on the patch; at 1 it has rotated a full 90 degrees about its
near edge (the edge of the shape facing the viewer) and stands
perpendicular to the patch plane.  The `slopeRad` / `slopeDeg`
pair from v6 is gone — there is a single `slope` field.  The cone
view reads it; the flat view still ignores it.  See the SLOPE
section in pg_view_squares.py.
"""

EXPORT_JS = r"""
/* ==========================================================================
   VISUAL STATE EXPORT
   ========================================================================== */

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

function buildVisualStateExport() {
  const out = {
    schemaVersion: 8,
    generatedAt:   new Date().toISOString(),

    cone: {
      ax:            _round3(cone.ax),
      ay:            _round3(cone.ay),
      depth:         _round3(cone.depth),
      halfAngleRad:  _round3(cone.halfAngle),
      halfAngleDeg:  _round3(cone.halfAngle * 180 / Math.PI),
      ringCount:     cone.ringCount,
      meridianCount: cone.meridianCount,
      R_world:       _round3(coneR()),
    },

    shapeDepthCone: SHAPE_DEPTH_CONE,
    shapeDepthFlat: SHAPE_DEPTH_FLAT,

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

    entry.cornersLocal = local.map(p =>
      [_round3(p[0]), _round3(p[1])]);
    entry.cornersWorld = world.map(p =>
      [_round3(p[0]), _round3(p[1])]);
    entry.worldSideLengths = _worldSideLengths(world);

    entry.perspCentre = _round3(shapePerspCentre(sq));
    entry.vMax        = _round3(shapeVMax(sq));

    const screenCone = squareCornersScreen(sq);
    if (screenCone) {
      entry.cornersScreenCone = screenCone.map(p =>
        [_round3(p[0]), _round3(p[1])]);
      entry.screenSideLengthsCone = _screenSideLengths(screenCone);
    }

    const flatCorners = squareFlatCorners(sq);
    if (flatCorners) {
      const screenFlat = flatCorners.map(([phi, s]) =>
        flatToScreen(phi, s));
      entry.cornersFlatParam = flatCorners.map(p =>
        [_round3(p[0]), _round3(p[1])]);
      entry.cornersScreenFlat = screenFlat.map(p =>
        [_round3(p[0]), _round3(p[1])]);
      entry.screenSideLengthsFlat = _screenSideLengths(screenFlat);
    }

    out.shapes.push(entry);
  }

  return out;
}

function exportVisualStateJSON() {
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

  if (typeof flashStatus === "function") {
    flashStatus("Exported " + quads.length + " patch(es) and " +
                floatSquares.length + " shape(s)", "ok");
  }
}
"""
