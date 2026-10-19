"""
pg_panel_lists.py — the file list.

`syncFileList` rebuilds the panel's list of file cards and ends with
`pgkitNotifyEntities()`.  It runs after every change to
`board.files`, every selection change, and every model-mutating
action.
"""

PG_PANEL_LISTS_JS = r"""
/* ==========================================================================
   FILE LIST
   ========================================================================== */

function syncFileList() {
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
    row.className = "fileRow"
      + (isSel ? " selected" : "")
      + (state === "closed"  ? " closed"  : "")
      + (state === "missing" ? " missing" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "fileSwatch";
    const hue = f.color || board.palette[0];
    sw.style.background = _rgba(hue, isSel ? 0.95 : 0.55);
    sw.style.border     = "1px solid " + _rgba(hue, isSel ? 1.0 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("span");
    lb.className = "fileLabel";
    lb.textContent = f.name;
    lb.title = f.path;
    row.appendChild(lb);

    const st = document.createElement("span");
    st.className = "fileState " + state;
    if (state === "open")        st.textContent = "\u25CF";
    else if (state === "closed") st.textContent = "\u25CB";
    else                          st.textContent = "\u2717";
    st.title = (state === "open")
      ? (f.modified ? "open in Krita (unsaved changes)" : "open in Krita")
      : (state === "closed" ? "not open in Krita" : "file missing on disk");
    row.appendChild(st);

    if (state === "closed") {
      const openBtn = document.createElement("button");
      openBtn.className = "fileAct open";
      openBtn.textContent = "Open";
      openBtn.title = "Open this file in Krita";
      openBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const ok = await openFileInKrita(f);
        if (ok) await refreshFromKrita();
      });
      row.appendChild(openBtn);
    }

    const del = document.createElement("button");
    del.className = "fileDel";
    del.textContent = "\u00D7";
    del.title = "Remove from the board (does not close it in Krita)";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      removeFile(i);
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
