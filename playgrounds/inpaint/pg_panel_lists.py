"""The mask list panel row renderer.  Called once per frame from
`pg_dispatch.draw()`."""

PANEL_LISTS_JS = r"""
import { board, maskColor } from "./pg_state.js";
import {
  isMaskSelected, toggleMaskSelection, addMaskToSelection,
  replaceMaskSelection,
} from "./pg_masks.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

export function syncMaskList() {
  const list = document.getElementById("maskList");
  const count = document.getElementById("maskCount");
  if (!list) return;
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
