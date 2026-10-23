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
    openItem            scope: row        (level: files,
                                          appliesTo: !open && !missing)
    deleteItem          scope: row        (level: files)

Board does not use the pair playground's `setAfterChange` hook.
Each action calls `syncFileList()`, `draw()`, `pgkitNotifyAll()`
in its own body, matching text_board's convention.  Cleaner for
board's eight actions, none of which share a common mutation
pattern.

RETURN-YOUR-ASYNC-WORK
======================
Five action wrappers in this file call an `async function` and
discard its Promise.  All five are the same shape: the underlying
helper does an `await fetch("/krita/...")` that is the whole point
of the action, so without a `return` the `command_result` fires
before the fetch is even sent:

    _addFilesAction        → pickFilesViaServer()
    _retrieveOpenAction    → retrieveOpenFromKrita()
    _openAllAction         → openAllMissing()
    _loadBoardFromPayload  → loadBoardFromText(board_json)
    _projectOverlapsAction → projectSelectedOntoOverlaps()

Each is now `return <helper>(...)`.  In the loadBoard case the
helper also returns `true`/`false`, so the boolean reaches
`command_result.result` — the same reasoning as pair and cone.

`_openItemByTarget` was already correct: both branches
(`return activateFile(f)` and `return openFileInKrita(f).then(…)`)
propagate the promise.  `_deleteItemByTarget` is fully
synchronous.  The sync helpers (`_arrangeGridAction`, `_fitAction`,
`_resetViewAction`, `_clearBoardAction`, `_saveSceneAction`) do
their work synchronously and correctly return `undefined`.

WHY DYNAMIC IMPORTS ARE NOT NEEDED HERE
=======================================
Every handler calls `syncFileList`, `draw`, `pgkitNotifyAll`
statically — no `import()` inside a handler.  There is no cycle
from `pg_panel_bindings` back to `pg_dispatch`, so static imports
of `draw` and `updateStatus` from `pg_dispatch` are safe.
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
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

// Five wrappers below call an async helper and would otherwise
// discard its Promise.  See the module docstring, RETURN-YOUR-
// ASYNC-WORK.  In each case the helper does an `await fetch(...)`
// that IS the action — the fetch completes when the .kra has been
// opened / the picker has been dispatched / the layout has been
// merged / the transforms have been pasted — so the wrapper must
// return the promise for `command_result` to fire at the right
// moment.

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
  // loadBoardFromText is `async function` — it applies the layout,
  // then awaits openAllMissing() so every closed file is opened in
  // Krita before the refresh.  Return the promise so command_result
  // fires when the whole load-and-open sequence has finished, and
  // propagate the boolean so a CLI script can tell success from
  // failure.
  return loadBoardFromText(payload.board_json);
}

function _projectOverlapsAction() {
  return projectSelectedOntoOverlaps();
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
