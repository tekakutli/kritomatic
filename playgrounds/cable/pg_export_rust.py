"""
pg_export_rust.py — the JS-side bridge to the Rust leader optimiser.

ES module.  One export: `optimizeLeadersOnServer(placed, opts)`, and
one assignment: `window.__optimizeLeadersOnServer`, so
`pg_export_labels.js` can call it as a dynamic global without a
static import (which would create a labels → rust → labels cycle).

WHAT IT DOES
============
Takes the JS-side `placed` array (the label pills, their anchor
positions, their channel assignments, and the mutable optimiser
fields) plus the obstacle context (boundaries, wire segments, wall
edges, the strip and top-pad geometry), POSTs them to
`/optimize-leaders`, and splices the optimised fields back into each
`placed` entry in place.

The three passes the Rust side runs, in order, are exactly the ones
the JS side used to run inline at the top of
`pg_export_labels.computeVertexLabelPlacement`:

    refineLeaderOffsets      the anchor-side fan-out
    polishLeaderLayout       the alternating optimise / hug / push loop
    hugOverhangingLeaders    the final overhang-clamping pass

SPLICE GUARDS
=============
Every field the Rust struct returns is validated before it is
written onto the JS-side `placed` entry:

    · scalar optimiser fields (channelYRel, offsetA, offsetP, bevel0,
      bevel1, jog0, jog1, diveMode, detourBias) are only assigned when
      the returned value is a finite number;
    · pillCenterX is only assigned when it is a finite number — a
      null/undefined/NaN here would blank every pill and every leader
      built off the pill's x;
    · finalPath is assigned only when it is an array (or explicitly
      null), never when it is a stray object or undefined.

This makes the JS side resilient to an older Rust build, a schema
drift, or a partially-serialised struct.

FALLBACK
========
If the fetch fails for any reason — server down, binary missing,
non-2xx response, timeout, unparseable body, or a validation failure
in the splice — the bridge catches and prints a warning, then runs
the JS-side versions of the three passes so the export still
completes.  The fallback preserves identical behaviour: on a server
that has the Rust binary, nothing changes; on one that does not, the
user pays the old performance cost and gets the same output.

WIRE FORMAT
===========
Request body: `{ "cables": [ OptimizeRequest, ... ] }` — one entry
per exported cable.  Each entry carries the `placed` array plus the
obstacle context.  Response: `{ "cables": [ OptimizeResponse, ... ] }`
with one `placed` array per input, in the same order.  Field names
are camelCase on both sides (the Rust structs carry
`#[serde(rename_all = "camelCase")]`).

DYNAMIC GLOBAL, NOT A STATIC IMPORT
===================================
`pg_export_labels.js` cannot statically import this module — that
would create a cycle (labels needs rust; rust needs the three
fallback passes that labels' siblings own — and today labels is what
calls the optimiser, so rust's fallback needs labels).  The
`window.__optimizeLeadersOnServer` global is the bridge: labels
reaches for it via `window.` at call time, not at import time.  Load
order does not matter as long as this module has evaluated by the
time labels fires its first placement.
"""

RUST_JS = r"""
import { refineLeaderOffsets } from "./pg_export_primitives.js";
import { polishLeaderLayout } from "./pg_export_pillpush.js";
import { hugOverhangingLeaders } from "./pg_export_labels.js";

/* ==========================================================================
   THE BRIDGE
   ========================================================================== */

export async function optimizeLeadersOnServer(placed, opts) {
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
      /* Mutable optimiser fields.  Sent as zeros because the Rust
         side resets and recomputes them all. */
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
    if (!data || !Array.isArray(data.cables) || !data.cables[0]) {
      throw new Error(
        "optimizer response missing cables[0] " +
        "(got keys: " + (data ? Object.keys(data).join(",") : "null") + ")"
      );
    }
    const out = data.cables[0].placed;
    if (!Array.isArray(out) || out.length !== placed.length) {
      throw new Error("optimizer returned " + (out ? out.length : "?") +
                      " placed items, expected " + placed.length);
    }

    /* ---- guarded splice ----
       Every scalar field is written only when the Rust side sent a
       finite number; `pillCenterX` in particular is the anchor every
       pill, leader, and info box is positioned from, so a stray
       `null`/`NaN` from a schema-drifted Rust struct must never be
       allowed through.  `finalPath` is likewise only accepted when it
       is an array or explicitly null. */
    for (let i = 0; i < placed.length; i++) {
      const o = out[i];
      const p = placed[i];

      if (typeof o.channelYRel === "number" && isFinite(o.channelYRel))
        p.channelYRel = o.channelYRel;
      if (typeof o.offsetA     === "number" && isFinite(o.offsetA))
        p.offsetA     = o.offsetA;
      if (typeof o.offsetP     === "number" && isFinite(o.offsetP))
        p.offsetP     = o.offsetP;
      if (typeof o.bevel0      === "number" && isFinite(o.bevel0))
        p.bevel0      = o.bevel0;
      if (typeof o.bevel1      === "number" && isFinite(o.bevel1))
        p.bevel1      = o.bevel1;
      if (typeof o.jog0        === "number" && isFinite(o.jog0))
        p.jog0        = o.jog0;
      if (typeof o.jog1        === "number" && isFinite(o.jog1))
        p.jog1        = o.jog1;
      if (typeof o.diveMode    === "number" && isFinite(o.diveMode))
        p.diveMode    = o.diveMode;
      if (typeof o.detourBias  === "number" && isFinite(o.detourBias))
        p.detourBias  = o.detourBias;

      if (typeof o.pillCenterX === "number" && isFinite(o.pillCenterX))
        p.pillCenterX = o.pillCenterX;

      if (Array.isArray(o.finalPath)) p.finalPath = o.finalPath;
      else if (o.finalPath === null)  p.finalPath = null;
      /* Anything else (undefined, string, object) leaves the existing
         finalPath untouched. */
    }
    return true;
  } catch (e) {
    console.warn("leader optimizer failed, falling back to JS:", e);
    /* Graceful degradation — run the JS-side passes in the original
       order.  The presence of all three functions is guaranteed
       because pg_export_primitives, pg_export_pillpush, and
       pg_export_labels are all imported at the top of this module. */
    try {
      refineLeaderOffsets(placed, opts.topPad, opts.trackOffsets,
                          opts.stripH);
      polishLeaderLayout(placed, opts.stripH, opts.topPad,
                         opts.trackOffsets,
                         opts.boundaries, opts.wireSegments,
                         opts.wallEdges);
      hugOverhangingLeaders(placed,
                            opts.stripAreaX0,
                            opts.stripAreaX0 + opts.stripAreaW,
                            opts.stripH, opts.topPad, opts.trackOffsets);
    } catch (e2) {
      console.error("JS fallback leader optimiser also failed:", e2);
    }
    return false;
  }
}

/* Bridge the exported name onto the global.  `pg_export_labels.js`
   looks up `window.__optimizeLeadersOnServer` at call time, so as
   long as this assignment runs before the first placement — which
   it does, because this module is imported by the boot module before
   any placement is triggered — the reference is available. */
window.__optimizeLeadersOnServer = optimizeLeadersOnServer;
"""
