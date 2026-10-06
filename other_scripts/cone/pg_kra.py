"""
pg_kra.py — panel-side glue for the "Generate .kra" button.

For each floating square, three things are captured:

  1. the four projected corners of the shape, from
     _unclippedProjectedCorners;

  2. the natural width and height of the shape, as the average of
     the two "U-direction" projected edges and the two
     "V-direction" projected edges;

  3. a flat-frame rotation for the text, so that when the transform
     mask projects the whole group, the text's baseline lands along
     the shape's longer projected side and reads floor-is-down.

FLAT-FRAME TEXT ROTATION
========================
The .kra builds each shape as a flat rectangle with horizontal text
inside its own group, and lets a perspective transform mask do the
projection.  The rectangle's four corners map onto the shape's four
projected corners, in TL, TR, BR, BL order.  Consequently:

    flat +X axis   projects to   the projected U direction
                                 (TL to TR of the shape)
    flat +Y axis   projects to   the projected V direction
                                 (TL to BL of the shape)

The text is drawn at a flat rotation R ∈ {0, 90, 180, 270}.  In
SVG's y-down convention, that reads:

    R = 0     baseline along flat +X   →  screen angle of U
    R = 90    baseline along flat +Y   →  screen angle of V
    R = 180   baseline along flat -X   →  screen angle of U + 180
    R = 270   baseline along flat -Y   →  screen angle of V + 180

We already know the correct final screen angle for the text — it is
what _shapeLabelRotationDeg returns, i.e. the longest-edge direction
with the floor-is-down flip applied.  So the correct pre-rotation is
whichever of those four candidates projects to a screen angle
closest to that target.

Do NOT use squareFlatCorners here — that returns the unfolded-cone
footprint, which loses the perspective.
"""

