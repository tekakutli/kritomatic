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
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

function _addFilesAction() { pickFilesViaServer(); }
function _retrieveOpenAction() { retrieveOpenFromKrita(); }
function _openAllAction() { openAllMissing(); }
function _arrangeGridAction() { arrangeGrid(); draw(); flashStatus("arranged " + board.files.length + " cards", "ok"); }
function _fitAction() { fitAll(); }

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
  draw();
  flashStatus("board cleared", "ok");
  pgkitNotifyAll();
}

function _saveSceneAction() { saveBoardJSON(); }

function _loadBoardFromPayload(payload) {
  if (!payload || typeof payload.board_json !== "string") return;
  loadBoardFromText(payload.board_json);
}

function _projectOverlapsAction() { projectSelectedOntoOverlaps(); }

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
  syncFileList();
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
  refreshFromKrita();
}
"""
