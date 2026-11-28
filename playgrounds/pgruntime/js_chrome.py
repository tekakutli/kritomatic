CHROME_JS = r"""
/* ==========================================================================
   makeChromeText — cached, non-re-measured chrome text.

   Each call to the returned draw function:
     1. computes the current text via textFn()
     2. on text change, measures once, allocates an offscreen
        canvas, and renders there
     3. blits the (possibly cached) canvas at (x, y)

   On a stable string, the cost per frame is one string compare +
   one drawImage.

   Opts:
     x, y       — blit position (top-left of the text)
     font       — CSS font string; default is the project monospace
     color      — fillStyle; default is the project chrome grey
   ========================================================================== */

export function makeChromeText(targetCtx, textFn, opts = {}) {
  const {
    x = 14,
    y = 14,
    font = "700 10px 'JetBrains Mono', 'Fira Code', monospace",
    color = "#5a6774",
  } = opts;

  let cache = { key: null, canvas: null };

  return function drawChromeText() {
    const text = textFn();

    if (cache.key !== text) {
      targetCtx.save();
      targetCtx.font = font;
      const w = Math.ceil(targetCtx.measureText(text).width) + 4;
      targetCtx.restore();

      const c = document.createElement("canvas");
      c.width = Math.max(1, w);
      c.height = 20;
      const cx = c.getContext("2d");
      cx.font = font;
      cx.fillStyle = color;
      cx.textAlign = "left";
      cx.textBaseline = "top";
      cx.fillText(text, 0, 0);

      cache = { key: text, canvas: c };
    }

    targetCtx.drawImage(cache.canvas, x, y);
  };
}
"""