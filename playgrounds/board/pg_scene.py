"""
pg_scene.py — save and load the board.

The saved board is a list of file paths, the layout the user has
arranged on the canvas, and each card's z and rotation.  Thumbnails
are not saved — they are pulled fresh from Krita.

On load, three things happen in order:

    1.  Paths, layout, z, and rotation are applied.
    2.  Every file whose path is not currently open in Krita is opened
        in Krita, sequentially, via the same endpoint the per-row
        "Open" button uses.
    3.  A single refresh picks up thumbnails and state for everything
        that just opened.

File shape:

    {
      "sceneVersion": 4,
      "type":         "board_scene",
      "generatedAt":  "2026-...",
      "board": { "defaultWidth": 320, "gridGap": 24 },
      "view":  { "zoom": ..., "panX": ..., "panY": ... },
      "files": [
        { "path": ..., "name": ..., "x": ..., "y": ...,
          "w": ..., "aspect": ..., "z": ..., "rotation": ...,
          "color": { "r": ..., "g": ..., "b": ... } },
        ...
      ]
    }
"""

SCENE_JS = r"""
/* ==========================================================================
   SCENE SAVE / LOAD
   ========================================================================== */

const SCENE_VERSION = 4;
const SCENE_TYPE    = "board_scene";
const SCENE_FILE    = "board_scene.json";

function buildBoardScene() {
  return {
    sceneVersion: SCENE_VERSION,
    type:         SCENE_TYPE,
    generatedAt:  new Date().toISOString(),
    board: {
      defaultWidth: board.defaultWidth,
      gridGap:      board.gridGap,
    },
    view: {
      zoom: view.zoom,
      panX: view.panX,
      panY: view.panY,
    },
    files: board.files.map(f => ({
      path:     f.path,
      name:     f.name,
      x:        f.x,
      y:        f.y,
      w:        f.w,
      aspect:   f.aspect,
      z:        f.z,
      rotation: f.rotation,
      color:    f.color ? { r: f.color.r, g: f.color.g, b: f.color.b } : null,
    })),
  };
}

function saveBoardJSON() {
  const text = JSON.stringify(buildBoardScene(), null, 2);
  const url  = "data:application/json;charset=utf-8,"
             + encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = SCENE_FILE;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  flashStatus("saved " + board.files.length + " file path(s)", "ok");
}

function applyBoardScene(data) {
  if (!data || typeof data !== "object") {
    throw new Error("board: not an object");
  }
  if (data.type && data.type !== SCENE_TYPE) {
    throw new Error("board: wrong type '" + data.type
                    + "' (expected '" + SCENE_TYPE + "')");
  }
  if (data.sceneVersion !== SCENE_VERSION) {
    throw new Error("board: unsupported sceneVersion " + data.sceneVersion);
  }

  if (data.board && typeof data.board === "object") {
    if (typeof data.board.defaultWidth === "number") {
      board.defaultWidth = data.board.defaultWidth;
    }
    if (typeof data.board.gridGap === "number") {
      board.gridGap = data.board.gridGap;
    }
  }

  if (data.view && typeof data.view === "object") {
    if (typeof data.view.zoom === "number") view.zoom = data.view.zoom;
    if (typeof data.view.panX === "number") view.panX = data.view.panX;
    if (typeof data.view.panY === "number") view.panY = data.view.panY;
  }

  const saved = Array.isArray(data.files) ? data.files : [];
  board.files = [];
  board.selectedIdx = -1;
  board.nextZ = 1;

  for (const s of saved) {
    if (!s.path) continue;
    const idx = addFileByPath(s.path);
    if (idx < 0) continue;
    const f = board.files[idx];
    if (typeof s.x === "number") f.x = s.x;
    if (typeof s.y === "number") f.y = s.y;
    if (typeof s.w === "number") f.w = s.w;
    if (typeof s.aspect === "number") f.aspect = s.aspect;
    f.h = Math.round(f.w * (f.aspect || 1));
    if (typeof s.z === "number") f.z = s.z;
    if (typeof s.rotation === "number") f.rotation = s.rotation;
    if (s.color && typeof s.color.r === "number") {
      f.color = { r: s.color.r, g: s.color.g, b: s.color.b };
    }
    if (typeof s.name === "string" && s.name) f.name = s.name;
  }

  let maxZ = 0;
  for (const f of board.files) {
    if (typeof f.z === "number" && f.z > maxZ) maxZ = f.z;
  }
  board.nextZ = Math.max(board.nextZ, maxZ + 1);

  syncPanelSliders();
  syncFileList();
  draw();
}

async function loadBoardFromText(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch (e) {
    flashStatus("Load failed: not valid JSON", "bad");
    return false;
  }
  try {
    applyBoardScene(data);
  } catch (e) {
    flashStatus("Load failed: " + e.message, "bad");
    return false;
  }

  flashStatus("loaded layout; opening files in Krita…", "ok");
  await openAllMissing();

  flashStatus("loaded " + board.files.length + " file(s)", "ok");
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

function promptLoadBoard() {
  _installBoardFileInput().click();
}
"""
