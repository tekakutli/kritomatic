"""
pg_panel_lists.py — the file list.

ES module.  Exports `syncFileList`, which rebuilds the panel's list
of file cards and ends with `pgkitNotifyEntities()`.

It runs after every change to `board.files`, every selection
change, and every model-mutating action.  The DOM is rebuilt from
scratch — the list is small and rebuilding is simpler than
reconciling.

FILE ROW ACTIONS
================
A file row carries a state dot, an optional per-row ◎ Focus
button, and a × delete button:

    ◎ Focus
        activateFile(file) — bring the file to the front in Krita.
        If the file is open, this POSTs /krita/activate with the
        file's kritaName (or path).  If the file is closed, this
        opens it first (open_document with add_view=True already
        activates the new view), then refreshes.  Disabled for
        missing files.

    × Delete
        Removes the card from the board.  Does not close the file
        in Krita.

The Focus button is always visible; the file's state (open,
closed, missing) is reflected in its tooltip and its disabled
state.  This replaced an earlier "Open" button that only appeared
when the file was closed — the Open click already activated the
new view (via add_view=True on open_document), so it was already
half a Focus button.  Making it a permanent Focus button
generalises the same action to already-open files.

DEAD IMPORT REMOVED
===================
The file used to import `_syncCardFields` from `./pg_panel_fields.js`
but never called it (it is called from `pg_dispatch.draw()` instead).
Removed as part of the ES-module audit.
"""

PG_PANEL_LISTS_JS = r"""
import { board, _rgba, bringToTop, fileState } from "./pg_core.js";
import { draw } from "./pg_dispatch.js";
import { pgkitNotifyEntities, pgkitNotifyAll } from "./pgkit/remote_client.js";

function _shortPath(p) {
  if (!p) return "";
  return String(p).split(/[\\\/]/).pop();
}

function _focusTitle(state, name) {
  if (state === "missing") return "File is missing on disk";
  if (state === "closed")  return "Open " + name + " in Krita and bring it to the front";
  return "Bring " + name + " to the front in Krita";
}

export function syncFileList() {
  const list = document.getElementById("fileList");
  if (!list) return;
  list.innerHTML = "";

  if (board.files.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no files yet — click + Add files… or Retrieve open";
    list.appendChild(e);
    pgkitNotifyEntities();
    return;
  }

  for (let i = 0; i < board.files.length; i++) {
    const f = board.files[i];
    const state = fileState(f);
    const isSel = (i === board.selectedIdx);

    const row = document.createElement("div");
    row.className = "fileRow" + (isSel ? " selected" : "") +
                    (state === "closed" ? " closed" : "") +
                    (state === "missing" ? " missing" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "fileSwatch";
    const hue = f.color || board.palette[0];
    sw.style.background = _rgba(hue, isSel ? 0.95 : 0.55);
    sw.style.border = "1px solid " + _rgba(hue, isSel ? 1.0 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("span");
    lb.className = "fileLabel";
    lb.textContent = f.name;
    lb.title = f.path;
    row.appendChild(lb);

    const st = document.createElement("span");
    st.className = "fileState " + state;
    st.textContent = state === "open" ? "\u25CF" : state === "closed" ? "\u25CB" : "\u2717";
    st.title = state === "open"
      ? (f.modified ? "open in Krita (unsaved changes)" : "open in Krita")
      : (state === "closed" ? "not open in Krita" : "file missing on disk");
    row.appendChild(st);

    const focusBtn = document.createElement("button");
    focusBtn.className = "fileAct focus";
    focusBtn.textContent = "\u25CE";
    focusBtn.title = _focusTitle(state, f.name);
    focusBtn.disabled = state === "missing";
    focusBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const m = await import("./pg_krita.js");
      await m.activateFile(f);
    });
    row.appendChild(focusBtn);

    const del = document.createElement("button");
    del.className = "fileDel";
    del.textContent = "\u00D7";
    del.title = "Remove from the board (does not close it in Krita)";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      board.files.splice(i, 1);
      if (board.selectedIdx === i) board.selectedIdx = -1;
      else if (board.selectedIdx > i) board.selectedIdx -= 1;
      syncFileList();
      draw();
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      board.selectedIdx = i;
      bringToTop(i);
      syncFileList();
      draw();
      pgkitNotifyAll();
    });

    list.appendChild(row);
  }

  pgkitNotifyEntities();
}
"""