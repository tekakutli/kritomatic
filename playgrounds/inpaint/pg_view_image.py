"""The loaded image and the tinted overlay for each selected mask.

The single most stable view file — the image is always drawn first,
the overlays are always tinted from the same palette.  Every layer
here is drawn against the fitted image rect `r` (from
`pg_state.fittedImageRect`), wrapped in the same rotation transform
that `pg_view_inpaint.js` uses for the context rectangle, so the
image, mask overlays, and context rect stay visually aligned at
every angle.

Mask overlays iterate `board.selectedMaskIndices` directly rather
than allocating a `Set` and iterating the whole mask array.  Every
mutator of `selectedMaskIndices` (`toggleMaskSelection`,
`addMaskToSelection`, `replaceMaskSelection`, `clearMaskSelection`,
`selectAllMasks`, the delete handlers in `pg_panel_lists.js` and
`pg_panel_bindings.js`, `_kritaImportMasksAction`,
`_maskFromRectAction`, `_pickMaskAction`, `setMasks`) keeps the
array in ascending order, so direct iteration preserves the previous
draw order — mask index ascending — while avoiding a per-frame
allocation."""

VIEW_IMAGE_JS = r"""
import { ctx, board, maskColor, view } from "./pg_state.js";
import { maskPixelData } from "./pg_pixels.js";

/* ============================================================
   Tinted mask overlay
   ============================================================
   One tinted canvas per mask, cached on the mask entry.  The key
   includes the color so that when `maskColor(idx)` shifts (which it
   does when a mask's index changes because another mask was deleted),
   the tint is rebuilt. */

function _tintedMask(mask, color) {
  const key = color.r + "," + color.g + "," + color.b;
  if (mask._tinted && mask._tintedKey === key) return mask._tinted;
  const px = maskPixelData(mask);
  if (!px) return null;

  const W = mask.rotatedCanvas ? mask.rotatedW : mask.w;
  const H = mask.rotatedCanvas ? mask.rotatedH : mask.h;
  if (!W || !H) return null;

  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  const imgData = octx.createImageData(W, H);
  const out = imgData.data;

  for (let i = 0; i < px.length; i += 4) {
    const m = px[i] / 255;
    const a = px[i + 3] / 255;
    out[i]     = color.r;
    out[i + 1] = color.g;
    out[i + 2] = color.b;
    out[i + 3] = Math.round(255 * m * a);
  }
  octx.putImageData(imgData, 0, 0);

  mask._tinted = off;
  mask._tintedKey = key;
  return off;
}

/* ============================================================
   The image layer
   ============================================================
   The image, its border, and every selected-mask overlay are
   drawn against the *un-rotated* fit rect `r`, wrapped in a
   `translate(cx,cy); rotate(rad); translate(-cx,-cy)` transform.
   That gives the visual rotation without changing any image
   dimensions or the fit scale.

   The overlay loop iterates `board.selectedMaskIndices` directly.
   The array is kept in ascending order by every mutator, so
   iteration order matches the previous
   `for i in 0..n-1, skip unless selected` loop.  Skipping the
   `new Set(...)` allocation removes the only per-frame object
   allocation in this function. */

export function drawImageLayer(r) {
  const cx = r.x0 + r.w / 2;
  const cy = r.y0 + r.h / 2;
  const rad = view.rotation * Math.PI / 180;

  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(rad);
  ctx.translate(-cx, -cy);

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  for (const i of board.selectedMaskIndices) {
    const m = board.masks[i];
    if (!m || !m.loaded) continue;
    const tinted = _tintedMask(m, maskColor(i));
    if (!tinted) continue;
    const isHovered = (i === board.hoveredMaskIdx);
    ctx.save();
    ctx.globalAlpha = isHovered ? 0.55 : 0.40;
    ctx.drawImage(tinted, r.x0, r.y0, r.w, r.h);
    ctx.restore();
  }

  ctx.restore();
}
"""
