"""
pg_export_textures.py — the four print-friendly hatch patterns.

ES module.  Two textures the main renderer uses directly:

    void          backslash diagonal — the void band behind the wall
                  chunks in the strip, and the void swatch in the
                  legend
    step          forward diagonal, three-segment — the step face
                  hatch in the strip and the step-face swatch
    roomInterior  sparse forward diagonal — the faint fill inside the
                  room outline on the plan view

Every pattern is a small transparent canvas, cached on first use and
reused for the lifetime of the page.  The same pattern-cache idiom
`pg_state.py` uses for the on-screen screen-patterns.

Nothing here draws.  The exports are `_patternCanvas` (the raw canvas
factory) and `_pat` (the `createPattern` wrapper that takes the target
canvas context as its first argument).

No project imports — this is a leaf module.
"""

TEXTURES_JS = r"""
/* ==========================================================================
   PRINT-FRIENDLY TEXTURES
   ========================================================================== */

const _PATTERN_CANVASES = Object.create(null);

export function _patternCanvas(kind) {
  if (_PATTERN_CANVASES[kind]) return _PATTERN_CANVASES[kind];
  const make = (size, draw) => {
    const pc = document.createElement("canvas");
    pc.width = pc.height = size;
    const p = pc.getContext("2d");
    p.fillStyle = "#ffffff";
    p.fillRect(0, 0, size, size);
    p.strokeStyle = "#000000";
    p.fillStyle   = "#000000";
    p.lineWidth   = 0.7;
    p.lineCap     = "round";
    draw(p, size);
    return pc;
  };
  const defs = {
    step: [8, (p, s) => {
      p.lineWidth = 0.7;
      p.beginPath();
      p.moveTo(-1, s + 1);     p.lineTo(s + 1, -1);
      p.moveTo(-1, 1);         p.lineTo(1, -1);
      p.moveTo(s - 1, s + 1);  p.lineTo(s + 1, s - 1);
      p.stroke();
    }],
    void: [12, (p, s) => {
      p.lineWidth = 0.35;
      p.beginPath();
      p.moveTo(s + 1, -1);     p.lineTo(-1, s + 1);
      p.stroke();
    }],
    roomInterior: [22, (p, s) => {
      p.lineWidth = 0.4;
      p.beginPath();
      p.moveTo(-1, s + 1); p.lineTo(s + 1, -1);
      p.stroke();
    }],
  };
  const [size, draw] = defs[kind];
  _PATTERN_CANVASES[kind] = make(size, draw);
  return _PATTERN_CANVASES[kind];
}

export function _pat(ctx, kind) {
  return ctx.createPattern(_patternCanvas(kind), "repeat");
}
"""
