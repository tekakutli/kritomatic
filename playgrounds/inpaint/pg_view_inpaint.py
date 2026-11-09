"""The inpaint context rectangle, plus the rotation clip-zone overlay.

Two exports:

  drawInpaintContext(r)         the context rectangle (unchanged).
  drawRotationClipOverlay(r)    black vignette over the parts of the
                                *rotated image content* that fall
                                outside the current crop — i.e. where
                                mask pixels would be clipped by the
                                payload.

`drawRotationClipOverlay` is called only when the display rotation is
non-zero, since 0° has no clipping. The vignette shrinks when the mask
overlaps the clip zones and forces the crop to expand: fewer pixels
of the rotated image are outside the crop.

The tinted regions are the *tips* of the rotated W×H square (the
corners of the rotated image that stick out past the axis-aligned
crop). NOT the corners of the axis-aligned fit rect — those are
empty in the on-screen view, mask content can't reach them, and
painting them was a previous version's bug. The mask overlay is
drawn under the same `rotate(theta)` transform as the image, so mask
content occupies `rotate(fit_rect)` on screen and can only spill
into the tips.

The tint is a radial vignette: transparent at the crop boundary,
near-black at the rotated square's corners (the deepest lost point).
Black is chosen over a warning color because the tipped pixels are
literally what LoadImage reads as black on the wire — the vignette
is an honest depiction of what the model receives, not an alarm.

The rectangle's drag interaction (resize handles, border-band move,
Shift-symmetric resize) is dispatched in `pg_dispatch.js`, which calls
the pure helpers in `pg_inpaint.js`. This module owns only the draw.

The rectangle is drawn only when `board.inpaintEnabled` is true. When
false, the panel is in full-canvas mode and there is nothing on the
canvas to draw for this layer.

The rectangle is deliberately NOT drawn under the rotation transform
that `pg_view_image.js` applies to the image: it stays visually fixed
on screen while the image rotates underneath it. `board.inpaintContext`
lives in un-rotated image coords; `contextRectScreen(r)` projects that
to a fixed screen position; the draw uses those screen coordinates
directly."""

