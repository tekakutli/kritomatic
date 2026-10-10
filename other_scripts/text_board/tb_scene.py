"""
tb_scene.py — save and load the board.

Only layout is persisted: board settings, view state, and the
position / size / z of each card keyed by its item id.  Content is
always pulled fresh from Krita on load.  Cards whose ids no longer
match a shape in the document are dropped silently.
"""

SCENE_JS = r"""
const SCENE_VERSION = 1;
const SCENE_TYPE    = "text_board_scene";
const SCENE_FILE    = "text_board_scene.json";

function buildBoardScene() {
  return {
    sceneVersion: SCENE_VERSION,
    type:         SCENE_TYPE,
    generatedAt:  new Date().toISOString(),
    board: {
      defaultWidth:  board.defaultWidth,
      gridGap:       board.gridGap,
      showWordBoxes: board.showWordBoxes,
    },
    view: {
      zoom: view.zoom,
      panX: view.panX,
      panY: view.panY,
    },
    items: board.items.map(it => ({
      id: it.id,
      bx: it.bx, by: it.by,
      bw: it.bw, bh: it.bh,
      bz: it.bz,
    })),
  };
}

function saveBoardJSON() {
  const text = JSON.stringify(buildBoardScene(), null, 2);
  const url  = "data:application/json;charset=utf-8," +
               encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = SCENE_FILE;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  flashStatus("saved " + board.items.length + " item(s)", "ok");
}

function applyBoardScene(data) {
  if (!data || typeof data !== "object") throw new Error("not an object");
  if (data.type && data.type !== SCENE_TYPE)
    throw new Error("wrong type '" + data.type + "'");
  if (data.sceneVersion !== SCENE_VERSION)
    throw new Error("unsupported sceneVersion " + data.sceneVersion);

  if (data.board) {
    if (typeof data.board.defaultWidth === "number")
      board.defaultWidth = data.board.defaultWidth;
    if (typeof data.board.gridGap === "number")
      board.gridGap = data.board.gridGap;
    if (typeof data.board.showWordBoxes === "boolean")
      board.showWordBoxes = data.board.showWordBoxes;
  }
  if (data.view) {
    if (typeof data.view.zoom === "number") view.zoom = data.view.zoom;
    if (typeof data.view.panX === "number") view.panX = data.view.panX;
    if (typeof data.view.panY === "number") view.panY = data.view.panY;
  }

  const saved = new Map();
  for (const s of (data.items || [])) {
    if (s && s.id) saved.set(s.id, s);
  }

  for (const it of board.items) {
    const s = saved.get(it.id);
    if (!s) continue;
    if (typeof s.bx === "number") it.bx = s.bx;
    if (typeof s.by === "number") it.by = s.by;
    if (typeof s.bw === "number") it.bw = s.bw;
    if (typeof s.bh === "number") it.bh = s.bh;
    if (typeof s.bz === "number") it.bz = s.bz;
  }

  let maxZ = 0;
  for (const it of board.items) if (it.bz > maxZ) maxZ = it.bz;
  board.nextZ = Math.max(board.nextZ, maxZ + 1);

  const wb = document.getElementById("wordBoxToggle");
  if (wb) wb.checked = !!board.showWordBoxes;

  syncPanelSliders();
  syncItemList();
  _syncEditor();
  draw();
}

async function loadBoardFromText(text) {
  let data;
  try { data = JSON.parse(text); }
  catch (e) { flashStatus("Load failed: not valid JSON", "bad"); return false; }

  try { applyBoardScene(data); }
  catch (e) { flashStatus("Load failed: " + e.message, "bad"); return false; }

  // Refresh to pick up content, then re-apply the layout (ids will
  // match because identity is doc + layer + index).
  await refreshFromKrita();
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

function promptLoadBoard() {
  _installBoardFileInput().click();
}
"""
