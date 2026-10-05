"""
pg_export.py — JSON export of the current visual state.

Dumps everything a person would need to compare the two views: the
cone state, the single SHAPE_DEPTH multiplier, the transform, and for
every patch and every shape the raw stored fields plus the fully-
derived screen positions in BOTH views.

The design goal is diffability.  For each shape:

    cornersLocal         world-local (u, v), rotated by θ, with the
                         single SHAPE_DEPTH multiplier applied.  This
                         is the ONE array both views read.

    cornersWorld         cornersLocal mapped to world coordinates
    cornersScreenCone    projected to screen with per-vertex
                         perspective
    cornersFlatParam     mapped to the flat view's (phi, s)
    cornersScreenFlat    screen position in the flat band

    screenSideLengthsCone  four side lengths, in screen pixels
    screenSideLengthsFlat  four side lengths, in screen pixels

If the four side lengths of a shape are all equal in a given view,
that shape appears as a screen square in that view.  If they are not,
they are what tell you by how much.

The export is a data: URL the browser downloads.  No server involved.
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
    schemaVersion: 2,
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

    shapeDepth:     SHAPE_DEPTH,
    perspStrength:  SHAPE_PERSP_STRENGTH,

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
      quadId: sq.quadId,
      index:  i,
      u:      _round3(sq.u),
      v:      _round3(sq.v),
      scaleU: _round3(sq.scaleU),
      scaleV: _round3(sq.scaleV),
      thetaRad: _round3(sq.theta || 0),
      thetaDeg: _round3((sq.theta || 0) * 180 / Math.PI),
    };

    if (!f) {
      out.shapes.push(entry);
      continue;
    }

    entry.uLen = _round3(f.uLen);
    entry.vLen = _round3(f.vLen);

    /* ---- The one array both views read ------------------------ */

    const local = squareCornersLocal(sq);
    const world = local.map(([u, v]) => frameToWorld(f, u, v));
    const dims  = squareDims(sq);

    entry.dims = { w: _round3(dims.w), h: _round3(dims.h) };
    entry.cornersLocal = local.map(p =>
      [_round3(p[0]), _round3(p[1])]);
    entry.cornersWorld = world.map(p =>
      [_round3(p[0]), _round3(p[1])]);
    entry.worldSideLengths = _worldSideLengths(world);

    /* ---- Cone-band projection --------------------------------- */

    const screenCone = squareCornersScreen(sq);
    if (screenCone) {
      entry.cornersScreenCone = screenCone.map(p =>
        [_round3(p[0]), _round3(p[1])]);
      entry.screenSideLengthsCone = _screenSideLengths(screenCone);
    }

    /* ---- Flat-band projection --------------------------------- */

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