KRA_JS = r"""
/* ==========================================================================
   .KRA EXPORT
   ========================================================================== */

function _hueToHex(h) {
  const to2 = (v) => Math.max(0, Math.min(255, Math.round(v)))
                        .toString(16).padStart(2, "0");
  return "#" + to2(h.r) + to2(h.g) + to2(h.b);
}

function _hueToHexLight(h) {
  const mix = (v) => Math.round(v * 0.5 + 255 * 0.5);
  const to2 = (v) => v.toString(16).padStart(2, "0");
  return "#" + to2(mix(h.r)) + to2(mix(h.g)) + to2(mix(h.b));
}

function _polygonCentroid(pts) {
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / pts.length, cy / pts.length];
}

function _polygonArea(pts) {
  let area = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    area += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(area) / 2;
}

/* ==========================================================================
   UNCLIPPED PROJECTED CORNERS
   ==========================================================================
   squareCornersScreen runs the four local corners through
   projectShapePoint AND then clips against the horizon.  Clipping
   can replace a real edge with a clip-created one, and the longest
   edge of the clipped polygon may then not be an edge of the shape
   proper.

   For choosing the label's baseline we want the shape's OWN edges,
   so we re-project the four local corners without clipping. */
function _unclippedProjectedCorners(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const local = squareCornersLocalCone(sq);
  if (!local) return null;

  const vC = sq.v * f.vLen;
  const pC = shapePerspCentre(sq);
  const vMax = shapeVMax(sq);
  const phiC = _patchNormalPhi(qi);
  const slope = sq.slope || 0;

  return local.map(([u, v]) =>
    projectShapePoint(f, qi, u, v, vC, pC, vMax, phiC, slope));
}

function _longestEdgeDir(pts) {
  let bestLen = 0, bx = 0, by = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    if (len > bestLen) {
      bestLen = len;
      bx = dx / len;
      by = dy / len;
    }
  }
  if (bestLen < 1e-6) return null;
  return [bx, by];
}

/* ==========================================================================
   TARGET SCREEN ANGLE — longest side is the base, floor is down
   ==========================================================================
   The reading direction the text should end up with, in final
   screen coordinates.  Two rules:

     LONGEST-SIDE-IS-THE-BASE
       Baseline along the direction of the shape's longest projected
       edge.

     FLOOR-IS-DOWN
       Given that baseline direction there are two reading senses,
       differing by 180°.  The one whose text-up vector has a
       positive component along screen up, (0, -1), is the correct
       one.  With text-up = (by, -bx) and screen up = (0, -1), the
       condition reduces to bx > 0. */
function _shapeLabelScreenAngleDeg(sq, screenPts) {
  const corners = _unclippedProjectedCorners(sq);
  if (!corners || corners.length < 3) return 0;

  const baseDir = _longestEdgeDir(corners);
  if (!baseDir) return 0;

  let bx = baseDir[0];
  let by = baseDir[1];
  if (bx < 0) { bx = -bx; by = -by; }

  return Math.atan2(by, bx) * 180 / Math.PI;
}

/* ==========================================================================
   FLAT-FRAME TEXT ROTATION
   ==========================================================================
   See the module docstring for the derivation.  Returns a rotation
   in {0, 90, 180, 270} — the flat-frame text rotation that, after
   the perspective transform, produces the target screen angle. */
function _shapeLabelFlatRotationDeg(sq, screenPts) {
  const corners = _unclippedProjectedCorners(sq);
  if (!corners || corners.length < 4) return 0;

  /* Projected direction of the flat rectangle's +X axis: TL to TR. */
  const ux = corners[1][0] - corners[0][0];
  const uy = corners[1][1] - corners[0][1];

  /* Projected direction of the flat rectangle's +Y axis: TL to BL.
     (This is the reference-frame -V direction, but for our purposes
     it only matters that it is the flat frame's +Y.) */
  const vx = corners[3][0] - corners[0][0];
  const vy = corners[3][1] - corners[0][1];

  const angleU = Math.atan2(uy, ux) * 180 / Math.PI;
  const angleV = Math.atan2(vy, vx) * 180 / Math.PI;

  const target = _shapeLabelScreenAngleDeg(sq, screenPts);

  /* SVG rotate() is clockwise in a y-down frame, so a flat rotation
     of R places the text's baseline along the flat direction
     (cos R, sin R).  For R in {0, 90, 180, 270} the projected
     screen direction is exactly ±U or ±V. */
  const candidates = [
    { r:   0, projected: angleU },
    { r:  90, projected: angleV },
    { r: 180, projected: angleU + 180 },
    { r: 270, projected: angleV + 180 },
  ];

  const angDiff = (a, b) => {
    let d = a - b;
    while (d >  180) d -= 360;
    while (d < -180) d += 360;
    return d;
  };

  let best = candidates[0];
  let bestD = Math.abs(angDiff(candidates[0].projected, target));
  for (let i = 1; i < candidates.length; i++) {
    const d = Math.abs(angDiff(candidates[i].projected, target));
    if (d < bestD) { bestD = d; best = candidates[i]; }
  }
  return best.r;
}

function buildKraShapes() {
  const shapes = [];
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi < 0) continue;
    const q = quads[qi];
    if (!q) continue;

    const corners = _unclippedProjectedCorners(sq);
    if (!corners || corners.length < 4) continue;

    /* Side lengths in projected order: sides 0 and 2 are U, sides 1
       and 3 are V.  The averages give the natural W and H of the
       flat rectangle that will be laid out inside the group. */
    const lens = (function () {
      const n = corners.length;
      const s = [];
      for (let k = 0; k < n; k++) {
        const a = corners[k];
        const b = corners[(k + 1) % n];
        s.push(Math.hypot(b[0] - a[0], b[1] - a[1]));
      }
      return s;
    })();
    const W = (lens[0] + lens[2]) / 2;
    const H = (lens[1] + lens[3]) / 2;
    if (W <= 0.5 || H <= 0.5) continue;

    const hue = patchHue(q);
    const hex = _hueToHex(hue);
    const hexLight = _hueToHexLight(hue);

    /* Pre-rotation of the text in the flat frame, so that after the
       perspective transform its baseline lands along the longer
       projected side and reads floor-is-down. */
    const flatRotation = _shapeLabelFlatRotationDeg(sq, corners);

    shapes.push({
      name:            squareDisplayName(sq),
      points:          corners.map(([x, y]) => [x, y]),
      natural_w:       W,
      natural_h:       H,
      flat_rotation:   flatRotation,
      fill:            hex,
      fill_opacity:    0.40,
      stroke:          hex,
      stroke_width:    2.0,
      stroke_opacity:  1.0,
      text_color:      hexLight,
    });
  }
  return shapes;
}

async function generateKraFromScene() {
  if (floatSquares.length === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("No shapes to export", "warn");
    }
    return;
  }

  const shapes = buildKraShapes();
  if (shapes.length === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("No exportable shapes", "warn");
    }
    return;
  }

  const defaultPath = "/tmp/cone_scene.kra";
  const outputPath = window.prompt("Output .kra path:", defaultPath);
  if (!outputPath) return;

  if (typeof flashStatus === "function") {
    flashStatus("Generating .kra (" + shapes.length + " shape(s))...", "");
  }

  try {
    const response = await fetch("/generate-kra", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        doc_name:    "Cone Scene",
        output_path: outputPath,
        shapes:      shapes,
      }),
    });
    const result = await response.json();
    if (result && result.success) {
      if (typeof flashStatus === "function") {
        flashStatus(result.message || "Done", "ok");
      }
    } else {
      const msg = (result && result.message) ? result.message : "unknown error";
      if (typeof flashStatus === "function") {
        flashStatus("Failed: " + msg, "bad");
      }
    }
  } catch (e) {
    if (typeof flashStatus === "function") {
      flashStatus("Request failed: " + e.message, "bad");
    }
  }
}
"""
