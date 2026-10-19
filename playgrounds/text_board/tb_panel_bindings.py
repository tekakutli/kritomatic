"""
tb_panel_bindings.py — buttons, remote actions, remote levels,
pgkitHandleSelect, and pgkitBootSync.

Exports: installPanelBindings, pgkitHandleSelect, pgkitBootSync,
plus helper functions the boot module's actions use.
"""

TB_PANEL_BINDINGS_JS = r"""
import {
  board, view, bringToTop, arrangeGrid, fitAll, groupByFormat,
  _documentSlots, _invalidateDocLayout, _docLayout, _pathBasename,
  addWantedPath,
} from "./tb_core.js";
import { syncItemList, exportCanvasJSON, clearCursorReadout } from "./tb_panel_lists.js";
import { _syncEditor, syncPanelSliders } from "./tb_panel_fields.js";
import {
  refreshFromKrita, pickFilesViaServer, openAllClosed,
  activateItemDocument, openOneFileInKrita,
} from "./tb_krita.js";
import {
  saveBoardJSON, promptLoadBoard, loadBoardFromText,
} from "./tb_scene.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyLocal,
  pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   HELPERS
   ========================================================================== */

function _arrangeGridAction() {
  arrangeGrid();
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
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
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  flashStatus("grouped by format", "ok");
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
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  flashStatus("board cleared", "ok");
}

function _activateSelectedAction() {
  const idx = board.selectedIdx;
  const it = (idx >= 0 && idx < board.items.length) ? board.items[idx] : null;
  if (!it) { flashStatus("no card selected", "warn"); return; }
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
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
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
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  openAllClosed();
}

function _loadBoardFromPayload(payload) {
  if (!payload || typeof payload.board_json !== "string") return;
  loadBoardFromText(payload.board_json);
}

function _sortedDocumentPaths() {
  return _docLayout().map(e => e.path);
}

/* ==========================================================================
   REMOTE LEVELS
   ========================================================================== */

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
    label: (it.layerName || "?") + " \u00b7 " + (it.text || "").slice(0, 40),
    extra: { documentName: it.documentName, layerName: it.layerName },
  })),
  selected: () => board.selectedIdx >= 0 ? "item." + board.selectedIdx : null,
  parent: () => {
    const idx = board.selectedIdx;
    const it  = (idx >= 0 && idx < board.items.length) ? board.items[idx] : null;
    if (!it) return null;
    const paths = _sortedDocumentPaths();
    const di = paths.indexOf(it.documentPath);
    if (di < 0) return null;
    return { level: "documents", name: "document." + di };
  },
});

/* ==========================================================================
   SELECT HANDLER
   ========================================================================== */

export function pgkitHandleSelect(level, name) {
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
        import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
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
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
    return true;
  }

  return false;
}

/* ==========================================================================
   BUTTON BINDINGS
   ========================================================================== */

export function installPanelBindings() {
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
  if (addFilesBtn)   addFilesBtn.addEventListener("click", pickFilesViaServer);
  if (openAllBtn)    openAllBtn.addEventListener("click", openAllClosed);
  if (gridBtn)       gridBtn.addEventListener("click", _arrangeGridAction);
  if (fitBtn)        fitBtn.addEventListener("click", _fitAction);
  if (resetBtn)      resetBtn.addEventListener("click", _resetViewAction);
  if (groupBtn)      groupBtn.addEventListener("click", _groupByFormatAction);
  if (clearBtn)      clearBtn.addEventListener("click", _clearBoardAction);
  if (saveBtn)       saveBtn.addEventListener("click", saveBoardJSON);
  if (loadBtn)       loadBtn.addEventListener("click", () => promptLoadBoard());
  if (exportJsonBtn) exportJsonBtn.addEventListener("click", exportCanvasJSON);
}

/* ==========================================================================
   ACTIONS
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
  run: pickFilesViaServer,
  meta: {
    variant: "plain",
    description: "Opens the native file picker on the server machine. Use action.addFilesWithPaths to add files by path from a remote client.",
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
      { name: "paths", kind: "text", label: "paths (semicolon-separated)" },
    ],
  },
});

pgkitRegisterAction("action.openAll", {
  label: "Open all", scope: "global", run: openAllClosed, meta: { variant: "plain" },
});

pgkitRegisterAction("action.arrangeGrid", {
  label: "Arrange grid", scope: "global", run: _arrangeGridAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.fit", {
  label: "Fit", scope: "global", run: _fitAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.resetView", {
  label: "Reset view", scope: "global", run: _resetViewAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.groupByFormat", {
  label: "Group by format", scope: "global", run: _groupByFormatAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.exportCanvasJSON", {
  label: "Export canvas JSON", scope: "global", run: exportCanvasJSON, meta: { variant: "plain" },
});

pgkitRegisterAction("action.saveBoard", {
  label: "Save board", scope: "global", run: saveBoardJSON, meta: { variant: "plain" },
});

pgkitRegisterAction("action.loadBoard", {
  label: "Load board",
  scope: "global",
  run: _loadBoardFromPayload,
  meta: {
    variant: "plain",
    requires: [
      { name: "board_json", kind: "file", label: "board JSON", accept: ".json,application/json" },
    ],
  },
});

pgkitRegisterAction("action.clearBoard", {
  label: "Clear board", scope: "global", run: _clearBoardAction, meta: { variant: "danger" },
});

pgkitRegisterAction("action.activateSelected", {
  label: "Activate selected", scope: "selection", level: "items",
  run: _activateSelectedAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.activateItem", {
  label: "Open", scope: "row", level: "items",
  appliesTo: () => true, run: _activateItemByTarget, meta: { variant: "primary" },
});

pgkitRegisterAction("action.deleteItem", {
  label: "\u00d7", scope: "row", level: "items",
  appliesTo: () => true, run: _deleteItemByTarget, meta: { variant: "danger" },
});

pgkitRegisterAction("action.openDocument", {
  label: "Open", scope: "row", level: "documents",
  appliesTo: () => true, run: _openDocumentByTarget, meta: { variant: "primary" },
});

/* ==========================================================================
   BOOT HOOK
   ========================================================================== */

export function pgkitBootSync() {
  syncPanelSliders();
  syncItemList();
  _syncEditor();
  refreshFromKrita();
}
"""
