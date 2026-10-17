"""
pg_panel_scrubs.py — pair scrub-input bindings and remote registration.

Every scrub input's `write` callback now ends with a call to
`pgkitNotifyLocal("...")`.  That is the *only* mechanism by which a
local scrub change reaches the wire — there is no polling anywhere
in the kit.

The API names use dotted paths that describe the model, not the DOM:

    pair.inclination   pair direction as inclination, degrees, [-90, +90]
    pair.metricA       world units per abstract unit along line A
    pair.metricB       world units per abstract unit along line B
    pair.offset        shift of line B along the direction of line A
    pair.gap           perpendicular distance between the two lines
    pair.scale         whole-pair zoom factor about the anchor
    pair.loopbackGap   extra abstract distance before the axis wraps

All operate on the currently-selected pair.  Setting a field with no
pair selected is a no-op (the `set` returns early).

The KRA text-padding field is not registered (it is read at export
time from the DOM, not scrubbed); the position select and rectangles
checkbox are registered in `pg_panel_bindings.py`.
"""

PANEL_SCRUBS_JS = r"""
/* ==========================================================================
   SCRUB-INPUT BINDINGS
   ========================================================================== */

(function installScrubs() {

  const inclEl  = document.getElementById("pairInclinationVal");
  const mAEl    = document.getElementById("pairMetricAVal");
  const mBEl    = document.getElementById("pairMetricBVal");
  const offEl   = document.getElementById("pairOffsetVal");
  const gapEl   = document.getElementById("pairGapVal");
  const scaleEl = document.getElementById("pairScaleVal");
  const loopEl  = document.getElementById("pairLoopbackVal");

  /* ---- Inclination° --------------------------------------------- */
  if (inclEl) _installScrubInput(inclEl, {
    rate: 0.4,
    snapStep: 15,
    read: () => {
      if (selectedPair < 0) return 0;
      return _pairInclinationDeg(pairs[selectedPair]);
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      let r = raw;
      if (r >  90) r =  90;
      if (r < -90) r = -90;
      pairs[selectedPair].angle = r * Math.PI / 180;
      pgkitNotifyLocal("pair.inclination");
    },
  });

  pgkitRegisterField("pair.inclination", {
    get: () => {
      if (selectedPair < 0) return 0;
      return _pairInclinationDeg(pairs[selectedPair]);
    },
    set: (v) => {
      if (selectedPair < 0) return;
      let r = parseFloat(v);
      if (!isFinite(r)) return;
      if (r >  90) r =  90;
      if (r < -90) r = -90;
      pairs[selectedPair].angle = r * Math.PI / 180;
    },
    meta: { kind: "number", label: "Inclination°", min: -90, max: 90 },
  });

  /* ---- Metric A ------------------------------------------------- */
  if (mAEl) _installScrubInput(mAEl, {
    rate: 0.005,
    snapStep: 0.1,
    read: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].metricA;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].metricA = Math.max(0.05, Math.min(100, raw));
      pgkitNotifyLocal("pair.metricA");
    },
  });

  pgkitRegisterField("pair.metricA", {
    get: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].metricA;
    },
    set: (v) => {
      if (selectedPair < 0) return;
      const r = parseFloat(v);
      if (!isFinite(r)) return;
      pairs[selectedPair].metricA = Math.max(0.05, Math.min(100, r));
    },
    meta: { kind: "number", label: "Metric A", min: 0.05, max: 100 },
  });

  /* ---- Metric B ------------------------------------------------- */
  if (mBEl) _installScrubInput(mBEl, {
    rate: 0.005,
    snapStep: 0.1,
    read: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].metricB;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].metricB = Math.max(0.05, Math.min(100, raw));
      pgkitNotifyLocal("pair.metricB");
    },
  });

  pgkitRegisterField("pair.metricB", {
    get: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].metricB;
    },
    set: (v) => {
      if (selectedPair < 0) return;
      const r = parseFloat(v);
      if (!isFinite(r)) return;
      pairs[selectedPair].metricB = Math.max(0.05, Math.min(100, r));
    },
    meta: { kind: "number", label: "Metric B", min: 0.05, max: 100 },
  });

  /* ---- Offset --------------------------------------------------- */
  if (offEl) _installScrubInput(offEl, {
    rate: 0.01,
    read: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].offset;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].offset = raw;
      pgkitNotifyLocal("pair.offset");
    },
  });

  pgkitRegisterField("pair.offset", {
    get: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].offset;
    },
    set: (v) => {
      if (selectedPair < 0) return;
      const r = parseFloat(v);
      if (!isFinite(r)) return;
      pairs[selectedPair].offset = r;
    },
    meta: { kind: "number", label: "Offset" },
  });

  /* ---- Gap ------------------------------------------------------ */
  if (gapEl) _installScrubInput(gapEl, {
    rate: 0.01,
    read: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].gap;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].gap = Math.max(0.01, raw);
      pgkitNotifyLocal("pair.gap");
    },
  });

  pgkitRegisterField("pair.gap", {
    get: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].gap;
    },
    set: (v) => {
      if (selectedPair < 0) return;
      const r = parseFloat(v);
      if (!isFinite(r)) return;
      pairs[selectedPair].gap = Math.max(0.01, r);
    },
    meta: { kind: "number", label: "Gap", min: 0.01 },
  });

  /* ---- Scale ---------------------------------------------------- */
  if (scaleEl) _installScrubInput(scaleEl, {
    rate: 0.005,
    snapStep: 0.1,
    read: () => {
      if (selectedPair < 0) return 1;
      const p = pairs[selectedPair];
      return (typeof p.scale === "number" && isFinite(p.scale)
                && p.scale > 0)
               ? p.scale : 1;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].scale = Math.max(0.05, Math.min(100, raw));
      pgkitNotifyLocal("pair.scale");
    },
  });

  pgkitRegisterField("pair.scale", {
    get: () => {
      if (selectedPair < 0) return 1;
      const p = pairs[selectedPair];
      return (typeof p.scale === "number" && isFinite(p.scale)
                && p.scale > 0)
               ? p.scale : 1;
    },
    set: (v) => {
      if (selectedPair < 0) return;
      const r = parseFloat(v);
      if (!isFinite(r)) return;
      pairs[selectedPair].scale = Math.max(0.05, Math.min(100, r));
    },
    meta: { kind: "number", label: "Scale", min: 0.05, max: 100 },
  });

  /* ---- Loop gap ------------------------------------------------- */
  if (loopEl) _installScrubInput(loopEl, {
    rate: 0.01,
    snapStep: 0.1,
    read: () => {
      if (selectedPair < 0) return 0;
      const p = pairs[selectedPair];
      return (typeof p.loopbackGap === "number") ? p.loopbackGap : 0;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].loopbackGap =
        Math.max(0, Math.min(1000, raw));
      pgkitNotifyLocal("pair.loopbackGap");
    },
  });

  pgkitRegisterField("pair.loopbackGap", {
    get: () => {
      if (selectedPair < 0) return 0;
      const p = pairs[selectedPair];
      return (typeof p.loopbackGap === "number") ? p.loopbackGap : 0;
    },
    set: (v) => {
      if (selectedPair < 0) return;
      const r = parseFloat(v);
      if (!isFinite(r)) return;
      pairs[selectedPair].loopbackGap = Math.max(0, Math.min(1000, r));
    },
    meta: { kind: "number", label: "Loop gap", min: 0, max: 1000 },
  });

})();
"""
