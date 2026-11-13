"""The mask list panel row renderer.

Rebuild-cache logic is unchanged from before: `syncMaskList` skips the
DOM rebuild when mask count, selection, and each mask's identity/
loaded/dims are unchanged.  That keeps `draw()` cheap on pan / hover /
zoom frames.

NEW: every mask-state mutation (row click for select, delete, ctrl/
shift-click modifiers) schedules a debounced sync so the pushed
`theselection` layer reflects the user's current selection."""

PANEL_LISTS_JS = r"""
import { board, maskColor } from "./pg_state.js";
import {
  isMaskSelected, toggleMaskSelection, addMaskToSelection,
  replaceMaskSelection,
} from "./pg_masks.js";
import { scheduleSync } from "./pg_sync.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

/* ============================================================
   Rebuild cache
   ============================================================ */

let _lastMaskList = {
  count: -1,
  selection: null,
  entries: null,
};

function _maskListChanged() {
  const n = board.masks.length;
  const sel = board.selectedMaskIndices;

  if (_lastMaskList.count !== n) return true;
  if (_lastMaskList.selection === null) return true;
  if (_lastMaskList.selection.length !== sel.length) return true;
  for (let i = 0; i < sel.length; i++) {
    if (_lastMaskList.selection[i] !== sel[i]) return true;
  }

  if (_lastMaskList.entries === null) return true;
  for (let i = 0; i < n; i++) {
    const m = board.masks[i];
    const c = _lastMaskList.entries[i];
    if (c === null) return true;
    if (c.entry !== m) return true;
    if (c.loaded !== (m.loaded ? 1 : 0)) return true;
    if (c.w !== m.w || c.h !== m.h) return true;
  }
  return false;
}

function _cacheMaskList() {
  _lastMaskList = {
    count: board.masks.length,
    selection: board.selectedMaskIndices.slice(),
    entries: board.masks.map(m => ({
      entry: m,
      loaded: m.loaded ? 1 : 0,
      w: m.w,
      h: m.h,
    })),
  };
}

/* ============================================================
   Public API
   ============================================================ */

export function syncMaskList() {
  const list = document.getElementById("maskList");
  const count = document.getElementById("maskCount");
  if (!list) return;

  if (!_maskListChanged()) return;
  _cacheMaskList();

  list.innerHTML = "";
  if (count) {
    count.textContent =
      board.selectedMaskIndices.length + " / " + board.masks.length;
  }

  if (board.masks.length === 0) {
    const el = document.createElement("div");
    el.className = "empty";
    el.textContent = "no masks yet \u2014 import one below";
    list.appendChild(el);
    pgkitNotifyEntities();
    return;
  }

  for (let i = 0; i < board.masks.length; i++) {
    const m = board.masks[i];
    const isSel = isMaskSelected(i);
    const c = maskColor(i);

    const row = document.createElement("div");
    row.className = "maskRow" + (isSel ? " selected" : "");
    row.dataset.idx = String(i);
    if (isSel) {
      row.style.borderColor =
        "rgba(" + c.r + "," + c.g + "," + c.b + ", 0.85)";
      row.style.background =
        "rgba(" + c.r + "," + c.g + "," + c.b + ", 0.08)";
    }

    const sw = document.createElement("img");
    sw.className = "maskSwatch";
    sw.src = "data:image/png;base64," + m.b64;
    sw.alt = "";
    sw.style.borderColor = isSel
      ? "rgba(" + c.r + "," + c.g + "," + c.b + ", 1.0)"
      : "#34445a";
    row.appendChild(sw);

    const lb = document.createElement("span");
    lb.className = "maskLabel";
    lb.textContent = "Mask " + (i + 1);
    row.appendChild(lb);

    const meta = document.createElement("span");
    meta.className = "maskMeta";
    meta.textContent = m.w + "\u00d7" + m.h;
    row.appendChild(meta);

    const del = document.createElement("button");
    del.className = "maskDel";
    del.textContent = "\u00d7";
    del.title = "Remove mask";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      board.masks.splice(i, 1);
      const next = [];
      for (const idx of board.selectedMaskIndices) {
        if (idx < i) next.push(idx);
        else if (idx > i) next.push(idx - 1);
      }
      board.selectedMaskIndices = next;
      if (board.hoveredMaskIdx === i) board.hoveredMaskIdx = -1;
      else if (board.hoveredMaskIdx > i) board.hoveredMaskIdx -= 1;
      syncMaskList();
      scheduleSync();
      import("./pg_dispatch.js").then(mm => mm.draw()).catch(() => {});
    });
    row.appendChild(del);

    row.addEventListener("click", (ev) => {
      if (ev.ctrlKey || ev.metaKey)      toggleMaskSelection(i);
      else if (ev.shiftKey)              addMaskToSelection(i);
      else                                replaceMaskSelection(i);
      syncMaskList();
      scheduleSync();
      import("./pg_dispatch.js").then(mm => mm.draw()).catch(() => {});
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}
"""