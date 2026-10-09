"""
pg_scene.py — save and load the board layout.

The scene is what the user has arranged: every doc's identity, board
position, size, and assigned hue.  The thumbnails themselves are not
saved — they are pulled fresh from Krita on load — so a saved board
file is small and does not go stale.

File shape:

    {
      "sceneVersion": 1,
      "type":         "board_scene",
      "generatedAt":  "2026-...",
      "board": {
        "defaultWidth": 320,
        "gridGap":      24,
      },
      "view": { "zoom": ..., "panX": ..., "panY": ... },
      "docs": [
        { "id": ..., "name": ..., "x": ..., "y": ...,
          "w": ..., "aspect": ...,
          "color": { "r": ..., "g": ..., "b": ... } },
        ...
      ]
    }

On load, the docs list is applied to whatever documents are currently
open in Krita; docs that are not open are still placed on the board
(showing a "not open" placeholder) so the user can see the layout.
"""

SCENE_JS = r"""
/* ==========================================================================
   SCENE SAVE / LOAD
   ========================================================================== */

const SCENE_VERSION = 1;
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
    docs: board.docs.map(d => ({
      id:     d.id,
      name:   d.name,
      x:      d.x,
      y:      d.y,
      w:      d.w,
      aspect: d.aspect,
      color:  d.color ? { r: d.color.r, g: d.color.g, b: d.color.b } : null,
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
  flashStatus("saved " + board.docs.length + " card positions", "ok");
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

  // Merge the saved layout onto the current docs list.  A saved doc
  // whose name matches a currently-open document updates that doc's
  // position and size.  A saved doc whose name does not match any
  // open document is still placed — the doc is recreated with no
  // thumbnail, so the layout is preserved even when the document
  // was not open at load time.
  const saved = Array.isArray(data.docs) ? data.docs : [];
  const byName = new Map();
  for (const d of board.docs) byName.set(d.name, d);

  const next = [];
  for (const s of saved) {
    if (!s.name) continue;
    const existing = byName.get(s.name);
    if (existing) {
      existing.x      = (typeof s.x === "number") ? s.x : existing.x;
      existing.y      = (typeof s.y === "number") ? s.y : existing.y;
      existing.w      = (typeof s.w === "number") ? s.w : existing.w;
      existing.aspect = (typeof s.aspect === "number") ? s.aspect : existing.aspect;
      if (s.color && typeof s.color.r === "number") {
        existing.color = { r: s.color.r, g: s.color.g, b: s.color.b };
      }
      existing.h = Math.round(existing.w * (existing.aspect || 1));
      next.push(existing);
      byName.delete(s.name);
    } else {
      const aspect = (typeof s.aspect === "number") ? s.aspect : 1.0;
      const doc = {
        id:        s.name,
        name:      s.name,
        thumb:     null,
        aspect:    aspect,
        w:         (typeof s.w === "number") ? s.w : board.defaultWidth,
        h:         Math.round(((typeof s.w === "number") ? s.w : board.defaultWidth) * aspect),
        x:         (typeof s.x === "number") ? s.x : 0,
        y:         (typeof s.y === "number") ? s.y : 0,
        modified:  false,
        active:    false,
        color:     (s.color && typeof s.color.r === "number")
                     ? { r: s.color.r, g: s.color.g, b: s.color.b }
                     : null,
      };
      _assignColor(doc);
      next.push(doc);
    }
  }

  board.docs = next;
  board.selectedIdx = -1;
  syncPanelSliders();
  syncDocList();
  draw();
}

function loadBoardFromText(text) {
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
  flashStatus("loaded " + board.docs.length + " card positions", "ok");
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
