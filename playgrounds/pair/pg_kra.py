"""
pg_kra.py — panel-side glue for the "Generate .kra" button.

For each segment of each pair, the client computes the trapezoid's
four screen corners, and a TEXT item whose affine transform
reproduces the trapezoid's local frame at the text's anchor point.
A RECT item accompanies each filled segment.  All exported text is
drawn in black; the trapezoid rects keep the pair hue.

THE PARAMETRIC MAP AND JACOBIAN
===============================
[...unchanged math derivation, see earlier revisions...]

HANDEDNESS AND READING DIRECTION
================================
[...unchanged, see earlier revisions...]

SPLIT ENTRY POINT
=================
`generateKraFromScene()` asks the user for a destination path via
`window.prompt` and forwards to `generateKraToPath(path)`.

`generateKraToPath(outputPath)` does the actual export; it takes the
path as an argument so it can be called from a remote client that
has already gathered the path on the other side of the WebSocket
(where a `window.prompt` would be blocked by the browser's
user-gesture rule).  The playground's action registration calls
this function directly when its payload carries an `output_path`.

`response_body(b)` is a one-line helper retained so the fetch body
is built in a single place.
"""

KRA_JS = r"""
/* ==========================================================================
   COLOR HELPERS
   ========================================================================== */

function _hueToHex(h) {
  const to2 = (v) => Math.max(0, Math.min(255, Math.round(v)))
                        .toString(16).padStart(2, "0");
  return "#" + to2(h.r) + to2(h.g) + to2(h.b);
}

/* ==========================================================================
   SEGMENT PARAMETRIC MAP
   ========================================================================== */

function _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy,
                         s, t) {
  const u = a + s * k;
  const bCoef = off + u * (mB - mA);
  const wx = p.cx + u * mA * ux + t * bCoef * ux + t * gap * vx;
  const wy = p.cy + u * mA * uy + t * bCoef * uy + t * gap * vy;
  return [wx, wy];
}

/* ==========================================================================
   ONE SEGMENT → ONE (text, rect) PAIR
   ========================================================================== */

function _computeSegmentKraItem(p, seg, options) {
  const a  = seg.a;
  const b  = seg.b;
  const k  = b - a;
  if (Math.abs(k) < 1e-6) return null;

  const ux = Math.cos(p.angle);
  const uy = Math.sin(p.angle);
  const vx = -uy;
  const vy = ux;

  const pairScale = _pairScale(p);
  const viewScale = view.scale;

  const mA  = p.metricA * pairScale;
  const mB  = p.metricB * pairScale;
  const off = p.offset  * pairScale;
  const gap = p.gap     * pairScale;

  const w0 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 0, 0);
  const w1 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 1, 0);
  const w2 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 1, 1);
  const w3 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 0, 1);

  const c0 = w2s(w0[0], w0[1]);
  const c1 = w2s(w1[0], w1[1]);
  const c2 = w2s(w2[0], w2[1]);
  const c3 = w2s(w3[0], w3[1]);

  const lenBottom = Math.hypot(c1[0] - c0[0], c1[1] - c0[1]);
  const lenTop    = Math.hypot(c2[0] - c3[0], c2[1] - c3[1]);
  const lenLeft   = Math.hypot(c3[0] - c0[0], c3[1] - c0[1]);
  const lenRight  = Math.hypot(c2[0] - c1[0], c2[1] - c1[1]);

  const W_sq = (lenBottom + lenTop) / 2;
  const H_sq = (lenLeft   + lenRight) / 2;

  if (W_sq < 2 || H_sq < 2) return null;

  const fontNat = Math.min(W_sq, H_sq) * 0.40;

  const label = seg.name || ("S" + seg.id);

  const pos = options.text_position || "center";
  const padFrac = (typeof options.text_padding === "number")
                    ? options.text_padding : 0.06;
  const drawRects = options.draw_rectangles !== false;

  const textPixelLen = label.length * fontNat * 0.55;
  const textPixelH   = fontNat;
  const pad = padFrac * Math.min(W_sq, H_sq);

  let col = 1, row = 1;
  if      (pos === "tl")                    { col = 0; row = 0; }
  else if (pos === "tc" || pos === "top")   { col = 1; row = 0; }
  else if (pos === "tr")                    { col = 2; row = 0; }
  else if (pos === "ml" || pos === "left")  { col = 0; row = 1; }
  else if (pos === "mr" || pos === "right") { col = 2; row = 1; }
  else if (pos === "bl" || pos === "bottom"){ col = 0; row = 2; }
  else if (pos === "bc")                    { col = 1; row = 2; }
  else if (pos === "br")                    { col = 2; row = 2; }

  const flatX = (col === 0) ? 0 : (col === 2) ? W_sq : W_sq / 2;
  const flatY = (row === 0) ? 0 : (row === 2) ? H_sq : H_sq / 2;
  const dirX  = (col === 0) ? 1 : (col === 2) ? -1 : 0;
  const dirY  = (row === 0) ? 1 : (row === 2) ? -1 : 0;

  const cxFlat = flatX + dirX * (pad + textPixelLen / 2);
  const cyFlat = flatY + dirY * (pad + textPixelH   / 2);

  let alignment;
  if (col === 0) alignment = "left";
  else if (col === 2) alignment = "right";
  else alignment = "center";

  let anchorFlatX = cxFlat;
  if (alignment === "left")  anchorFlatX -= textPixelLen / 2;
  if (alignment === "right") anchorFlatX += textPixelLen / 2;
  const anchorFlatY = cyFlat;

  const s = anchorFlatX / W_sq;
  const t = 1 - anchorFlatY / H_sq;

  const anchorWorld  = _segmentWorldAt(
    p, a, k, mA, mB, off, gap, ux, uy, vx, vy, s, t);
  const [ax, ay] = w2s(anchorWorld[0], anchorWorld[1]);

  const u = a + s * k;
  const Ja = k * (mA + t * (mB - mA));
  const Jb = (off + u * (mB - mA));

  const J00 =  viewScale * Ja * ux;
  const J10 = -viewScale * Ja * uy;
  const J01 =  viewScale * (Jb * ux + gap * vx);
  const J11 = -viewScale * (Jb * uy + gap * vy);

  const sLen = Math.hypot(J00, J10);
  const tLen = Math.hypot(J01, J11);
  const maxScale = Math.max(sLen, tLen);
  if (maxScale < 1e-9) return null;
  const invScale = 1 / maxScale;

  const sHoriz = Math.abs(J00) / (sLen || 1);
  const tHoriz = Math.abs(J01) / (tLen || 1);
  const readAlongS = (sHoriz >= tHoriz);

  let m11, m12, m21, m22;
  if (readAlongS) {
    m11 = J00 * invScale;
    m12 = J10 * invScale;
    m21 = J01 * invScale;
    m22 = J11 * invScale;
  } else {
    m11 = J01 * invScale;
    m12 = J11 * invScale;
    m21 = J00 * invScale;
    m22 = J10 * invScale;
  }

  if (m11 * m22 - m21 * m12 < 0) {
    m21 = -m21;
    m22 = -m22;
  }

  if (m11 < 0) {
    m11 = -m11; m12 = -m12;
    m21 = -m21; m22 = -m22;
  }

  const dx = ax - m11 * ax - m21 * ay;
  const dy = ay - m12 * ax - m22 * ay;

  const textColor = "#000000";

  const textItem = {
    name:      seg.name + "_text",
    label:     label,
    text:      label,
    x:         ax,
    y:         ay,
    font_px:   Math.max(6, fontNat),
    alignment: alignment,
    color:     textColor,
    transform: [m11, m12, m21, m22, dx, dy],
    inline_size: W_sq,
  };

  let rectItem = null;
  const filled = seg.filled !== false;
  if (drawRects && filled) {
    const hue = pairHue(p);
    rectItem = {
      name:           seg.name + "_rect",
      points:         [c0, c1, c2, c3],
      fill:           _hueToHex(hue),
      fill_opacity:   0.40,
      stroke:         _hueToHex(hue),
      stroke_width:   2.0,
      stroke_opacity: 1.0,
    };
  }

  return { text: textItem, rect: rectItem };
}

/* ==========================================================================
   PAYLOAD BUILDER
   ========================================================================== */

function buildKraPayload(options) {
  const texts = [];
  const rects = [];
  for (const p of pairs) {
    for (const seg of p.segments) {
      const item = _computeSegmentKraItem(p, seg, options);
      if (!item) continue;
      if (item.text) texts.push(item.text);
      if (item.rect) rects.push(item.rect);
    }
  }
  return { texts, rects };
}

/* ==========================================================================
   OPTION READ / WRITE
   ========================================================================== */

function readKraOptionsFromDOM() {
  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");

  const text_position = posEl ? posEl.value : "center";

  let text_padding = padEl ? parseFloat(padEl.value) : 0.06;
  if (!isFinite(text_padding) || text_padding < 0) text_padding = 0.06;
  if (text_padding > 0.5) text_padding = 0.5;

  const draw_rectangles = rectEl ? !!rectEl.checked : true;

  return {
    text_position:   text_position,
    text_padding:    text_padding,
    draw_rectangles: draw_rectangles,
  };
}

function applyKraOptionsToDOM(opts) {
  if (!opts || typeof opts !== "object") return;

  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");

  if (posEl && typeof opts.text_position === "string") {
    posEl.value = opts.text_position;
  }
  if (padEl && typeof opts.text_padding === "number") {
    padEl.value = String(opts.text_padding);
  }
  if (rectEl && typeof opts.draw_rectangles === "boolean") {
    rectEl.checked = opts.draw_rectangles;
  }
}

/* ==========================================================================
   TOP-LEVEL ENTRY
   ==========================================================================
   Two functions:

     generateKraFromScene()   prompts via window.prompt, then delegates
     generateKraToPath(path)  does the work; the path is an argument

   A remote client gathers the path on the other end of the
   WebSocket and calls the action with `output_path` in its payload;
   the registered action invokes generateKraToPath directly, no
   window.prompt involved. */

async function generateKraFromScene() {
  const defaultPath = "/tmp/pair_scene.kra";
  const outputPath = window.prompt("Output .kra path:", defaultPath);
  if (!outputPath) return;
  await generateKraToPath(outputPath);
}

async function generateKraToPath(outputPath) {
  if (!outputPath || typeof outputPath !== "string") return;

  let segCount = 0;
  for (const p of pairs) segCount += p.segments.length;

  if (segCount === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("No segments to export", "warn");
    }
    return;
  }

  const options  = readKraOptionsFromDOM();
  const band = {
    width:  Math.max(1, Math.round(window.innerWidth)),
    height: Math.max(1, Math.round(layout.topH)),
  };

  const result = buildKraPayload(options);
  if (result.texts.length === 0 && result.rects.length === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("Nothing exportable", "warn");
    }
    return;
  }

  const body = {
    doc_name:    "Pairs Scene",
    output_path: outputPath,
    options:     options,
    cone_band:   band,
    texts:       result.texts,
    rects:       result.rects,
  };

  if (typeof flashStatus === "function") {
    flashStatus("Generating .kra (" + result.texts.length
                + " text(s))...", "");
  }

  try {
    const response = await fetch("/generate-kra", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(response_body(body)),
    });
    const res = await response.json();
    if (res && res.success) {
      if (typeof flashStatus === "function") {
        flashStatus(res.message || "Done", "ok");
      }
    } else {
      const msg = (res && res.message) ? res.message : "unknown error";
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

function response_body(b) { return b; }
"""
