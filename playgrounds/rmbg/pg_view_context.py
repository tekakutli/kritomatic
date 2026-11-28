"""The context rectangle overlay.

Drawn on the same single canvas as the image (above it).  No
rotation transform — the context rect is an axis-aligned UI
region in the display frame, and the display frame is the
boundary-cropped image.  It moves with the image only in the
sense that both live in the same coordinate space; there's no
rotation to keep visually fixed against."""

VIEW_CONTEXT_JS = r"""
import { ctx, board } from "./pg_state.js";
import {
  contextRectScreen, contextHandles, ensureDefaultContext,
} from "./pg_context.js";

export function drawContext(r) {
  if (!board.contextEnabled) return;
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
    const active = board.context.activeHandle;
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