"""
pg_panel_fields.py — the pair's panel-field semantics.

ES module.  Exports two functions:

    _pairInclinationDeg(p)     display helper for `pair.inclination`
    _syncPairFieldInputs()     push selected pair's values into DOM

The inclination display folds the pair's `p.angle` (which is a
plain radians value on `(−π, +π]`) into `(−π/2, +π/2]` and converts
to degrees, so a right-to-left pair reports the same inclination as
the same pair read left-to-right.  The model keeps the raw angle
for physics; only the panel simplifies it.

`_syncPairFieldInputs` runs from `pairBootSync` and from
`syncPairList` on every model mutation.  It caches no state of its
own; each write is guarded by an `activeElement !== input` check,
which is the same guard the scrub-input installer uses.  Without
those guards, a value update that landed while the user was
mid-scrub or mid-type would steal the caret.

The seven pair-scoped fields it drives — Inclination, Metric A,
Metric B, Offset, Gap, Scale, Loop gap — are all registered as
`pair.*` API fields in `pg_panel_scrubs.py`.  This file only owns
the DOM side; the getters/setters live there.

PER-PAIR SCALE
==============
The scale field's value reads as 1 whenever the pair's own `scale`
is missing or not a positive finite number.  That is what every
pair starts with, and what every scene written before the field
existed loads as; the field is written back only on a scrub of the
Scale input or on a scene save.  See pg_core.py, PER-PAIR SCALE.
"""

PAIR_PANEL_FIELDS_JS = r"""
import { pairs, pairState } from "./pg_core.js";

export function _pairInclinationDeg(p) {
  if (!p) return 0;
  let a = p.angle;
  while (a >  Math.PI / 2) a -= Math.PI;
  while (a < -Math.PI / 2) a += Math.PI;
  return a * 180 / Math.PI;
}

export function _syncPairFieldInputs() {
  const inclEl  = document.getElementById("pairInclinationVal");
  const mAEl    = document.getElementById("pairMetricAVal");
  const mBEl    = document.getElementById("pairMetricBVal");
  const offEl   = document.getElementById("pairOffsetVal");
  const gapEl   = document.getElementById("pairGapVal");
  const scaleEl = document.getElementById("pairScaleVal");
  const loopEl  = document.getElementById("pairLoopbackVal");

  const sel = pairState.selectedPair;
  const p = (sel >= 0 && sel < pairs.length) ? pairs[sel] : null;

  if (!p) {
    for (const el of [inclEl, mAEl, mBEl, offEl, gapEl, scaleEl, loopEl]) {
      if (!el) continue;
      el.disabled = true;
      if (document.activeElement !== el) el.value = "\u2014";
    }
    return;
  }

  if (inclEl) {
    inclEl.disabled = false;
    if (document.activeElement !== inclEl) {
      inclEl.value = _pairInclinationDeg(p).toFixed(1);
    }
  }
  if (mAEl) {
    mAEl.disabled = false;
    if (document.activeElement !== mAEl) mAEl.value = p.metricA.toFixed(3);
  }
  if (mBEl) {
    mBEl.disabled = false;
    if (document.activeElement !== mBEl) mBEl.value = p.metricB.toFixed(3);
  }
  if (offEl) {
    offEl.disabled = false;
    if (document.activeElement !== offEl) offEl.value = p.offset.toFixed(3);
  }
  if (gapEl) {
    gapEl.disabled = false;
    if (document.activeElement !== gapEl) gapEl.value = p.gap.toFixed(3);
  }
  if (scaleEl) {
    scaleEl.disabled = false;
    if (document.activeElement !== scaleEl) {
      const v = (typeof p.scale === "number" && isFinite(p.scale) && p.scale > 0)
                ? p.scale : 1;
      scaleEl.value = v.toFixed(3);
    }
  }
  if (loopEl) {
    loopEl.disabled = false;
    if (document.activeElement !== loopEl) {
      const v = (typeof p.loopbackGap === "number") ? p.loopbackGap : 0;
      loopEl.value = v.toFixed(3);
    }
  }
}
"""
