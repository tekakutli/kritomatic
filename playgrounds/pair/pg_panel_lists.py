"""
pg_panel_lists.py — the two selection lists.

ES module.  Exports:

    syncSegmentList   rebuild the segment list for the selected pair
    syncPairList      rebuild the pair list (calls syncSegmentList
                      and _syncPairFieldInputs first, so the field
                      values always match the newly-selected pair)

Both rebuild from scratch on every call.  Each rebuild ends with
`pgkitNotifyEntities()` — the two lists are the pair playground's
two entity levels — but does NOT call `draw()`.  Canvas repaint is
the caller's job: `notifyChanged()` (the injected afterChange hook)
fires `syncPairList() → draw() → pgkitNotifyAll()` in one pass, so
any model change that goes through `notifyChanged` gets the panel
and canvas update together.  Places that need only the panel
refreshed (a name edit that hasn't changed geometry, an incremental
`syncSegmentList`) call `syncSegmentList` / `syncPairList` directly.

DOM STATE
=========
`syncSegmentList` also reads `state.overlapHighlight`,
`state.shiftDragSet`, and the per-segment overlap memo.  Every row
therefore reflects three independent bits: the current selection,
the overlap highlight (if enabled), and whether the row belongs to
the shift-drag set.  Rows are rebuilt, so the color-band feedback
is a pure function of the current state.

`syncPairList` runs `_syncPairFieldInputs()` first so the seven
pair-scoped inputs reflect whichever pair is selected.

The overlap markers (`.overlapRow`, `.shiftMember`) and the
make-room (`⇣`) button are the only pair-specific CSS classes on
these rows; see `PAIR_CSS` in `pg_base.py`.
"""

PAIR_PANEL_LISTS_JS = r"""
import {
  pairs, pairState, state, pairHue, _huergb, _huergbLight,
  _segmentOverlapsOtherPair, _segmentOverlapsPairBelow,
  _makeRoomInferior, _isShiftSetMember,
  notifyChanged, deletePair, deleteSegment, movePair,
} from "./pg_core.js";
import { _syncPairFieldInputs } from "./pg_panel_fields.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   SEGMENT LIST
   ========================================================================== */

export function syncSegmentList() {
  const list = document.getElementById("segmentList");
  if (!list) return;
  list.innerHTML = "";

  if (pairState.selectedPair < 0 || pairState.selectedPair >= pairs.length) {
    const e = document.createElement("div");
    e.className = "empty"; e.textContent = "select a pair";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }

  const p = pairs[pairState.selectedPair];
  if (p.segments.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no segments on " + p.name + " yet";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }

  const hue = pairHue(p);
  const overlapOn = !!state.overlapHighlight;

  for (let j = 0; j < p.segments.length; j++) {
    const seg = p.segments[j];
    const sel = pairState.selectedSegment;
    const isSel = sel && sel.pairIdx === pairState.selectedPair && sel.segIdx === j;
    const overlap = overlapOn && _segmentOverlapsOtherPair(pairState.selectedPair, j);
    const inSet = _isShiftSetMember(pairState.selectedPair, j);
    const canMakeRoom = overlapOn &&
                        (pairState.selectedPair + 1 < pairs.length) &&
                        _segmentOverlapsPairBelow(pairState.selectedPair, j);

    const row = document.createElement("div");
    row.className = "quadRow squareRow" +
                    (isSel ? " selected" : "") +
                    (overlap ? " overlapRow" : "") +
                    (inSet ? " shiftMember" : "");
    row.dataset.idx = String(j);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    const filled = seg.filled !== false;
    if (filled) {
      sw.style.background = _huergb(hue, isSel ? 0.95 : 0.55);
      sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.85);
    } else {
      sw.style.background = "transparent";
      sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.70);
    }
    sw.style.border = "1px solid " + sw.style.borderColor;
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text"; lb.className = "quadLabelInput";
    lb.value = seg.name; lb.spellcheck = false;
    const startName = seg.name;
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => { seg.name = lb.value; });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") { seg.name = startName; lb.value = startName; lb.blur(); }
    });
    row.appendChild(lb);

    if (canMakeRoom) {
      const lowerName = pairs[pairState.selectedPair + 1].name;
      const rm = document.createElement("button");
      rm.className = "quadRoom"; rm.textContent = "\u21E3";
      rm.title = "Make room: shift the offending segment on " + lowerName;
      rm.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const moved = _makeRoomInferior(pairState.selectedPair, j);
        if (moved > 0) {
          notifyChanged();
          flashStatus("Made room on " + lowerName +
                      " (" + moved + " shift" + (moved === 1 ? "" : "s") + ")", "ok");
        } else {
          flashStatus("Could not resolve overlap", "warn");
        }
      });
      row.appendChild(rm);
    }

    const del = document.createElement("button");
    del.className = "quadDel"; del.textContent = "\u00D7";
    del.title = "Delete segment";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteSegment(pairState.selectedPair, j);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      pairState.selectedSegment = { pairIdx: pairState.selectedPair, segIdx: j };
      notifyChanged();
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}

/* ==========================================================================
   PAIR LIST
   ========================================================================== */

export function syncPairList() {
  syncSegmentList();
  _syncPairFieldInputs();

  const list = document.getElementById("pairList");
  if (!list) return;
  list.innerHTML = "";

  if (pairs.length === 0) {
    const e = document.createElement("div");
    e.className = "empty"; e.textContent = "no pairs yet";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }

  const overlapOn = !!state.overlapHighlight;

  for (let i = 0; i < pairs.length; i++) {
    const p = pairs[i];
    const hue = pairHue(p);
    const isSel = (i === pairState.selectedPair);
    let hasOverlap = false;
    if (overlapOn) {
      for (let j = 0; j < p.segments.length; j++) {
        if (_segmentOverlapsOtherPair(i, j)) { hasOverlap = true; break; }
      }
    }

    const row = document.createElement("div");
    row.className = "quadRow" +
                    (isSel ? " selected" : "") +
                    (hasOverlap ? " overlapRow" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    sw.style.background = _huergb(hue, isSel ? 0.95 : 0.55);
    sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.85);
    sw.style.border = "1px solid " + sw.style.borderColor;
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text"; lb.className = "quadLabelInput";
    lb.value = p.name; lb.spellcheck = false;
    const startName = p.name;
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => { p.name = lb.value; });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") { p.name = startName; lb.value = startName; lb.blur(); }
    });
    row.appendChild(lb);

    const up = document.createElement("button");
    up.className = "quadMv"; up.textContent = "\u2191";
    up.title = "Move pair up";
    up.disabled = (i === 0);
    up.addEventListener("click", (ev) => { ev.stopPropagation(); movePair(i, -1); });
    row.appendChild(up);

    const dn = document.createElement("button");
    dn.className = "quadMv"; dn.textContent = "\u2193";
    dn.title = "Move pair down";
    dn.disabled = (i === pairs.length - 1);
    dn.addEventListener("click", (ev) => { ev.stopPropagation(); movePair(i, +1); });
    row.appendChild(dn);

    const del = document.createElement("button");
    del.className = "quadDel"; del.textContent = "\u00D7";
    del.title = "Delete pair";
    del.addEventListener("click", (ev) => { ev.stopPropagation(); deletePair(i); });
    row.appendChild(del);

    row.addEventListener("click", () => {
      pairState.selectedPair = i;
      pairState.selectedSegment = null;
      notifyChanged();
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}
"""
