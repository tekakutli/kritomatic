"""The inpaint context rectangle.

Kept in its own file so subsequent inpaint work (an inpainted preview,
a run-inpaint spinner, an overlay showing the model's output directly
in the context rect, etc.) can be added here without touching any of
the other view files.

The rectangle's drag interaction (resize handles, border-band move,
Shift-symmetric resize) is dispatched in `pg_dispatch.js`, which calls
the pure helpers in `pg_inpaint.js`.  This module owns only the draw."""

VIEW_INPAINT_JS = r"""
import { ctx, board } from "./pg_state.js";
import {
  contextRectScreen, contextHandles, ensureDefaultContext,
} from "./pg_inpaint.js";

export function drawInpaintContext(r) {
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

  // Faint interior fill — signals the rect is live without
  // competing with the mask overlays.
  ctx.fillStyle = soft;
  ctx.fillRect(x0, y0, w, h);

  // Dashed dark halo, then dashed bright line — same visual
  // language as the lasso draft, different hue so the two are
  // never confused.
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

  // Corner + edge handles.  The handle that is currently being
  // dragged by the user is drawn in bright white so the active
  // affordance is unmistakable.
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

  // Label — always drawn inside the top-left of the rect so it
  // stays visible regardless of zoom or pan.
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = bright;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText("CONTEXT", x0 + 4, y0 + 4);

  ctx.restore();
}
"""
