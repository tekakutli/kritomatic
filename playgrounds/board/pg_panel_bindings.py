"""
pg_panel_bindings.py — button bindings, remote actions, remote
levels, and pgkitHandleSelect.

LEVELS
======
    files    one item per file on the board.  selected() reflects
             board.selectedIdx.  No nested levels.

ACTION SCOPES
=============
    global      refreshFromKrita, addFiles, retrieveOpen, openAll,
                arrangeGrid, fit, resetView, clearBoard, saveBoard,
                loadBoard (requires board_json file)
    selection   (on files) projectOverlaps
    row         (on files) openItem  (appliesTo: !open && !missing)
                          deleteItem (appliesTo: () => true)
"""

PG_PANEL_BINDINGS_JS = r"""
/* ==========================================================================
   ACTION HELPERS
   ========================================================================== */

function _addFilesAction() {
  pickFilesViaServer();
}

function _retrieveOpenAction() {
  retrieveOpenFromKrita();
}

function _openAllAction() {
  openAllMissing();
}

function _arrangeGridAction() {
  arrangeGrid();
  draw();
  flashStatus("arranged " + board.files.length + " cards", "ok");
}

function _fitAction() {
  fitAll();
}

function _resetViewAction() {
  view.zoom = 1;
  view.panX = window.innerWidth  / 2;
  view.panY = window.innerHeight / 2;
  pgkitNotifyLocal("view.zoom");
  pgkitNotifyLocal("view.panX");
  pgkitNotifyLocal("view.panY");
  draw();
}

function _clearBoardAction() {
  if (board.files.length === 0) return;
  board.files = [];
  board.selectedIdx = -1;
  syncFileList();
  draw();
  flashStatus("board cleared", "ok");
  pgkitNotifyAll();
}

function _saveSceneAction() {
  saveBoardJSON();
}

function _loadBoardFromPayload(payload) {
  if (!payload || typeof payload.board_json !== "string") return;
  loadBoardFromText(payload.board_json);
}

function _projectOverlapsAction() {
  projectSelectedOntoOverlaps();
}

function _openItemByTarget(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "file.";
  if (!name.startsWith(prefix)) return;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < board.files.length)) return;
  const f = board.files[idx];
  if (f.open) return activateFile(f);
  return openFileInKrita(f).then(ok => { if (ok) refreshFromKrita(); });
}

function _deleteItemByTarget(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "file.";
  if (!name.startsWith(prefix)) return;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < board.files.length)) return;
  removeFile(idx);
}

/* ==========================================================================
   REMOTE LEVELS
   ========================================================================== */

pgkitRegisterLevel("files", {
  label: "Files",
  items: () => board.files.map((f, i) => ({
    name:  "file." + i,
    label: f.name,
    extra: { state: fileState(f) },
  })),
  selected: () => board.selectedIdx >= 0
    ? "file." + board.selectedIdx
    : null,
  parent: () => null,
});

/* ==========================================================================
   SELECT COMMAND
   ========================================================================== */

function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;
  if (level !== "files") return false;
  const prefix = "file.";
  if (!name.startsWith(prefix)) return false;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < board.files.length)) return false;
  board.selectedIdx = idx;
  bringToTop(idx);
  syncFileList();
  draw();
  return true;
}

/* ==========================================================================
   BUTTON BINDINGS
   ========================================================================== */

(function installPanelBindings() {
  const refreshBtn  = document.getElementById("refreshBtn");
  const addFilesBtn = document.getElementById("addFilesBtn");
  const retrieveBtn = document.getElementById("retrieveBtn");
  const openAllBtn  = document.getElementById("openAllBtn");
  const projectBtn  = document.getElementById("projectBtn");
  const gridBtn     = document.getElementById("gridBtn");
  const fitBtn      = document.getElementById("fitBtn");
  const resetBtn    = document.getElementById("resetBtn");
  const clearBtn    = document.getElementById("clearBtn");
  const saveBtn     = document.getElementById("saveSceneBtn");
  const loadBtn     = document.getElementById("loadSceneBtn");

  if (refreshBtn)  refreshBtn.addEventListener("click", refreshFromKrita);
  if (addFilesBtn) addFilesBtn.addEventListener("click", _addFilesAction);
  if (retrieveBtn) retrieveBtn.addEventListener("click", _retrieveOpenAction);
  if (openAllBtn)  openAllBtn.addEventListener("click", _openAllAction);

  if (projectBtn) projectBtn.addEventListener("click", () => {
    try { _projectOverlapsAction(); }
    catch (err) {
      console.error("[panel] project handler threw:", err);
      flashStatus("project error: " + err.message, "bad");
    }
  });

  if (gridBtn) gridBtn.addEventListener("click", _arrangeGridAction);
  if (fitBtn)  fitBtn.addEventListener("click", _fitAction);
  if (resetBtn) resetBtn.addEventListener("click", _resetViewAction);
  if (clearBtn) clearBtn.addEventListener("click", _clearBoardAction);
  if (saveBtn)  saveBtn.addEventListener("click", _saveSceneAction);
  if (loadBtn)  loadBtn.addEventListener("click", promptLoadBoard);
})();

/* ==========================================================================
   REMOTE ACTION REGISTRATIONS
   ========================================================================== */

pgkitRegisterAction("action.refreshFromKrita", {
  label: "Refresh from Krita",
  scope: "global",
  run: refreshFromKrita,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.addFiles", {
  label: "+ Add files…",
  scope: "global",
  run: _addFilesAction,
  meta: {
    variant: "plain",
    description: "Opens the native file picker on the server machine. " +
                 "Only useful when triggered from the playground's own " +
                 "panel; a remote client cannot see a local dialog.",
  },
});

pgkitRegisterAction("action.retrieveOpen", {
  label: "Retrieve open",
  scope: "global",
  run: _retrieveOpenAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.openAll", {
  label: "Open all",
  scope: "global",
  run: _openAllAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.arrangeGrid", {
  label: "Arrange grid",
  scope: "global",
  run: _arrangeGridAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.fit", {
  label: "Fit",
  scope: "global",
  run: _fitAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.resetView", {
  label: "Reset view",
  scope: "global",
  run: _resetViewAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.clearBoard", {
  label: "Clear",
  scope: "global",
  run: _clearBoardAction,
  meta: { variant: "danger" },
});

pgkitRegisterAction("action.saveBoard", {
  label: "Save board",
  scope: "global",
  run: _saveSceneAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.loadBoard", {
  label: "Load board",
  scope: "global",
  run: _loadBoardFromPayload,
  meta: {
    variant: "plain",
    requires: [
      {
        name:   "board_json",
        kind:   "file",
        label:  "board JSON",
        accept: ".json,application/json",
      },
    ],
  },
});

pgkitRegisterAction("action.projectOverlaps", {
  label: "Project overlaps",
  scope: "selection",
  level: "files",
  run: _projectOverlapsAction,
  meta: { variant: "primary" },
});

/* ---- row actions on files ---------------------------------------- */

pgkitRegisterAction("action.openItem", {
  label: "Open",
  scope: "row",
  level: "files",
  appliesTo: (item) => {
    if (!item || typeof item.name !== "string") return false;
    const prefix = "file.";
    if (!item.name.startsWith(prefix)) return false;
    const idx = parseInt(item.name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < board.files.length)) return false;
    const f = board.files[idx];
    return !f.open && !f.missing;
  },
  run: _openItemByTarget,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.deleteItem", {
  label: "\u00D7",
  scope: "row",
  level: "files",
  appliesTo: () => true,
  run: _deleteItemByTarget,
  meta: { variant: "danger" },
});

/* ==========================================================================
   BOOT HOOK
   ========================================================================== */

function boardBootSync() {
  syncPanelSliders();
  syncFileList();
  refreshFromKrita();
}
"""
