"""
pg_panel_lists.py — the two selection lists.

ES module.  `syncQuadList` rebuilds the patch list, calls
`syncSquareList` for the squares of the selected patch, and ends
with `pgkitNotifyEntities()`.

Writes go through `notifyChanged()` (from state) which triggers the
boot's afterChange hook (sync + draw + pgkitNotifyAll).  Editing the
list rows themselves only mutates the model — the next sync reflects
it.
"""

PANEL_LISTS_JS = r"""
import {
  quads, floatSquares, coneState,
  addQuad, cloneQuad, deleteQuad, cloneSquare, deleteSquare,
  notifyChanged,
} from "./pg_state.js";
import {
  patchHue, _huergb, squareDisplayName,
} from "./pg_view_squares_core.js";
import { _syncPatchCoordInputs } from "./pg_panel_fields.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

/* ==========================================================================
   PATCH LIST
   ========================================================================== */

export function syncQuadList() {
  syncSquareList();
  _syncPatchCoordInputs();

  const list = document.getElementById("quadList");
  if (!list) return;
  list.innerHTML = "";
  if (quads.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no patches yet";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }
  for (let i = 0; i < quads.length; i++) {
    const q   = quads[i];
    const hue = patchHue(q);
    const isSel = (i === coneState.selectedQuad);
    const row = document.createElement("div");
    row.className = "quadRow" + (isSel ? " selected" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    sw.style.background  = _huergb(hue, isSel ? 0.95 : 0.55);
    sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text";
    lb.className = "quadLabelInput";
    lb.value = q.name;
    lb.spellcheck = false;
    const startName = q.name;
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => {
      q.name = lb.value;
    });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") {
        q.name = startName;
        lb.value = startName;
        lb.blur();
      }
    });
    row.appendChild(lb);

    const del = document.createElement("button");
    del.className = "quadDel";
    del.textContent = "\u00D7";
    del.title = "Delete patch";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteQuad(i);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      coneState.selectedQuad = i;
      notifyChanged();
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}

/* ==========================================================================
   SQUARE LIST
   ========================================================================== */

export function syncSquareList() {
  const list = document.getElementById("squareList");
  if (!list) return;
  list.innerHTML = "";
  if (coneState.selectedQuad < 0 ||
      coneState.selectedQuad >= quads.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "select a patch";
    list.appendChild(e);
    return;
  }
  const activeQ = quads[coneState.selectedQuad];
  const activeHue = patchHue(activeQ);
  const rows = [];
  for (let i = 0; i < floatSquares.length; i++) {
    if (floatSquares[i].quadId === activeQ.id) {
      rows.push({ sq: floatSquares[i], idx: i });
    }
  }
  if (rows.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no squares on " + activeQ.name + " yet";
    list.appendChild(e);
    return;
  }
  for (const { sq, idx } of rows) {
    const isSel = (idx === coneState.selectedSquare);
    const row = document.createElement("div");
    row.className = "quadRow squareRow" + (isSel ? " selected" : "");
    row.dataset.idx = String(idx);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    sw.style.background  = _huergb(activeHue, isSel ? 0.95 : 0.55);
    sw.style.borderColor = _huergb(activeHue, isSel ? 1.00 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text";
    lb.className = "quadLabelInput";
    lb.value = squareDisplayName(sq);
    lb.spellcheck = false;
    const startName = squareDisplayName(sq);
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => {
      sq.name = lb.value;
    });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") {
        sq.name = startName;
        lb.value = startName;
        lb.blur();
      }
    });
    row.appendChild(lb);

    const del = document.createElement("button");
    del.className = "quadDel";
    del.textContent = "\u00D7";
    del.title = "Delete square";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSquare(idx);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      coneState.selectedSquare = idx;
      notifyChanged();
    });

    list.appendChild(row);
  }
}
"""
