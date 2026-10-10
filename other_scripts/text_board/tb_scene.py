"""
tb_scene.py — save and load the board.

Only presentation is persisted: board settings, view state, the
current board scale, and — for reference — each item's board
rectangle.  Content is always pulled fresh from Krita on load, and
positions are recomputed from the documents' current shapes via
`_rebaseAllItems`, so a saved board is a set of settings, not a
snapshot of a layout that will go stale.
"""

SCENE_JS = r"""
const SCENE_VERSION = 2;
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
      boardScale:    _boardScale,
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
    if (typeof data.board.boardScale === "number")
      _boardScale = data.board.boardScale;
    else
      _boardScale = _boardScaleForWidth(board.defaultWidth);
  }
  if (data.view) {
    if (typeof data.view.zoom === "number") view.zoom = data.view.zoom;
    if (typeof data.view.panX === "number") view.panX = data.view.panX;
    if (typeof data.view.panY === "number") view.panY = data.view.panY;
  }

  // bz is preserved across a load so a user's manual stacking
  // survives; positions are recomputed from the documents.
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
  draw();
}

async function loadBoardFromText(text) {
  let data;
  try { data = JSON.parse(text); }
  catch (e) { flashStatus("Load failed: not valid JSON", "bad"); return false; }

  try { applyBoardScene(data); }
  catch (e) { flashStatus("Load failed: " + e.message, "bad"); return false; }

  // Refresh to pick up content, then re-apply the settings.
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
