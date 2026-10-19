"""
tb_scene.py — save and load the board.

ES module.  Exports the scene read/write functions.  The scene format
is deliberately settings-only: board defaults, view state, and each
item's z-order.  Items themselves are always re-pulled from Krita;
board rectangles are recomputed from document slots by
`_rebaseAllItems`.  A saved board is therefore a stable set of
settings, not a snapshot of a layout that will go stale.

SCENE CONTENTS
==============
    sceneVersion  2
    type          "text_board_scene"
    board         defaultWidth, gridGap, showWordBoxes, boardScale
    view          zoom, panX, panY
    items         per-item { id, bx, by, bw, bh, bz } — position
                  data is re-derived on load, but bz survives so a
                  user's manual stacking is preserved across reloads

REMOTE NOTIFICATION
===================
`applyBoardScene` replaces the board's settings and view state, so
it ends with `pgkitNotifyAll()` — one patch reaches the wire with
every value any registered field reads, on the next animation frame.
`saveBoardJSON` is read-only from the model's perspective and does
not notify.
"""

SCENE_JS = r"""
import {
  board, view, _rebaseAllItems, _computeBoardScale,
} from "./tb_core.js";
import { syncItemList } from "./tb_panel_lists.js";
import { _syncEditor, syncPanelSliders } from "./tb_panel_fields.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

const SCENE_VERSION = 2;
const SCENE_TYPE    = "text_board_scene";
const SCENE_FILE    = "text_board_scene.json";

export function buildBoardScene() {
  return {
    sceneVersion: SCENE_VERSION,
    type:         SCENE_TYPE,
    generatedAt:  new Date().toISOString(),
    board: {
      defaultWidth:  board.defaultWidth,
      gridGap:       board.gridGap,
      showWordBoxes: board.showWordBoxes,
      boardScale:    board.scale,
    },
    view: { zoom: view.zoom, panX: view.panX, panY: view.panY },
    items: board.items.map(it => ({
      id: it.id, bx: it.bx, by: it.by, bw: it.bw, bh: it.bh, bz: it.bz,
    })),
  };
}

export function saveBoardJSON() {
  const text = JSON.stringify(buildBoardScene(), null, 2);
  const url  = "data:application/json;charset=utf-8," + encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = SCENE_FILE;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  flashStatus("saved " + board.items.length + " item(s)", "ok");
}

export function applyBoardScene(data) {
  if (!data || typeof data !== "object") throw new Error("not an object");
  if (data.type && data.type !== SCENE_TYPE) throw new Error("wrong type '" + data.type + "'");
  if (data.sceneVersion !== SCENE_VERSION) throw new Error("unsupported sceneVersion " + data.sceneVersion);

  if (data.board) {
    if (typeof data.board.defaultWidth === "number") board.defaultWidth = data.board.defaultWidth;
    if (typeof data.board.gridGap === "number") board.gridGap = data.board.gridGap;
    if (typeof data.board.showWordBoxes === "boolean") board.showWordBoxes = data.board.showWordBoxes;
    if (typeof data.board.boardScale === "number") board.scale = data.board.boardScale;
    else board.scale = _computeBoardScale();
  }
  if (data.view) {
    if (typeof data.view.zoom === "number") view.zoom = data.view.zoom;
    if (typeof data.view.panX === "number") view.panX = data.view.panX;
    if (typeof data.view.panY === "number") view.panY = data.view.panY;
  }

  const savedBz = new Map();
  for (const s of (data.items || [])) {
    if (s && s.id && typeof s.bz === "number") savedBz.set(s.id, s.bz);
  }
  for (const it of board.items) {
    const bz = savedBz.get(it.id);
    if (typeof bz === "number") it.bz = bz;
  }

  let maxZ = 0;
  for (const it of board.items) if (it.bz > maxZ) maxZ = it.bz;
  board.nextZ = Math.max(board.nextZ, maxZ + 1);

  _rebaseAllItems();

  const wb = document.getElementById("wordBoxToggle");
  if (wb) wb.checked = !!board.showWordBoxes;

  syncPanelSliders();
  syncItemList();
  _syncEditor();
  import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  pgkitNotifyAll();
}

export async function loadBoardFromText(text) {
  let data;
  try { data = JSON.parse(text); }
  catch (e) { flashStatus("Load failed: not valid JSON", "bad"); return false; }

  try { applyBoardScene(data); }
  catch (e) { flashStatus("Load failed: " + e.message, "bad"); return false; }

  const m = await import("./tb_krita.js");
  await m.refreshFromKrita();
  try { applyBoardScene(data); }
  catch (e) { /* already reported */ }

  flashStatus("loaded " + board.items.length + " item(s)", "ok");
  return true;
}

let _boardFileInput = null;
function _installBoardFileInput() {
  if (_boardFileInput) return _boardFileInput;
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = "application/json,.json";
  inp.style.display = "none";
  inp.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      loadBoardFromText(String(reader.result || ""));
      inp.value = "";
    };
    reader.onerror = () => {
      flashStatus("Load failed: could not read file", "bad");
      inp.value = "";
    };
    reader.readAsText(file);
  });
  document.body.appendChild(inp);
  _boardFileInput = inp;
  return inp;
}

export function promptLoadBoard() {
  _installBoardFileInput().click();
}
"""
