"""
pg_panel_fields.py — the pair's panel-field semantics.

`_pairInclinationDeg(p)` is the display helper for the single
direction field.

`_syncPairFieldInputs()` pushes the selected pair's current values
into the six scrub fields — inclination, metric A, metric B,
offset, gap, loop back.  Called every frame from draw() and from
every place the selection changes.  A focused field is left alone.
"""

PANEL_FIELDS_JS = r"""
/* ==========================================================================
   INCLINATION — DISPLAY HELPER
   ========================================================================== */

function _pairInclinationDeg(p) {
  if (!p) return 0;
  let a = p.angle;
  while (a >  Math.PI / 2) a -= Math.PI;
  while (a < -Math.PI / 2) a += Math.PI;
  return a * 180 / Math.PI;
}

/* ==========================================================================
   FIELD SYNC
   ========================================================================== */

function _syncPairFieldInputs() {
  const inclEl  = document.getElementById("pairInclinationVal");
  const mAEl    = document.getElementById("pairMetricAVal");
  const mBEl    = document.getElementById("pairMetricBVal");
  const offEl   = document.getElementById("pairOffsetVal");
  const gapEl   = document.getElementById("pairGapVal");
  const loopEl  = document.getElementById("pairLoopbackVal");

  const p = (selectedPair >= 0 && selectedPair < pairs.length)
    ? pairs[selectedPair] : null;

  if (!p) {
    for (const el of [inclEl, mAEl, mBEl, offEl, gapEl, loopEl]) {
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
    if (document.activeElement !== mAEl) {
      mAEl.value = p.metricA.toFixed(3);
    }
  }
  if (mBEl) {
    mBEl.disabled = false;
    if (document.activeElement !== mBEl) {
      mBEl.value = p.metricB.toFixed(3);
    }
  }
  if (offEl) {
    offEl.disabled = false;
    if (document.activeElement !== offEl) {
      offEl.value = p.offset.toFixed(3);
    }
  }
  if (gapEl) {
    gapEl.disabled = false;
    if (document.activeElement !== gapEl) {
      gapEl.value = p.gap.toFixed(3);
    }
  }
  if (loopEl) {
    loopEl.disabled = false;
    if (document.activeElement !== loopEl) {
      const v = (typeof p.loopbackGap === "number")
                  ? p.loopbackGap : 0;
      loopEl.value = v.toFixed(3);
    }
  }
}
"""
