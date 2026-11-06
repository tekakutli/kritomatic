"""
pg_panel_bindings.py — buttons, remote actions, remote levels,
pgkitHandleSelect, and boardBootSync.

ES module.  Exports:

    installPanelBindings   attach every button listener
    pgkitHandleSelect      translate the remote `select` command
    boardBootSync          initial panel sync + async refresh

LEVELS
======
One: `files` — one item per card on the board.  No nested levels;
`parent()` always returns null.  `selected()` reflects
`board.selectedIdx`.

ACTIONS
=======
Every action follows the pgkit pattern — `scope`, optional `level`,
optional `appliesTo` for row scope, optional `requires` for
payloads, optional `variant` for the button's color.

Board's action set:

    refreshFromKrita    scope: global
    addFiles            scope: global     (opens native dialog)
    retrieveOpen        scope: global
    openAll             scope: global
    arrangeGrid         scope: global
    fit                 scope: global
    resetView           scope: global
    clearBoard          scope: global     (variant: danger)
    saveBoard           scope: global
    loadBoard           scope: global     (requires: board_json file)
    projectOverlaps     scope: selection  (level: files)
    newCrop             scope: global
    createCropFile      scope: global
    gridToggle          scope: global     (per selected file)
    gridAddV            scope: global     (per selected file)
    gridAddH            scope: global     (per selected file)
    createCellCrops     scope: global     (per selected file)
    openItem            scope: row        (level: files,
                                          appliesTo: !open && !missing)
    deleteItem          scope: row        (level: files)

Board does not use the pair playground's `setAfterChange` hook.
Each action calls `syncFileList()`, `draw()`, `pgkitNotifyAll()`
in its own body, matching text_board's convention.

RETURN-YOUR-ASYNC-WORK
======================
Action wrappers that call an `async function` `return` its Promise,
so `command_result` fires after the underlying fetch settles:

    _addFilesAction        → pickFilesViaServer()
    _retrieveOpenAction    → retrieveOpenFromKrita()
    _openAllAction         → openAllMissing()
    _loadBoardFromPayload  → loadBoardFromText(board_json)
    _projectOverlapsAction → projectSelectedOntoOverlaps()
    _createCropFileAction  → createCropFile()
    _createCellCropsAction → createCellCrops()

`_openItemByTarget` propagates its promise in both branches.
`_deleteItemByTarget` is fully synchronous.  The sync helpers
(arrange, fit, reset, clear, save, new-crop, grid-toggle,
grid-add-v, grid-add-h) do their work synchronously and correctly
return `undefined`.
"""

