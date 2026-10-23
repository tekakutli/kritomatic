PANEL_LISTS_JS = r"""
import { board, setSelectedMask } from "./pg_core.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

export function syncMaskList() {
  const list = document.getElementById("maskList");
  const count = document.getElementById("maskCount");
  if (!list) return;
  list.innerHTML = "";
  if (count) count.textContent = String(board.masks.length);

  if (board.masks.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no masks yet \u2014 click Run";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }

  for (let i = 0; i < board.masks.length; i++) {
    const m = board.masks[i];
    const isSel = (i === board.selectedMaskIdx);

    const row = document.createElement("div");
    row.className = "maskRow" + (isSel ? " selected" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("img");
    sw.className = "maskSwatch";
    sw.src = "data:image/png;base64," + m.b64;
    sw.alt = "";
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
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      board.masks.splice(i, 1);
      if (board.selectedMaskIdx === i) {
        board.selectedMaskIdx = board.masks.length > 0 ? 0 : -1;
      } else if (board.selectedMaskIdx > i) {
        board.selectedMaskIdx -= 1;
      }
      syncMaskList();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      setSelectedMask(i);
      syncMaskList();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}
"""
