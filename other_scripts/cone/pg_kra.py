"""
pg_kra.py — panel-side glue for the "Generate .kra" button.

For each floating square, two things are captured:

  1. the shape's CONE-VIEW screen quadrilateral, from
     squareCornersScreen — the perspective-tapered, horizon-clipped,
     slope-offset polygon the cone band itself renders;

  2. a label record: the shape's display name, the polygon's
     centroid, a font size proportional to the polygon's own size,
     and a rotation.

LABEL ORIENTATION
=================
Two rules decide how the text is oriented on its shape:

  RULE — LONGEST-SIDE-IS-THE-BASE
      The text's baseline runs along the LONGEST edge of the shape's
      projected quad.  When a shape renders wide, the text reads
      horizontally; when a shape renders tall (because theta has
      turned it, or because Ku/Kv foreshortens the width), the text
      reads vertically.  The longest visible edge is what the eye
      reads as the base, so that is what the baseline follows.

  RULE — FLOOR-IS-DOWN
      Along that baseline direction there are two reading
      directions, differing by 180°.  The one chosen is the one
      whose text-up vector has a positive dot product with screen
      up, (0, -1).  Screen down is the floor; letters stand on it.
      This is the reading sense a viewer expects without having to
      reason about the cone's geometry.

Together these replace the earlier "run the text along the shape's
U axis" rule and the intermediate "away from the cone's base
centre" rule.  The first ignored projected aspect ratio; the second
failed on shapes whose position made "away from the base" point
sideways or downward on screen.

Each shape becomes one vector layer in the .kra holding both the
polygon and the text.

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
  /* A 50/50 mix toward white: readable on the dark fill of a shape
     whose own hue is drawn at low alpha. */
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
   so we re-project the four local corners without clipping.  If a
   corner lies past the horizon its screen position is meaningless,
   but the shape would be heavily obscured in that case anyway. */
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

/* The direction of the longest edge of a polygon, as a screen-space
   unit vector.  For the four-corner unclipped quad this is the
   visual baseline direction of the shape. */
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
   LABEL ROTATION
   ==========================================================================
   RULE — LONGEST-SIDE-IS-THE-BASE
       The baseline direction is the direction of the longest edge of
       the projected quad.  Not the shape's U axis: after rotation by
       theta and the Ku/Kv aspect correction, the visually longest
       side is what a viewer sees as the base, and that is what the
       text should sit on.

   RULE — FLOOR-IS-DOWN
       Given the baseline direction there are two possible reading
       directions, differing by 180°.  The one chosen is the one
       whose text-up vector has a positive dot product with screen
       up, (0, -1).

       Screen y is down, so for a text whose reading direction is
       the unit vector (rx, ry), the text-up vector is (ry, -rx).
       The screen-up vector is (0, -1).  Their dot product is
       ry · 0 + (-rx) · (-1) = rx.  So the condition "text-up has a
       positive component along screen-up" reduces to "rx > 0", and
       we flip the baseline direction if rx < 0. */
function _shapeLabelRotationDeg(sq, screenPts) {
  const corners4 = _unclippedProjectedCorners(sq);
  if (!corners4 || corners4.length < 3) return 0;

  const baseDir = _longestEdgeDir(corners4);
  if (!baseDir) return 0;

  let bx = baseDir[0];
  let by = baseDir[1];

  /* FLOOR-IS-DOWN: text-up must have a positive component along
     screen-up.  With text-up = (by, -bx) and screen-up = (0, -1),
     that condition reduces to bx > 0.  Flip the reading direction
     when it fails. */
  if (bx < 0) { bx = -bx; by = -by; }

  return Math.atan2(by, bx) * 180 / Math.PI;
}

function buildKraShapes() {
  const shapes = [];
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi < 0) continue;
    const q = quads[qi];
    if (!q) continue;

    /* squareCornersScreen returns the same quadrilateral the cone
       band draws: perspective-tapered, horizon-clipped, and (if the
       shape is tilted) slope-offset.  That is the shape the user
       sees; that is the shape we export. */
    const pts = squareCornersScreen(sq);
    if (!pts || pts.length < 3) continue;

    const hue = patchHue(q);
    const hex = _hueToHex(hue);
    const hexLight = _hueToHexLight(hue);

    /* Label.  sqrt(area) is a robust size estimate that stays
       correct for rotated and clipped polygons; the 0.30 factor
       matches the pill-to-shape proportion in the playground. */
    const centroid = _polygonCentroid(pts);
    const area = _polygonArea(pts);
    const fontPx = Math.max(6, Math.sqrt(area) * 0.30);

    /* Longest-side-is-the-base, floor-is-down.  See
       _shapeLabelRotationDeg. */
    const rotDeg = _shapeLabelRotationDeg(sq, pts);

    shapes.push({
      name:           squareDisplayName(sq),
      points:         pts.map(([x, y]) => [x, y]),
      fill:           hex,
      fill_opacity:   0.40,
      stroke:         hex,
      stroke_width:   2.0,
      stroke_opacity: 1.0,
      label: {
        text:     squareDisplayName(sq),
        x:        centroid[0],
        y:        centroid[1],
        font_px:  fontPx,
        rotation: rotDeg,
        color:    hexLight,
      },
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
