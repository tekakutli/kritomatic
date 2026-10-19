PAIR_PANEL_SCRUBS_JS = r"""
import { pairs, pairState } from "./pg_core.js";
import { _pairInclinationDeg } from "./pg_panel_fields.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

(function installScrubs() {
  const inclEl  = document.getElementById("pairInclinationVal");
  const mAEl    = document.getElementById("pairMetricAVal");
  const mBEl    = document.getElementById("pairMetricBVal");
  const offEl   = document.getElementById("pairOffsetVal");
  const gapEl   = document.getElementById("pairGapVal");
  const scaleEl = document.getElementById("pairScaleVal");
  const loopEl  = document.getElementById("pairLoopbackVal");

  if (inclEl) _installScrubInput(inclEl, {
    rate: 0.4, snapStep: 15,
    read: () => {
      const i = pairState.selectedPair;
      if (i < 0) return 0;
      return _pairInclinationDeg(pairs[i]);
    },
    write: (raw) => {
      const i = pairState.selectedPair;
      if (i < 0) return;
      let r = raw;
      if (r >  90) r =  90;
      if (r < -90) r = -90;
      pairs[i].angle = r * Math.PI / 180;
      pgkitNotifyLocal("pair.inclination");
    },
  });

  if (mAEl) _installScrubInput(mAEl, {
    rate: 0.005, snapStep: 0.1,
    read: () => {
      const i = pairState.selectedPair;
      return i < 0 ? 0 : pairs[i].metricA;
    },
    write: (raw) => {
      const i = pairState.selectedPair;
      if (i < 0) return;
      pairs[i].metricA = Math.max(0.05, Math.min(100, raw));
      pgkitNotifyLocal("pair.metricA");
    },
  });

  if (mBEl) _installScrubInput(mBEl, {
    rate: 0.005, snapStep: 0.1,
    read: () => {
      const i = pairState.selectedPair;
      return i < 0 ? 0 : pairs[i].metricB;
    },
    write: (raw) => {
      const i = pairState.selectedPair;
      if (i < 0) return;
      pairs[i].metricB = Math.max(0.05, Math.min(100, raw));
      pgkitNotifyLocal("pair.metricB");
    },
  });

  if (offEl) _installScrubInput(offEl, {
    rate: 0.01,
    read: () => {
      const i = pairState.selectedPair;
      return i < 0 ? 0 : pairs[i].offset;
    },
    write: (raw) => {
      const i = pairState.selectedPair;
      if (i < 0) return;
      pairs[i].offset = raw;
      pgkitNotifyLocal("pair.offset");
    },
  });

  if (gapEl) _installScrubInput(gapEl, {
    rate: 0.01,
    read: () => {
      const i = pairState.selectedPair;
      return i < 0 ? 0 : pairs[i].gap;
    },
    write: (raw) => {
      const i = pairState.selectedPair;
      if (i < 0) return;
      pairs[i].gap = Math.max(0.01, raw);
      pgkitNotifyLocal("pair.gap");
    },
  });

  if (scaleEl) _installScrubInput(scaleEl, {
    rate: 0.005, snapStep: 0.1,
    read: () => {
      const i = pairState.selectedPair;
      if (i < 0) return 1;
      const p = pairs[i];
      return (typeof p.scale === "number" && isFinite(p.scale) && p.scale > 0)
             ? p.scale : 1;
    },
    write: (raw) => {
      const i = pairState.selectedPair;
      if (i < 0) return;
      pairs[i].scale = Math.max(0.05, Math.min(100, raw));
      pgkitNotifyLocal("pair.scale");
    },
  });

  if (loopEl) _installScrubInput(loopEl, {
    rate: 0.01, snapStep: 0.1,
    read: () => {
      const i = pairState.selectedPair;
      if (i < 0) return 0;
      const p = pairs[i];
      return (typeof p.loopbackGap === "number") ? p.loopbackGap : 0;
    },
    write: (raw) => {
      const i = pairState.selectedPair;
      if (i < 0) return;
      pairs[i].loopbackGap = Math.max(0, Math.min(1000, raw));
      pgkitNotifyLocal("pair.loopbackGap");
    },
  });
})();

/* ==========================================================================
   FIELD REGISTRATIONS
   ========================================================================== */

pgkitRegisterField("pair.inclination", {
  get: () => {
    const i = pairState.selectedPair;
    return i < 0 ? 0 : _pairInclinationDeg(pairs[i]);
  },
  set: (v) => {
    const i = pairState.selectedPair;
    if (i < 0) return;
    let r = parseFloat(v);
    if (!isFinite(r)) return;
    if (r >  90) r =  90;
    if (r < -90) r = -90;
    pairs[i].angle = r * Math.PI / 180;
  },
  meta: { kind: "number", label: "Inclination°", min: -90, max: 90 },
});

pgkitRegisterField("pair.metricA", {
  get: () => {
    const i = pairState.selectedPair;
    return i < 0 ? 0 : pairs[i].metricA;
  },
  set: (v) => {
    const i = pairState.selectedPair;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    pairs[i].metricA = Math.max(0.05, Math.min(100, r));
  },
  meta: { kind: "number", label: "Metric A", min: 0.05, max: 100 },
});

pgkitRegisterField("pair.metricB", {
  get: () => {
    const i = pairState.selectedPair;
    return i < 0 ? 0 : pairs[i].metricB;
  },
  set: (v) => {
    const i = pairState.selectedPair;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    pairs[i].metricB = Math.max(0.05, Math.min(100, r));
  },
  meta: { kind: "number", label: "Metric B", min: 0.05, max: 100 },
});

pgkitRegisterField("pair.offset", {
  get: () => {
    const i = pairState.selectedPair;
    return i < 0 ? 0 : pairs[i].offset;
  },
  set: (v) => {
    const i = pairState.selectedPair;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    pairs[i].offset = r;
  },
  meta: { kind: "number", label: "Offset" },
});

pgkitRegisterField("pair.gap", {
  get: () => {
    const i = pairState.selectedPair;
    return i < 0 ? 0 : pairs[i].gap;
  },
  set: (v) => {
    const i = pairState.selectedPair;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    pairs[i].gap = Math.max(0.01, r);
  },
  meta: { kind: "number", label: "Gap", min: 0.01 },
});

pgkitRegisterField("pair.scale", {
  get: () => {
    const i = pairState.selectedPair;
    if (i < 0) return 1;
    const p = pairs[i];
    return (typeof p.scale === "number" && isFinite(p.scale) && p.scale > 0)
           ? p.scale : 1;
  },
  set: (v) => {
    const i = pairState.selectedPair;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    pairs[i].scale = Math.max(0.05, Math.min(100, r));
  },
  meta: { kind: "number", label: "Scale", min: 0.05, max: 100 },
});

pgkitRegisterField("pair.loopbackGap", {
  get: () => {
    const i = pairState.selectedPair;
    if (i < 0) return 0;
    const p = pairs[i];
    return (typeof p.loopbackGap === "number") ? p.loopbackGap : 0;
  },
  set: (v) => {
    const i = pairState.selectedPair;
    if (i < 0) return;
    const r = parseFloat(v);
    if (!isFinite(r)) return;
    pairs[i].loopbackGap = Math.max(0, Math.min(1000, r));
  },
  meta: { kind: "number", label: "Loop gap", min: 0, max: 1000 },
});
"""
