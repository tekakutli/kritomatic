"""
pg_export_rust.py — the JS-side bridge to the Rust leader optimiser.

One function:

    window.__optimizeLeadersOnServer(placed, opts) -> Promise<bool>

It takes the JS-side `placed` array (the label pills, their anchor
positions, their channel assignments, and the mutable optimiser
fields) plus the obstacle context (boundaries, wire segments,
wall edges, the strip and top-pad geometry), POSTs them to
/optimize-leaders, and splices the optimised fields back into each
`placed` entry in place.

The three passes the Rust side runs, in order, are exactly the ones
the JS side used to run inline at the top of
pg_export_labels.computeVertexLabelPlacement:

    refineLeaderOffsets      the anchor-side fan-out
    polishLeaderLayout       the alternating optimise / hug / push loop
    hugOverhangingLeaders    the final overhang-clamping pass

Fallback
--------
If the fetch fails for any reason — server down, binary missing,
non-2xx response, timeout — the bridge catches and prints a warning,
then runs the JS-side versions of the three passes so the export
still completes.  The fallback preserves identical behaviour: on a
server that has the Rust binary, nothing changes; on one that does
not, the user pays the old performance cost and gets the same
output.

Wire format
-----------
Request body: { "cables": [ OptimizeRequest, ... ] } — one entry
per exported cable.  Each entry carries the `placed` array plus the
obstacle context.  Response: { "cables": [ OptimizeResponse, ... ] }
with one `placed` array per input, in the same order.  Field names
are camelCase on the JS side and camelCase on the Rust side (the
Rust structs carry #[serde(rename_all = "camelCase")]).
"""


RUST_JS = r"""
/* ==========================================================================
   RUST LEADER-OPTIMIZER BRIDGE
   ==========================================================================
   See the module docstring.  The signature is deliberately narrow:
   one `placed` array (mutated in place), one `opts` object carrying
   the geometry.  Everything else — the fetch, the JSON shape, the
   splice-back loop — is an implementation detail of this function. */

window.__optimizeLeadersOnServer = async function (placed, opts) {
  const payload = {
    placed: placed.map(it => ({
      /* Read-only geometry the optimiser needs. */
      anchorCx:     it.anchorCx,
      anchorCyRel:  it.anchorCyRel,
      w:            it.w,
      h:            it.h,
      track:        it.track,
      pillCenterX:  it.pillCenterX,
      channelYRel:  it.channelYRel,
      /* These are the mutable optimiser fields.  They go out as
         zero because the Rust side resets and recomputes them all;
         sending the JS-side current values would be wasted bytes. */
      offsetA:      0,
      offsetP:      0,
      bevel0:       0,
      bevel1:       0,
      jog0:         0,
      jog1:         0,
      diveMode:     0,
      detourBias:   0,
    })),
    stripH:       opts.stripH,
    topPad:       opts.topPad,
    trackOffsets: opts.trackOffsets,
    boundaries:   opts.boundaries,
    wireSegments: opts.wireSegments.map(w => ({
      ax: w.ax, ay: w.ay, bx: w.bx, by: w.by,
    })),
    wallEdges:    opts.wallEdges.map(w => ({
      x0: w.x0, x1: w.x1, y: w.y,
    })),
    stripAreaX0:  opts.stripAreaX0,
    stripAreaW:   opts.stripAreaW,
  };

  try {
    const r = await fetch("/optimize-leaders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cables: [{ cableId: 0, ...payload }] }),
    });
    if (!r.ok) {
      const txt = await r.text();
      throw new Error("HTTP " + r.status + ": " + txt.slice(0, 200));
    }
    const data = await r.json();
    const out = data.cables[0].placed;
    if (!Array.isArray(out) || out.length !== placed.length) {
      throw new Error("optimizer returned " + (out ? out.length : "?") +
                      " placed items, expected " + placed.length);
    }
    for (let i = 0; i < placed.length; i++) {
      const o = out[i];
      placed[i].channelYRel = o.channelYRel;
      placed[i].offsetA     = o.offsetA;
      placed[i].offsetP     = o.offsetP;
      placed[i].bevel0      = o.bevel0;
      placed[i].bevel1      = o.bevel1;
      placed[i].jog0        = o.jog0;
      placed[i].jog1        = o.jog1;
      placed[i].diveMode    = o.diveMode;
      placed[i].detourBias  = o.detourBias;
      placed[i].pillCenterX = o.pillCenterX;
      /* finalPath serializes from Rust as [[x, y], ...] or null —
         the same array-of-pairs shape drawVertexLabels already
         consumes, so no conversion is needed here. */
      placed[i].finalPath   = o.finalPath || null;
    }
    return true;
  } catch (e) {
    console.warn("leader optimizer failed, falling back to JS:", e);
    /* Graceful degradation — run the JS-side passes in the original
       order.  The presence of all three functions is guaranteed
       because pg_export_primitives, pg_export_pillpush, and this
       same module's siblings are all concatenated into the same
       script scope before this bridge ever runs. */
    try {
      if (typeof refineLeaderOffsets === "function") {
        refineLeaderOffsets(placed, opts.topPad, opts.trackOffsets,
                            opts.stripH);
      }
      if (typeof polishLeaderLayout === "function") {
        polishLeaderLayout(placed, opts.stripH, opts.topPad,
                           opts.trackOffsets,
                           opts.boundaries, opts.wireSegments,
                           opts.wallEdges);
      }
      if (typeof hugOverhangingLeaders === "function") {
        hugOverhangingLeaders(placed,
                              opts.stripAreaX0,
                              opts.stripAreaX0 + opts.stripAreaW,
                              opts.stripH, opts.topPad, opts.trackOffsets);
      }
    } catch (e2) {
      console.error("JS fallback leader optimiser also failed:", e2);
    }
    return false;
  }
};
"""
