PANEL_FIELDS_JS = r"""
/* ==========================================================================
   syncImageInfo — update an element's text with the image's dims + hash.

   Opts:
     hint            — string shown when no image is loaded
                       (default: "No image loaded")
     hashPrefixLen   — how many hex chars of imageHash to show
                       (default: 8)
     dimsSeparator   — string between W and H (default: " × ")
     hashLabelFormat — function(hash) → string, default
                       "[<prefix>…]"

   Equality-guarded: a steady-state frame touches no DOM.
   ========================================================================== */

export function syncImageInfo(el, board, opts = {}) {
  if (!el) return;
  const {
    hint = "No image loaded",
    hashPrefixLen = 8,
    dimsSeparator = " \u00D7 ",
    hashLabelFormat = null,
  } = opts;

  let text;
  if (!board.imageEl) {
    text = hint;
  } else {
    const prefix = String(board.imageHash || "").slice(0, hashPrefixLen);
    const hashLabel = hashLabelFormat
      ? hashLabelFormat(prefix)
      : ("[" + prefix + "\u2026]");
    text = board.imageWidth + dimsSeparator + board.imageHeight
         + "   " + hashLabel;
  }
  if (el.textContent !== text) el.textContent = text;
}
"""