VIEW_INPAINT_JS = r"""
import { ctx, board, view } from "./pg_state.js";
import {
  contextRectScreen, contextHandles, ensureDefaultContext,
  projectCropDims,
} from "./pg_inpaint.js";
import { computeMaskBBox } from "./pg_pixels.js";

/* ============================================================
   Rotation clip-zone overlay
   ============================================================
   The payload's sent region is a (cropW × cropH) axis-aligned crop
   of the rotated W'×H' frame. Any part of the *rotated image content*
   (the rotated W×H square) that falls outside that crop does not
   make it into the payload — mask pixels there are lost.

   On screen, the rotated image content occupies `rotate(theta)` of
   the fit rect. The crop (in W'×H' coords) is an axis-aligned
   (cropW × cropH) rect. The clip zones are the parts of the rotated
   square that poke out past that axis-aligned crop — the tips.

   The overlay is a radial vignette clipped to the rotated W×H
   square: transparent at r0 (roughly the crop's inscribed radius),
   near-black at r1 (the rotated square's corner distance). The
   crop rect is then erased at full opacity, so nothing inside the
   crop stays tinted. What survives is a smooth black-to-transparent
   falloff exactly in the tips, darkest at the deepest lost points.

   The overlay is cached by (W, H, cropW, cropH, rotation) so pan
   and zoom, which happen every frame and reuse the same tint, pay
   only a single `drawImage`. */

let _clipOverlayCache = null;
let _clipOverlayKey = "";

function _currentCropDims(W, H, theta) {
  // Only the single-selection path produces a mask to intersect with
  // — mirrors `_buildInpaintPayload`'s precondition. Multi-selection
  // or none → base crop.
  if (board.selectedMaskIndices.length !== 1) {
    return { cropW: W, cropH: H };
  }
  const idx = board.selectedMaskIndices[0];
  const bbox = computeMaskBBox(idx);  // cached on the mask entry
  return projectCropDims(
    W, H, bbox, theta, board.inpaintContext, board.inpaintEnabled,
  );
}

function _buildClipOverlay(Wp, Hp, W, H, theta, cropW, cropH) {
  const rad = theta * Math.PI / 180;

  const off = document.createElement("canvas");
  off.width = Wp;
  off.height = Hp;
  const octx = off.getContext("2d");

  // 1. Draw the vignette, clipped to the rotated W×H square.
  //
  //    r0: transparent inner radius. Anchored to the crop's min
  //        half-dimension so it grows as the crop expands (which
  //        keeps the tint shrinking smoothly as the user's mask
  //        forces more of the frame to be included).
  //    r1: opaque outer radius. The rotated square's corner
  //        distance — the deepest points of any tip.
  //
  //    Anything between r0 and r1 gets a linear black gradient. On
  //    screen this reads as "content dissolving into darkness".
  octx.save();
  octx.translate(Wp / 2, Hp / 2);
  octx.rotate(rad);
  octx.beginPath();
  octx.rect(-W / 2, -H / 2, W, H);
  octx.clip();

  const r0 = Math.min(cropW, cropH) / 2 * 0.7;
  const r1 = Math.sqrt(W * W + H * H) / 2;
  const grad = octx.createRadialGradient(0, 0, r0, 0, 0, r1);
  grad.addColorStop(0.0, "rgba(0, 0, 0, 0.0)");
  grad.addColorStop(1.0, "rgba(0, 0, 0, 0.85)");
  octx.fillStyle = grad;
  octx.fillRect(-W / 2, -H / 2, W, H);
  octx.restore();

  // 2. Erase the current crop region at full opacity. Nothing inside
  //    the crop survives into the payload as lost content, so nothing
  //    inside should show tint. `destination-out` uses the source
  //    ALPHA to remove destination pixels: D' = D × (1 - Sa). Full
  //    opacity (Sa = 1) is a true erase — using the tint's own alpha
  //    here was the "nothing painted" bug earlier.
  octx.globalCompositeOperation = "destination-out";
  octx.fillStyle = "#fff";
  octx.fillRect(
    Wp / 2 - cropW / 2, Hp / 2 - cropH / 2,
    cropW, cropH,
  );
  octx.globalCompositeOperation = "source-over";

  return off;
}

export function drawRotationClipOverlay(r) {
  if (!board.imageEl) return;
  if (!r || r.w <= 0 || r.h <= 0) return;
  const theta = view.rotation;
  if (!theta || theta % 360 === 0) return;

  const imgW = board.imageWidth;
  const imgH = board.imageHeight;

  // The rotated frame's bbox in original-image units.
  const rad = (theta % 360) * Math.PI / 180;
  const cosA = Math.abs(Math.cos(rad));
  const sinA = Math.abs(Math.sin(rad));
  const Wp = Math.max(1, Math.round(imgW * cosA + imgH * sinA));
  const Hp = Math.max(1, Math.round(imgW * sinA + imgH * cosA));

  // Crop dims in original-image units — `projectCropDims` works in
  // image coords throughout, and the tint canvas is in image coords
  // too (W'×H'), so no scaling is needed for the cache. Only the
  // on-screen draw below scales.
  const proj = _currentCropDims(imgW, imgH, theta);

  const roundedTheta = Math.round(theta * 10) / 10;
  const key = imgW + "x" + imgH
            + "|" + Math.round(proj.cropW * 10) + "x" + Math.round(proj.cropH * 10)
            + "@" + roundedTheta;

  if (key !== _clipOverlayKey || !_clipOverlayCache) {
    _clipOverlayCache = _buildClipOverlay(
      Wp, Hp, imgW, imgH, roundedTheta, proj.cropW, proj.cropH,
    );
    _clipOverlayKey = key;
  }

  // Draw the W'×H' tint canvas at the W'×H' bbox position on screen:
  // centered on the fit rect's center, scaled by the fit scale. This
  // is what lets the tint reach the tips of the rotated image, which
  // extend past the fit rect's edges.
  const s = r.w / imgW;
  const bigW = Wp * s;
  const bigH = Hp * s;
  const bigX = r.x0 + r.w / 2 - bigW / 2;
  const bigY = r.y0 + r.h / 2 - bigH / 2;
  ctx.drawImage(_clipOverlayCache, bigX, bigY, bigW, bigH);
}

/* ============================================================
   The inpaint context rectangle (unchanged) */

export function drawInpaintContext(r) {
  if (!board.inpaintEnabled) return;
  if (!board.imageEl) return;

  ensureDefaultContext();

  const rect = contextRectScreen(r);
  if (!rect) return;
  const { x0, y0, x1, y1 } = rect;
  const w = x1 - x0, h = y1 - y0;

  const bright = "rgba(180, 155, 255, 1.0)";
  const soft   = "rgba(180, 155, 255, 0.08)";
  const halo   = "rgba(8, 10, 14, 0.82)";

  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap  = "round";

  ctx.fillStyle = soft;
  ctx.fillRect(x0, y0, w, h);

  ctx.beginPath();
  ctx.rect(x0, y0, w, h);
  ctx.strokeStyle = halo;
  ctx.lineWidth   = 6.0;
  ctx.setLineDash([7, 5]);
  ctx.stroke();

  ctx.beginPath();
  ctx.rect(x0, y0, w, h);
  ctx.strokeStyle = bright;
  ctx.lineWidth   = 2.8;
  ctx.setLineDash([7, 5]);
  ctx.stroke();
  ctx.setLineDash([]);

  const handles = contextHandles(r);
  if (handles) {
    const HS = 7;
    const active = board.inpaintContext.activeHandle;
    for (const hd of handles) {
      ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
      ctx.fillRect(hd.x - HS - 2, hd.y - HS - 2, (HS + 2) * 2, (HS + 2) * 2);
      ctx.fillStyle = (active === hd.name)
        ? "rgba(255, 255, 255, 1.0)"
        : bright;
      ctx.fillRect(hd.x - HS, hd.y - HS, HS * 2, HS * 2);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(hd.x - HS, hd.y - HS, HS * 2, HS * 2);
    }
  }

  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = bright;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText("CONTEXT", x0 + 4, y0 + 4);

  ctx.restore();
}
"""