PG_PANEL_BINDINGS_JS = r"""
import {
  board, view, arrangeGrid, fitAll, bringToTop, fileState, removeFile,
} from "./pg_core.js";
import { selectedFile, _syncCardFields } from "./pg_panel_fields.js";
import { syncFileList } from "./pg_panel_lists.js";
import { syncPanelSliders } from "./pg_panel_scrubs.js";
import { draw, updateStatus } from "./pg_dispatch.js";
import {
  refreshFromKrita, retrieveOpenFromKrita, openAllMissing,
  pickFilesViaServer, projectSelectedOntoOverlaps, activateFile,
  openFileInKrita,
} from "./pg_krita.js";
import {
  saveBoardJSON, promptLoadBoard, loadBoardFromText,
} from "./pg_scene.js";
import {
  createCropForSelectedFile, createCropFile, syncCropList,
} from "./pg_crops.js";
import {
  toggleGridMode, addVLine, addHLine, createCellCrops, syncGridPanel,
} from "./pg_grid.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

// Wrappers below call an async helper and would otherwise discard
// its Promise.  See the module docstring, RETURN-YOUR-ASYNC-WORK.

function _addFilesAction() {
  return pickFilesViaServer();
}

function _retrieveOpenAction() {
  return retrieveOpenFromKrita();
}

function _openAllAction() {
  return openAllMissing();
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
  view.panX = window.innerWidth / 2;
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
  syncCropList();
  syncGridPanel();
  draw();
  flashStatus("board cleared", "ok");
  pgkitNotifyAll();
}

function _saveSceneAction() {
  saveBoardJSON();
}

function _loadBoardFromPayload(payload) {
  if (!payload || typeof payload.board_json !== "string") {
    promptLoadBoard();
    return;
  }
  return loadBoardFromText(payload.board_json);
}

function _projectOverlapsAction() {
  return projectSelectedOntoOverlaps();
}

function _newCropAction() {
  createCropForSelectedFile();
}

function _createCropFileAction() {
  return createCropFile();
}

function _gridToggleAction() {
  toggleGridMode();
}

function _gridAddVAction() {
  addVLine();
}

function _gridAddHAction() {
  addHLine();
}

function _createCellCropsAction() {
  return createCellCrops();
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
  return openFileInKrita(f).then(ok => {
    if (ok) refreshFromKrita();
  });
}

function _deleteItemByTarget(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "file.";
  if (!name.startsWith(prefix)) return;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < board.files.length)) return;
  removeFile(idx);
  syncFileList();
  syncCropList();
  syncGridPanel();
  draw();
  pgkitNotifyAll();
}

pgkitRegisterLevel("files", {
  label: "Files",
  items: () => board.files.map((f, i) => ({
    name: "file." + i,
    label: f.name,
    extra: { state: fileState(f) },
  })),
  selected: () => board.selectedIdx >= 0 ? "file." + board.selectedIdx : null,
  parent: () => null,
});

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string" || level !== "files") return false;
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

export function installPanelBindings() {
  const els = {
    refreshBtn:  document.getElementById("refreshBtn"),
    addFilesBtn: document.getElementById("addFilesBtn"),
    retrieveBtn: document.getElementById("retrieveBtn"),
    openAllBtn:  document.getElementById("openAllBtn"),
    projectBtn:  document.getElementById("projectBtn"),
    gridBtn:     document.getElementById("gridBtn"),
    fitBtn:      document.getElementById("fitBtn"),
    resetBtn:    document.getElementById("resetBtn"),
    clearBtn:    document.getElementById("clearBtn"),
    saveBtn:     document.getElementById("saveSceneBtn"),
    loadBtn:     document.getElementById("loadSceneBtn"),
  };

  if (els.refreshBtn)  els.refreshBtn.addEventListener("click", refreshFromKrita);
  if (els.addFilesBtn) els.addFilesBtn.addEventListener("click", _addFilesAction);
  if (els.retrieveBtn) els.retrieveBtn.addEventListener("click", _retrieveOpenAction);
  if (els.openAllBtn)  els.openAllBtn.addEventListener("click", _openAllAction);
  if (els.projectBtn)  els.projectBtn.addEventListener("click", () => {
    try { _projectOverlapsAction(); }
    catch (err) { console.error("[panel] project handler threw:", err); flashStatus("project error: " + err.message, "bad"); }
  });
  if (els.gridBtn)  els.gridBtn.addEventListener("click", _arrangeGridAction);
  if (els.fitBtn)   els.fitBtn.addEventListener("click", _fitAction);
  if (els.resetBtn) els.resetBtn.addEventListener("click", _resetViewAction);
  if (els.clearBtn) els.clearBtn.addEventListener("click", _clearBoardAction);
  if (els.saveBtn)  els.saveBtn.addEventListener("click", _saveSceneAction);
  if (els.loadBtn)  els.loadBtn.addEventListener("click", () => promptLoadBoard());

  const newCropBtn = document.getElementById("newCropBtn");
  if (newCropBtn) newCropBtn.addEventListener("click", _newCropAction);
  const createCropFileBtn = document.getElementById("createCropFileBtn");
  if (createCropFileBtn) createCropFileBtn.addEventListener("click", () => {
    _createCropFileAction().catch((err) => {
      console.error("[crop] create handler threw:", err);
      flashStatus("crop error: " + err.message, "bad");
    });
  });

  const gridSplitBtn = document.getElementById("gridSplitBtn");
  if (gridSplitBtn) gridSplitBtn.addEventListener("click", _gridToggleAction);
  const gridAddVBtn = document.getElementById("gridAddVBtn");
  if (gridAddVBtn) gridAddVBtn.addEventListener("click", _gridAddVAction);
  const gridAddHBtn = document.getElementById("gridAddHBtn");
  if (gridAddHBtn) gridAddHBtn.addEventListener("click", _gridAddHAction);
  const gridCreateBtn = document.getElementById("gridCreateBtn");
  if (gridCreateBtn) gridCreateBtn.addEventListener("click", () => {
    _createCellCropsAction().catch((err) => {
      console.error("[grid] create handler threw:", err);
      flashStatus("grid crop error: " + err.message, "bad");
    });
  });
}

pgkitRegisterAction("action.refreshFromKrita", { label: "Refresh from Krita", scope: "global", run: refreshFromKrita, meta: { variant: "plain" } });
pgkitRegisterAction("action.addFiles", { label: "+ Add files…", scope: "global", run: _addFilesAction, meta: { variant: "plain", description: "Opens the native file picker on the server machine. Only useful when triggered from the playground's own panel; a remote client cannot see a local dialog." } });
pgkitRegisterAction("action.retrieveOpen", { label: "Retrieve open", scope: "global", run: _retrieveOpenAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.openAll", { label: "Open all", scope: "global", run: _openAllAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.arrangeGrid", { label: "Arrange grid", scope: "global", run: _arrangeGridAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.fit", { label: "Fit", scope: "global", run: _fitAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.resetView", { label: "Reset view", scope: "global", run: _resetViewAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.clearBoard", { label: "Clear", scope: "global", run: _clearBoardAction, meta: { variant: "danger" } });
pgkitRegisterAction("action.saveBoard", { label: "Save board", scope: "global", run: _saveSceneAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.loadBoard", {
  label: "Load board", scope: "global", run: _loadBoardFromPayload,
  meta: { variant: "plain", requires: [{ name: "board_json", kind: "file", label: "board JSON", accept: ".json,application/json" }] },
});
pgkitRegisterAction("action.projectOverlaps", { label: "Project overlaps", scope: "selection", level: "files", run: _projectOverlapsAction, meta: { variant: "primary" } });
pgkitRegisterAction("action.newCrop", { label: "+ New crop", scope: "global", run: _newCropAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.createCropFile", { label: "Create crop file", scope: "global", run: _createCropFileAction, meta: { variant: "primary" } });
pgkitRegisterAction("action.gridToggle", { label: "Grid split", scope: "global", run: _gridToggleAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.gridAddV", { label: "+ V line", scope: "global", run: _gridAddVAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.gridAddH", { label: "+ H line", scope: "global", run: _gridAddHAction, meta: { variant: "plain" } });
pgkitRegisterAction("action.createCellCrops", { label: "Create cell crops", scope: "global", run: _createCellCropsAction, meta: { variant: "primary" } });
pgkitRegisterAction("action.openItem", {
  label: "Open", scope: "row", level: "files",
  appliesTo: (item) => {
    if (!item || typeof item.name !== "string") return false;
    const prefix = "file.";
    if (!item.name.startsWith(prefix)) return false;
    const idx = parseInt(item.name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < board.files.length)) return false;
    const f = board.files[idx];
    return !f.open && !f.missing;
  },
  run: _openItemByTarget, meta: { variant: "primary" },
});
pgkitRegisterAction("action.deleteItem", { label: "\u00D7", scope: "row", level: "files", appliesTo: () => true, run: _deleteItemByTarget, meta: { variant: "danger" } });

export function boardBootSync() {
  syncPanelSliders();
  syncFileList();
  syncCropList();
  syncGridPanel();
  refreshFromKrita();
}
"""