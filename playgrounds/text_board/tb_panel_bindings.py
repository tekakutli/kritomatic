"""
tb_panel_bindings.py — button bindings, remote actions, remote
levels, and pgkitHandleSelect.

LEVELS
======
    documents   one item per `.kra` on the board.  selected() is
                null: no document-level selection exists.
    items       one item per text shape.  selected() reflects
                board.selectedIdx.  parent() reports the parent
                document of the selected item.

ACTION SCOPES
=============
    global      refreshFromKrita, addFiles, addFilesWithPaths,
                openAll, arrangeGrid, fit, resetView, groupByFormat,
                exportCanvasJSON, saveBoard, loadBoard (requires
                board_json file), clearBoard

    all         (on items) none

    selection   (on items) activateSelected

    row         (on items) activateItem, deleteItem
                (on documents) openDocument

BOOT DIAGNOSTICS
================
`pgkitBootSync` is wrapped in try/catch with per-step logging.  The
boot script runs it as the last thing before `resize()`.  Before
this wrapper was added, a single missing function anywhere in the
boot chain would throw out of `pgkitBootSync` and skip `resize()`
— leaving the canvas blank, the item list empty, and no obvious
error unless the browser console was open.  Now each step logs
success or failure, and the outer catch logs the final state.
"""

TB_PANEL_BINDINGS_JS = r"""
/* ==========================================================================
   ACTION HELPERS
   ========================================================================== */

function _addFilesAction() {
  pickFilesViaServer();
}

function _openAllAction() {
  openAllClosed();
}

function _arrangeGridAction() {
  arrangeGrid();
  draw();
  flashStatus("arranged " + board.items.length + " card(s)", "ok");
}

function _fitAction() {
  board._autoFitDone = true;
  fitAll();
}

function _resetViewAction() {
  board._autoFitDone = true;
  fitAll();
}

function _groupByFormatAction() {
  groupByFormat();
  draw();
  flashStatus("grouped by format", "ok");
}

function _exportCanvasAction() {
  exportCanvasJSON();
}

function _saveBoardAction() {
  saveBoardJSON();
}

function _clearBoardAction() {
  if (board.items.length === 0) return;
  board.items = [];
  board.selectedIdx = -1;
  _documentSlots.clear();
  _invalidateDocLayout();
  board._scaleUserSet = false;
  board._autoFitDone  = false;
  syncItemList();
  _syncEditor();
  clearCursorReadout();
  draw();
  flashStatus("board cleared", "ok");
}

function _activateSelectedAction() {
  const it = selectedItem();
  if (!it) {
    flashStatus("no card selected", "warn");
    return;
  }
  activateItemDocument(it);
  flashStatus("activating " + (it.documentName || "document"), "ok");
}

function _activateItemByTarget(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "item.";
  if (!name.startsWith(prefix)) return;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < board.items.length)) return;
  activateItemDocument(board.items[idx]);
  flashStatus("activating " + (board.items[idx].documentName || "document"), "ok");
}

function _deleteItemByTarget(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "item.";
  if (!name.startsWith(prefix)) return;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < board.items.length)) return;
  board.items.splice(idx, 1);
  if (board.selectedIdx === idx) board.selectedIdx = -1;
  else if (board.selectedIdx > idx) board.selectedIdx -= 1;
  syncItemList();
  _syncEditor();
  draw();
  flashStatus("removed from board", "ok");
}

function _openDocumentByTarget(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "document.";
  if (!name.startsWith(prefix)) return;
  const paths = _sortedDocumentPaths();
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < paths.length)) return;
  openOneFileInKrita(paths[idx]);
}

function _addFilesWithPaths(payload) {
  if (!payload || typeof payload.paths !== "string") return;
  const parts = payload.paths.split(";").map(s => s.trim()).filter(Boolean);
  for (const p of parts) addWantedPath(p);
  syncItemList();
  draw();
  openAllClosed();
}

function _loadBoardFromPayload(payload) {
  if (!payload || typeof payload.board_json !== "string") return;
  loadBoardFromText(payload.board_json);
}

/* ==========================================================================
   REMOTE LEVELS
   ========================================================================== */

function _sortedDocumentPaths() {
  return _docLayout().map(e => e.path);
}

pgkitRegisterLevel("documents", {
  label: "Documents",
  items: () => {
    const paths = _sortedDocumentPaths();
    return paths.map((path, i) => {
      const slot = _documentSlots.get(path);
      return {
        name:  "document." + i,
        label: (slot && slot.name) || _pathBasename(path),
      };
    });
  },
  selected: () => null,
  parent:   () => null,
});

pgkitRegisterLevel("items", {
  label: "Items",
  items: () => board.items.map((it, i) => ({
    name:  "item." + i,
    label: (it.layerName || "?") + " \u00b7 "
           + (it.text || "").slice(0, 40),
    extra: {
      documentName: it.documentName,
      layerName:    it.layerName,
    },
  })),
  selected: () => board.selectedIdx >= 0
    ? "item." + board.selectedIdx
    : null,
  parent: () => {
    const it = selectedItem();
    if (!it) return null;
    const paths = _sortedDocumentPaths();
    const idx = paths.indexOf(it.documentPath);
    if (idx < 0) return null;
    return { level: "documents", name: "document." + idx };
  },
});

/* ==========================================================================
   SELECT COMMAND
   ========================================================================== */

function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;

  if (level === "documents") {
    const prefix = "document.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    const paths = _sortedDocumentPaths();
    if (!(idx >= 0 && idx < paths.length)) return false;
    const targetPath = paths[idx];
    for (let i = 0; i < board.items.length; i++) {
      if (board.items[i].documentPath === targetPath) {
        board.selectedIdx = i;
        bringToTop(i);
        syncItemList();
        _syncEditor();
        draw();
        return true;
      }
    }
    return false;
  }

  if (level === "items") {
    const prefix = "item.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < board.items.length)) return false;
    board.selectedIdx = idx;
    bringToTop(idx);
    syncItemList();
    _syncEditor();
    draw();
    return true;
  }

  return false;
}

/* ==========================================================================
   BUTTON BINDINGS
   ========================================================================== */

(function installPanelBindings() {
  const refreshBtn    = document.getElementById("refreshBtn");
  const addFilesBtn   = document.getElementById("addFilesBtn");
  const openAllBtn    = document.getElementById("openAllBtn");
  const gridBtn       = document.getElementById("gridBtn");
  const fitBtn        = document.getElementById("fitBtn");
  const resetBtn      = document.getElementById("resetBtn");
  const groupBtn      = document.getElementById("groupBtn");
  const clearBtn      = document.getElementById("clearBtn");
  const saveBtn       = document.getElementById("saveSceneBtn");
  const loadBtn       = document.getElementById("loadSceneBtn");
  const exportJsonBtn = document.getElementById("exportJsonBtn");

  if (refreshBtn)    refreshBtn.addEventListener("click", refreshFromKrita);
  if (addFilesBtn)   addFilesBtn.addEventListener("click", _addFilesAction);
  if (openAllBtn)    openAllBtn.addEventListener("click", _openAllAction);
  if (gridBtn)       gridBtn.addEventListener("click", _arrangeGridAction);
  if (fitBtn)        fitBtn.addEventListener("click", _fitAction);
  if (resetBtn)      resetBtn.addEventListener("click", _resetViewAction);
  if (groupBtn)      groupBtn.addEventListener("click", _groupByFormatAction);
  if (clearBtn)      clearBtn.addEventListener("click", _clearBoardAction);
  if (saveBtn)       saveBtn.addEventListener("click", _saveBoardAction);
  if (loadBtn)       loadBtn.addEventListener("click", promptLoadBoard);
  if (exportJsonBtn) exportJsonBtn.addEventListener("click", _exportCanvasAction);
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
  label: "+ Add files\u2026",
  scope: "global",
  run: _addFilesAction,
  meta: {
    variant: "plain",
    description: "Opens the native file picker on the server machine. " +
                 "Use action.addFilesWithPaths to add files by path " +
                 "from a remote client.",
  },
});

pgkitRegisterAction("action.addFilesWithPaths", {
  label: "+ Add files by path",
  scope: "all",
  level: "items",
  run: _addFilesWithPaths,
  meta: {
    variant: "primary",
    requires: [
      {
        name:  "paths",
        kind:  "text",
        label: "paths (semicolon-separated)",
      },
    ],
  },
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

pgkitRegisterAction("action.groupByFormat", {
  label: "Group by format",
  scope: "global",
  run: _groupByFormatAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.exportCanvasJSON", {
  label: "Export canvas JSON",
  scope: "global",
  run: _exportCanvasAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.saveBoard", {
  label: "Save board",
  scope: "global",
  run: _saveBoardAction,
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

pgkitRegisterAction("action.clearBoard", {
  label: "Clear board",
  scope: "global",
  run: _clearBoardAction,
  meta: { variant: "danger" },
});

pgkitRegisterAction("action.activateSelected", {
  label: "Activate selected",
  scope: "selection",
  level: "items",
  run: _activateSelectedAction,
  meta: { variant: "plain" },
});

/* ---- row actions on items ---------------------------------------- */

pgkitRegisterAction("action.activateItem", {
  label: "Open",
  scope: "row",
  level: "items",
  appliesTo: () => true,
  run: _activateItemByTarget,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.deleteItem", {
  label: "\u00d7",
  scope: "row",
  level: "items",
  appliesTo: () => true,
  run: _deleteItemByTarget,
  meta: { variant: "danger" },
});

/* ---- row actions on documents ------------------------------------ */

pgkitRegisterAction("action.openDocument", {
  label: "Open",
  scope: "row",
  level: "documents",
  appliesTo: () => true,
  run: _openDocumentByTarget,
  meta: { variant: "primary" },
});

/* ==========================================================================
   BOOT HOOK — WITH PER-STEP DIAGNOSTICS
   ==========================================================================
   Called by page() after installShellControls and before resize().
   Each step is logged; failures do not prevent subsequent steps from
   running, so a single missing function cannot kill the whole boot
   (which is what happened when `syncPanelSliders` was dropped from an
   earlier version of this migration).

   What to look for in the browser console on page load:

       [boot] start                            — script parsed, boot began
       [boot] syncPanelSliders ok              — card-width slider synced
       [boot] syncItemList ok                  — item list populated
       [boot] _syncEditor ok                   — editor DOM synced
       [boot] complete                         — refreshFromKrita() fired

   If the boot stops between two lines, the failing step is named in
   the error log for that step.  If no `[boot]` line appears at all,
   the script did not parse — look for a `SyntaxError` at the top of
   the console. */

function pgkitBootSync() {
  console.log("[boot] start");
  try {
    try {
      syncPanelSliders();
      console.log("[boot] syncPanelSliders ok");
    } catch (e) {
      console.error("[boot] syncPanelSliders failed:", e);
    }
    try {
      syncItemList();
      console.log("[boot] syncItemList ok");
    } catch (e) {
      console.error("[boot] syncItemList failed:", e);
    }
    try {
      _syncEditor();
      console.log("[boot] _syncEditor ok");
    } catch (e) {
      console.error("[boot] _syncEditor failed:", e);
    }
    refreshFromKrita();
    console.log("[boot] complete");
  } catch (e) {
    console.error("[boot] pgkitBootSync failed:", e);
  }
}
"""
