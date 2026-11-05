"""The mask list panel row renderer.

Because `draw()` runs on every animation frame during pan / hover /
zoom / rotation, `syncMaskList` would otherwise rebuild the entire
mask DOM — and force a base64 decode of every mask's swatch PNG —
every frame.  That is the panel's largest per-frame cost.

`syncMaskList` therefore keeps a lightweight cache of the state its
rendered DOM depends on — mask count, selection list, and each
mask's identity plus loaded / w / h — and skips the rebuild when
nothing has changed.  Pan / hover / zoom / rotation frames leave the
cache valid; mask add / delete / select / load flip the cache and
the rebuild happens once, exactly when needed.

The identity check is by entry reference, not by b64 content.
Every entry's `b64` and `img` are set once at entry creation and
never mutated; a replaced mask (`replaceMaskAt`) is a fresh entry
object, so the reference check catches it in O(1).  This is
important because the cached `img.onload` can fire before the first
`_scheduleDraw` rAF, and a length-based check would miss a b64
swap that happened to preserve byte length.

Skipping `pgkitNotifyEntities` on a no-op call is safe: the
`remote_client.js` entity broadcast is internally deduplicated by
`_pgkitLastEntitiesSig`, and if the mask list is unchanged the
entity dump is unchanged too."""

PANEL_LISTS_JS = r"""
import { board, maskColor } from "./pg_state.js";
import {
  isMaskSelected, toggleMaskSelection, addMaskToSelection,
  replaceMaskSelection,
} from "./pg_masks.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

/* ============================================================
   Rebuild cache
   ============================================================
   `_lastMaskList` captures everything the rendered DOM depends
   on.  `_maskListChanged` compares current board state against
   the cache; `_cacheMaskList` refreshes the cache after a
   rebuild.  Both are O(mask count) and touch no DOM.

   The `null` sentinels for `selection` and `entries` force the
   first call after boot to rebuild, without a separate code path.

   What is intentionally NOT captured:

     - `board.hoveredMaskIdx` — the row DOM does not depend on
       hover; only `drawImageLayer` in `pg_view_image.js` does.
     - `_pixels`, `_tinted`, `_bbox` — internal pixel caches on
       each mask entry; they do not affect the row DOM.
     - `m.b64` content — the entry reference check covers any
       replacement; `b64` is set once per entry and never mutated
       in place. */

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
      import("./pg_dispatch.js").then(mm => mm.draw()).catch(() => {});
    });
    row.appendChild(del);

    row.addEventListener("click", (ev) => {
      if (ev.ctrlKey || ev.metaKey)      toggleMaskSelection(i);
      else if (ev.shiftKey)              addMaskToSelection(i);
      else                                replaceMaskSelection(i);
      syncMaskList();
      import("./pg_dispatch.js").then(mm => mm.draw()).catch(() => {});
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}
"""
