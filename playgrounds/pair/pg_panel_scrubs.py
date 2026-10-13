"""
pg_panel_scrubs.py — every scrub-input binding.

One IIFE, and inside it one _installScrubInput(...) call per scrub
field.  Each call supplies rate, snapStep, read() and write().

FIELDS INSTALLED HERE
=====================
    pairInclinationVal  pair direction as inclination against
                        horizontal, [-90, +90] degrees
    pairMetricAVal      world units per abstract unit along line A
    pairMetricBVal      world units per abstract unit along line B
    pairOffsetVal       shift of line B along the direction of line A
    pairGapVal          perpendicular distance between the two lines
    pairScaleVal        whole-pair zoom factor about the anchor
                        point (cx, cy); 1 = identity
    pairLoopbackVal     extra abstract distance appended to the
                        pair's axis period (>= 0)

The KRA text-padding field (kraTextPad) is not a scrub field: it is
read as a plain text input at KRA-export time.

SCALE VERSUS METRIC A / B
=========================
Metric A and metric B change ONE line's stretch independently of
the other.  Scale changes BOTH lines AND the offset AND the gap by
the same factor, so the pair behaves like a single object drawn at
a different size.  The two are meant to be used together: the user
sets the pair's shape with the metrics and offset, then adjusts its
overall size with the scale.  See pg_core.py, PER-PAIR SCALE.
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

  /* ---- Inclination° — the pair's tilt against the horizontal
     axis, in [-90, +90] degrees. */
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
    },
  });

  /* ---- Metric A. */
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
    },
  });

  /* ---- Metric B. */
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
    },
  });

  /* ---- Offset.  Shift of line B along the direction of line A. */
  if (offEl) _installScrubInput(offEl, {
    rate: 0.01,
    read: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].offset;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].offset = raw;
    },
  });

  /* ---- Gap.  Perpendicular distance between the two lines. */
  if (gapEl) _installScrubInput(gapEl, {
    rate: 0.01,
    read: () => {
      if (selectedPair < 0) return 0;
      return pairs[selectedPair].gap;
    },
    write: (raw) => {
      if (selectedPair < 0) return;
      pairs[selectedPair].gap = Math.max(0.01, raw);
    },
  });

  /* ---- Scale.  Whole-pair zoom about the anchor point.  Folds
     into both metrics, the offset, and the gap (see pg_core.py,
     PER-PAIR SCALE), so a scale of 2 makes the pair look twice
     as big on the canvas, anchored at (cx, cy); a scale of 0.5
     halves it.  Snap-snaps to 0.1 on Shift, like the metrics. */
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
    },
  });

  /* ---- Loop gap.  Extra abstract distance appended to the pair's
     axis period.  Clamped to [0, 1000] on write; the value is in
     abstract units, same as the other pair fields.  Shift-snap to
     0.1 for consistency with metric A / B. */
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
    },
  });

})();
"""
