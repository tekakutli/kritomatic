"""The loaded image and the tinted overlay for each selected mask.

The single most stable view file — the image is always drawn first,
the overlays are always tinted from the same palette.  Every layer
here is drawn against the fitted image rect `r` (from
`pg_state.fittedImageRect`).

Mask overlays iterate `board.selectedMaskIndices` directly rather
than allocating a `Set` and iterating the whole mask array.  Every
mutator of `selectedMaskIndices` (`toggleMaskSelection`,
`addMaskToSelection`, `replaceMaskSelection`, `clearMaskSelection`,
`selectAllMasks`, `restoreMasks`, `setMasks`, and the delete
handlers in `pg_panel_lists.js` / `pg_panel_bindings.js`) keeps the
array in ascending order, so direct iteration preserves the
previous draw order — mask index ascending — while avoiding a
per-frame allocation."""

VIEW_IMAGE_JS = r"""
import { ctx, board, maskColor } from "./pg_state.js";
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

  const W = mask.img.width, H = mask.img.height;
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
   The overlay loop iterates `board.selectedMaskIndices` directly.
   The array is kept in ascending order by every mutator, so
   iteration order matches the previous
   `for i in 0..n-1, skip unless selected` loop.  Skipping the
   `new Set(...)` allocation removes the only per-frame object
   allocation in this function. */

export function drawImageLayer(r) {
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
}
"""
