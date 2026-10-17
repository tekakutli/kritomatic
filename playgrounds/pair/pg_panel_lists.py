"""
pg_panel_lists.py — the two panel lists.

Rebuilt from scratch on every sync.  Each rebuild also triggers an
entities refresh (`pgkitNotifyEntities()`), because the list reflects
which items exist and which is selected — both of which the remote
panel's entities view depends on.
"""

PANEL_LISTS_JS = r"""
/* ==========================================================================
   SEGMENT LIST
   ========================================================================== */

function syncSegmentList() {
  const list = document.getElementById("segmentList");
  if (!list) return;
  list.innerHTML = "";

  if (selectedPair < 0 || selectedPair >= pairs.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "select a pair";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }

  const p = pairs[selectedPair];
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
    const isSel = selectedSegment &&
                  selectedSegment.pairIdx === selectedPair &&
                  selectedSegment.segIdx  === j;
    const overlap = overlapOn &&
                    _segmentOverlapsOtherPair(selectedPair, j);
    const inSet = _isShiftSetMember(selectedPair, j);

    const canMakeRoom = overlapOn &&
                        (selectedPair + 1 < pairs.length) &&
                        _segmentOverlapsPairBelow(selectedPair, j);

    const row = document.createElement("div");
    row.className = "quadRow squareRow"
                  + (isSel ? " selected" : "")
                  + (overlap ? " overlapRow" : "")
                  + (inSet ? " shiftMember" : "");
    row.dataset.idx = String(j);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    const filled = seg.filled !== false;
    if (filled) {
      sw.style.background  = _huergb(hue, isSel ? 0.95 : 0.55);
      sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.85);
    } else {
      sw.style.background  = "transparent";
      sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.70);
    }
    sw.style.border = "1px solid " + sw.style.borderColor;
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text";
    lb.className = "quadLabelInput";
    lb.value = seg.name;
    lb.spellcheck = false;
    const startName = seg.name;
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => {
      seg.name = lb.value;
      draw();
    });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") {
        seg.name = startName;
        lb.value = startName;
        lb.blur();
        draw();
      }
    });
    row.appendChild(lb);

    if (canMakeRoom) {
      const lowerName = pairs[selectedPair + 1].name;
      const rm = document.createElement("button");
      rm.className = "quadRoom";
      rm.textContent = "\u21E3";
      rm.title = "Make room: shift the offending segment on "
               + lowerName
               + " \u2014 and every segment that follows it in the"
               + " same direction \u2014 so this segment no longer"
               + " overlaps it";
      rm.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const moved = _makeRoomInferior(selectedPair, j);
        if (moved > 0) {
          syncPairList();
          draw();
          flashStatus("Made room on " + lowerName
                      + " (" + moved + " shift" +
                      (moved === 1 ? "" : "s") + ")", "ok");
        } else {
          flashStatus("Could not resolve overlap", "warn");
        }
      });
      row.appendChild(rm);
    }

    const del = document.createElement("button");
    del.className = "quadDel";
    del.textContent = "\u00D7";
    del.title = "Delete segment";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteSegment(selectedPair, j);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      selectedSegment = { pairIdx: selectedPair, segIdx: j };
      syncSegmentList();
      draw();
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}

/* ==========================================================================
   PAIR LIST
   ========================================================================== */

function syncPairList() {
  syncSegmentList();
  _syncPairFieldInputs();

  const list = document.getElementById("pairList");
  if (!list) return;
  list.innerHTML = "";
  if (pairs.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no pairs yet";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }

  const overlapOn = !!state.overlapHighlight;

  for (let i = 0; i < pairs.length; i++) {
    const p = pairs[i];
    const hue = pairHue(p);
    const isSel = (i === selectedPair);
    const overlap = overlapOn && _pairHasAnyOverlap(i);

    const row = document.createElement("div");
    row.className = "quadRow"
                  + (isSel ? " selected" : "")
                  + (overlap ? " overlapRow" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    sw.style.background  = _huergb(hue, isSel ? 0.95 : 0.55);
    sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.85);
    sw.style.border = "1px solid " + sw.style.borderColor;
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text";
    lb.className = "quadLabelInput";
    lb.value = p.name;
    lb.spellcheck = false;
    const startName = p.name;
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => {
      p.name = lb.value;
      draw();
    });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") {
        p.name = startName;
        lb.value = startName;
        lb.blur();
        draw();
      }
    });
    row.appendChild(lb);

    const up = document.createElement("button");
    up.className = "quadMv";
    up.textContent = "\u2191";
    up.title = "Move pair up in the hierarchy";
    up.disabled = (i === 0);
    up.addEventListener("click", (ev) => {
      ev.stopPropagation();
      movePair(i, -1);
    });
    row.appendChild(up);

    const dn = document.createElement("button");
    dn.className = "quadMv";
    dn.textContent = "\u2193";
    dn.title = "Move pair down in the hierarchy";
    dn.disabled = (i === pairs.length - 1);
    dn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      movePair(i, +1);
    });
    row.appendChild(dn);

    const del = document.createElement("button");
    del.className = "quadDel";
    del.textContent = "\u00D7";
    del.title = "Delete pair";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deletePair(i);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      selectedPair = i;
      selectedSegment = null;
      syncPairList();
      draw();
      pgkitNotifyAll();
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}
"""
