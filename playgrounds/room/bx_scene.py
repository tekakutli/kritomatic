"""
bx_scene.py — save and load the board.

ES module.  Exports the scene read/write functions.  The scene format
is settings-plus-layout: every placed box, its position and size,
plus the current view transform.  Loading replaces the box list
wholesale and restores the view.

SCENE CONTENTS
==============
    sceneVersion  1
    type          "boxes_scene"
    view          zoom, panX, panY
    boxes         per-box { name, x, y, w, h, rot }

REMOTE NOTIFICATION
===================
`applyScene` replaces the board's contents, so it ends with
`pgkitNotifyAll()` — one patch reaches the wire with every value any
registered field reads, on the next animation frame.  `saveSceneJSON`
is read-only and does not notify.

DYNAMIC IMPORTS
===============
`loadSceneFromText` needs `flashStatus` (from pgkit/panel_core) and
`callDraw` (from bx_core).  A static import of `draw` would create a
cycle: bx_panel_bindings imports draw; bx_scene is imported by
bx_panel_bindings.  Both are loaded dynamically to sidestep the
ordering problem.
"""

SCENE_JS = r"""
import {
  board, view, invalidateBox, applyLoadedBoxes,
} from "./bx_core.js";
import {
  pgkitNotifyAll,
} from "./pgkit/remote_client.js";

const SCENE_VERSION = 1;
const SCENE_TYPE    = "boxes_scene";
const SCENE_FILE    = "boxes_scene.json";

export function buildScene() {
  return {
    sceneVersion: SCENE_VERSION,
    type:         SCENE_TYPE,
    generatedAt:  new Date().toISOString(),
    view: { zoom: view.zoom, panX: view.panX, panY: view.panY },
    boxes: board.items.map(b => ({
      name: b.name || "",
      x: b.x, y: b.y, w: b.w, h: b.h, rot: b.rot,
    })),
  };
}

export function saveSceneJSON() {
  const text = JSON.stringify(buildScene(), null, 2);
  const url = "data:application/json;charset=utf-8," + encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = SCENE_FILE;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  import("./pgkit/panel_core.js").then(m => {
    m.flashStatus("saved " + board.items.length + " box(es)", "ok");
  }).catch(() => {});
}

export function applyScene(data) {
  if (!data || typeof data !== "object") {
    throw new Error("scene: not an object");
  }
  if (data.type && data.type !== SCENE_TYPE) {
    throw new Error("scene: wrong type '" + data.type + "'");
  }
  if (data.sceneVersion !== SCENE_VERSION) {
    throw new Error("scene: unsupported sceneVersion " + data.sceneVersion);
  }

  if (data.view) {
    if (typeof data.view.zoom === "number") view.zoom = data.view.zoom;
    if (typeof data.view.panX === "number") view.panX = data.view.panX;
    if (typeof data.view.panY === "number") view.panY = data.view.panY;
  }

  applyLoadedBoxes(Array.isArray(data.boxes) ? data.boxes : []);
  pgkitNotifyAll();
}

export async function loadSceneFromText(text) {
  const panel = await import("./pgkit/panel_core.js");
  const core  = await import("./bx_core.js");
  let data;
  try { data = JSON.parse(text); }
  catch (e) { panel.flashStatus("Load failed: not valid JSON", "bad"); return false; }
  try { applyScene(data); }
  catch (e) { panel.flashStatus("Load failed: " + e.message, "bad"); return false; }
  panel.flashStatus("loaded " + board.items.length + " box(es)", "ok");
  core.callDraw();
  return true;
}

let _sceneFileInput = null;
function _installSceneFileInput() {
  if (_sceneFileInput) return _sceneFileInput;
  const inp = document.createElement("input");
  inp.type = "file";
  inp.accept = "application/json,.json";
  inp.style.display = "none";
  inp.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      loadSceneFromText(String(reader.result || ""));
      inp.value = "";
    };
    reader.onerror = () => {
      import("./pgkit/panel_core.js").then(m => {
        m.flashStatus("Load failed: could not read file", "bad");
      }).catch(() => {});
      inp.value = "";
    };
    reader.readAsText(file);
  });
  document.body.appendChild(inp);
  _sceneFileInput = inp;
  return inp;
}

export function promptLoadScene() {
  _installSceneFileInput().click();
}
"""
