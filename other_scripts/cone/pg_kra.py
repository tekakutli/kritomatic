"""
pg_kra.py — panel-side glue for the "Generate .kra" button.

The frame of reference is VISUAL, not flat, and specifically the
frame in which the TEXT reads horizontally.  When the user picks
"top-left", they mean the corner that is top-left in that frame —
the same frame that determined the text's baseline.

Meta-options
============
The panel exposes a small set of options that shape how the .kra is
built.  They are read at export time and travel in the payload to
the daemon, where cone_kra.py applies them:

    text_position      where in the visual frame the text anchor
                       sits (center, tl, tr, bl, br, top, bottom,
                       left, right)

    text_padding       a fraction of min(W, H), how far the anchor
                       sits from the edge it is attached to

    draw_rectangles    if false, the rectangle polygon is not added
                       to the .kra; only the text is drawn.

    text_color_mode    "color" (default) uses the shape's patch hue
                       as the text color; "black" forces pure black
                       on every label.

These options are also saved and loaded with the scene, via
pg_scene.py, which calls readKraOptionsFromDOM and
applyKraOptionsToDOM — the two functions below are the single point
of truth for what the options are and how they map to DOM controls.

Computing the text anchor
=========================
For each shape, the four projected corners are already available
(_unclippedProjectedCorners).  The text's reading direction on
screen is the longest projected edge, with a floor-is-down flip
(see _shapeLabelScreenAngleDeg).  That direction defines a frame:

    X axis = reading direction (bx, by)
    Y axis = (-by, bx)          (text-down, screen y is down)

Project the four corners into this frame.  In it, the corner roles
are unambiguous:

    top-left      min(X + Y)
    top-right     max(X - Y)
    bottom-right  max(X + Y)
    bottom-left   min(X - Y)

Once the flat corner that plays each visual role is identified, the
anchor is placed in the flat rectangle near that corner, offset
toward the flat center by padding plus half the text's own extent.

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

function _shapeLabelFlatRotationDeg(sq, screenPts) {
  const corners = _unclippedProjectedCorners(sq);
  if (!corners || corners.length < 4) return 0;

  const ux = corners[1][0] - corners[0][0];
  const uy = corners[1][1] - corners[0][1];

  const vx = corners[3][0] - corners[0][0];
  const vy = corners[3][1] - corners[0][1];

  const angleU = Math.atan2(uy, ux) * 180 / Math.PI;
  const angleV = Math.atan2(vy, vx) * 180 / Math.PI;

  const target = _shapeLabelScreenAngleDeg(sq, screenPts);

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

function _classifyCornersInReadingFrame(visualPts, readingAngleDeg) {
  const rad = readingAngleDeg * Math.PI / 180;
  const bx = Math.cos(rad);
  const by = Math.sin(rad);
  const dx = -by;
  const dy = bx;

  const coords = [];
  for (let i = 0; i < 4; i++) {
    const px = visualPts[i][0];
    const py = visualPts[i][1];
    coords.push({
      X: px * bx + py * by,
      Y: px * dx + py * dy,
    });
  }

  let tlI = 0, trI = 0, brI = 0, blI = 0;
  let bestTL = Infinity, bestTR = -Infinity;
  let bestBR = -Infinity, bestBL = Infinity;
  for (let i = 0; i < 4; i++) {
    const X = coords[i].X, Y = coords[i].Y;
    const sTL = X + Y;
    const sTR = X - Y;
    const sBR = X + Y;
    const sBL = X - Y;

    if (sTL < bestTL) { bestTL = sTL; tlI = i; }
    if (sTR > bestTR) { bestTR = sTR; trI = i; }
    if (sBR > bestBR) { bestBR = sBR; brI = i; }
    if (sBL < bestBL) { bestBL = sBL; blI = i; }
  }

  return { tl: tlI, tr: trI, br: brI, bl: blI };
}

function _flatAnchorFromVisual(visualPts, W, H, position,
                               padFrac, fontSize, textLen,
                               readingAngleDeg, flatRotationDeg) {
  if (!position || position === "center" ||
      !visualPts || visualPts.length < 4) {
    return { x: W / 2, y: H / 2 };
  }

  const pad = padFrac * Math.min(W, H);

  const textPixelLen = textLen * fontSize * 0.55;
  const textPixelH = fontSize;
  let textHalfW, textHalfH;
  if (flatRotationDeg === 90 || flatRotationDeg === 270) {
    textHalfW = textPixelH / 2;
    textHalfH = textPixelLen / 2;
  } else {
    textHalfW = textPixelLen / 2;
    textHalfH = textPixelH / 2;
  }

  const roles = _classifyCornersInReadingFrame(visualPts, readingAngleDeg);

  const flatCorners = [[0, 0], [W, 0], [W, H], [0, H]];

  function anchorForCorner(idx) {
    const fx = flatCorners[idx][0];
    const fy = flatCorners[idx][1];
    const dirX = (fx < W / 2) ? 1 : ((fx > W / 2) ? -1 : 0);
    const dirY = (fy < H / 2) ? 1 : ((fy > H / 2) ? -1 : 0);
    return {
      x: fx + dirX * (pad + textHalfW),
      y: fy + dirY * (pad + textHalfH),
    };
  }

  const aTL = anchorForCorner(roles.tl);
  const aTR = anchorForCorner(roles.tr);
  const aBR = anchorForCorner(roles.br);
  const aBL = anchorForCorner(roles.bl);

  if (position === "tl") return aTL;
  if (position === "tr") return aTR;
  if (position === "br") return aBR;
  if (position === "bl") return aBL;
  if (position === "top") {
    return { x: (aTL.x + aTR.x) / 2, y: (aTL.y + aTR.y) / 2 };
  }
  if (position === "bottom") {
    return { x: (aBL.x + aBR.x) / 2, y: (aBL.y + aBR.y) / 2 };
  }
  if (position === "left") {
    return { x: (aTL.x + aBL.x) / 2, y: (aTL.y + aBL.y) / 2 };
  }
  if (position === "right") {
    return { x: (aTR.x + aBR.x) / 2, y: (aTR.y + aBR.y) / 2 };
  }
  return { x: W / 2, y: H / 2 };
}

function buildKraShapes(options) {
  options = options || {};
  const textPos = options.text_position || "center";
  const padFrac = (typeof options.text_padding === "number")
                    ? options.text_padding : 0.06;
  const colorMode = options.text_color_mode || "color";

  const shapes = [];
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi < 0) continue;
    const q = quads[qi];
    if (!q) continue;

    const corners = _unclippedProjectedCorners(sq);
    if (!corners || corners.length < 4) continue;

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

    /* Text color.  "color" keeps the light-tinted patch hue that
       has been the default; "black" replaces it with pure black.
       The rectangle keeps the patch hue in both modes. */
    const textColor = (colorMode === "black") ? "#000000" : hexLight;

    const flatRotation = _shapeLabelFlatRotationDeg(sq, corners);
    const readingAngle  = _shapeLabelScreenAngleDeg(sq, corners);

    const name = squareDisplayName(sq);
    const fontNat = Math.min(W, H) * 0.40;

    const anchor = _flatAnchorFromVisual(
      corners, W, H, textPos, padFrac, fontNat, name.length,
      readingAngle, flatRotation
    );

    shapes.push({
      name:            name,
      points:          corners.map(([x, y]) => [x, y]),
      natural_w:       W,
      natural_h:       H,
      flat_rotation:   flatRotation,
      text_anchor:     anchor,
      fill:            hex,
      fill_opacity:    0.40,
      stroke:          hex,
      stroke_width:    2.0,
      stroke_opacity:  1.0,
      text_color:      textColor,
    });
  }
  return shapes;
}

/* ==========================================================================
   META-OPTIONS
   ==========================================================================
   The two functions below are the SINGLE POINT OF TRUTH for what the
   KRA-export options are.  pg_scene.py calls them when saving and
   loading a scene, so adding a new option here is enough to have it
   persisted with scenes as well — no changes needed in pg_scene.py.
   ========================================================================== */

function readKraOptionsFromDOM() {
  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");
  const colEl  = document.getElementById("kraTextColor");

  const text_position = posEl ? posEl.value : "center";

  let text_padding = padEl ? parseFloat(padEl.value) : 0.06;
  if (!isFinite(text_padding) || text_padding < 0) text_padding = 0.06;

  const draw_rectangles = rectEl ? !!rectEl.checked : true;

  let text_color_mode = colEl ? colEl.value : "color";
  if (text_color_mode !== "color" && text_color_mode !== "black") {
    text_color_mode = "color";
  }

  return {
    text_position:   text_position,
    text_padding:    text_padding,
    draw_rectangles: draw_rectangles,
    text_color_mode: text_color_mode,
  };
}

function applyKraOptionsToDOM(opts) {
  if (!opts || typeof opts !== "object") return;

  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");
  const colEl  = document.getElementById("kraTextColor");

  if (posEl && typeof opts.text_position === "string") {
    posEl.value = opts.text_position;
  }
  if (padEl && typeof opts.text_padding === "number") {
    padEl.value = String(opts.text_padding);
  }
  if (rectEl && typeof opts.draw_rectangles === "boolean") {
    rectEl.checked = opts.draw_rectangles;
  }
  if (colEl && typeof opts.text_color_mode === "string") {
    colEl.value = opts.text_color_mode;
  }
}

async function generateKraFromScene() {
  if (floatSquares.length === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("No shapes to export", "warn");
    }
    return;
  }

  const options = readKraOptionsFromDOM();
  const shapes = buildKraShapes(options);
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
        options:     options,
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
