"""Panel: character bbox list, buttons, state sync, analysis wiring.

No viewer ratio selector, no prompt, no seed, no generate.  Just:
  · image load / import / refresh / clear
  · character bbox add / delete / seed-default / clear-all
  · analyze all (calls /analyze_characters)
  · state sync to /state

The analyze flow:
  1. Convert each `board.characterBboxes[i]` to pixel coords
  2. POST {image_b64, bboxes_px} to /analyze_characters
  3. On success, set `board.referenceEmbeddings[i]` from the response
  4. Sync state
  5. Re-render the bbox list so each row reflects "has embedding"
"""

PANEL_JS = r"""
import {
  board, setImage, applyCanvasImage, clearImage,
  addCharacterBbox, deleteCharacterBbox,
  clearAllBboxes, seedDefault, selectBbox,
  setReferenceEmbeddings,
} from "./pg_state.js";
import {
  uploadImageFile, importFromKrita, checkKritaStatus,
  fetchActiveState, fetchResourceImage, analyzeCharacters,
} from "./pg_client.js";
import { fetchSelectedLayer } from "./pg_autosync.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { SYNC_DEBOUNCE_MS } from "./pg_tuning.js";

const PLAYGROUND_ID = "magi";
const SLOT_ID       = "@thecharacter";

/* ============================================================
   State sync
   ============================================================ */

let _syncTimer = null;
let _lastStateSnapshot = "";

function _stateSnapshot() {
  return JSON.stringify({
    c: board.characterBboxes.map(b => ({
      x: b.x, y: b.y, w: b.w, h: b.h,
    })),
    r: board.referenceEmbeddings,
    d: [board.imageWidth, board.imageHeight],
  });
}

function _buildPayload() {
  const toWire = (list) => list.map(b => [
    b.x, b.y, b.x + b.w, b.y + b.h,
  ]);
  return {
    op: "set",
    state: {
      character_bboxes:     toWire(board.characterBboxes),
      reference_embeddings: board.referenceEmbeddings.map(r => r || null),
      image_dims:           [board.imageWidth, board.imageHeight],
    },
  };
}

function _scheduleStateSync() {
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_doStateSync, SYNC_DEBOUNCE_MS);
}

async function _doStateSync() {
  _syncTimer = null;
  const snap = _stateSnapshot();
  if (snap === _lastStateSnapshot) return;
  try {
    const res = await fetch("/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_buildPayload()),
    });
    const data = await res.json();
    if (data.success) {
      _lastStateSnapshot = snap;
      board.lastSync = Date.now();
      const v = (data.version || "").slice(0, 8);
      const el = document.getElementById("stateVersion");
      if (el) el.textContent = v || "—";
    } else {
      flashStatus("state sync failed: " + (data.message || "unknown"), "bad");
    }
  } catch (e) {
    flashStatus("state sync failed: " + e.message, "bad");
  }
  _refreshStatusLine();
  pgkitNotifyAll();
}

window._magiOnDragEnd = _scheduleStateSync;

/* ============================================================
   Bbox list
   ============================================================ */

function _renderBboxList() {
  const list = document.getElementById("bboxList");
  if (!list) return;

  list.innerHTML = "";

  if (board.characterBboxes.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = board.imageEl
      ? "no character boxes yet \u2014 click + Character"
      : "load a reference image first";
    list.appendChild(e);
    return;
  }

  for (let i = 0; i < board.characterBboxes.length; i++) {
    const bbox = board.characterBboxes[i];
    const hasEmbed = board.referenceEmbeddings[i] !== null;
    const isSel = board.selectedIdx === i;

    const row = document.createElement("div");
    row.className = "bboxRow" + (isSel ? " selected" : "");

    const sw = document.createElement("span");
    sw.className = "bboxSwatch" + (hasEmbed ? "" : " dim");
    row.appendChild(sw);

    const lbl = document.createElement("span");
    lbl.className = "bboxLabel";
    lbl.textContent = "Char " + (i + 1);
    row.appendChild(lbl);

    const dims = document.createElement("span");
    dims.className = "bboxDims";
    dims.textContent = Math.round(bbox.w * 100) + "% \u00d7 "
                     + Math.round(bbox.h * 100) + "%";
    row.appendChild(dims);

    const emb = document.createElement("span");
    emb.className = "bboxEmbed " + (hasEmbed ? "yes" : "no");
    emb.textContent = hasEmbed ? "768-d" : "—";
    row.appendChild(emb);

    const del = document.createElement("button");
    del.className = "bboxDel";
    del.textContent = "\u00d7";
    del.title = "Remove bbox";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteCharacterBbox(i);
      _renderBboxList();
      _scheduleStateSync();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      selectBbox(i);
      _renderBboxList();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    });

    list.appendChild(row);
  }
}

/* ============================================================
   Status line
   ============================================================ */

function _refreshStatusLine() {
  const el = document.getElementById("statusLine");
  if (!el) return;

  const total = board.characterBboxes.length;
  const analyzed = board.referenceEmbeddings.filter(r => r !== null).length;

  let text, cls = "";
  if (board.analyzing) {
    text = "analyzing\u2026"; cls = "ok";
  } else if (board.analyzeError) {
    text = "analyze failed: " + board.analyzeError; cls = "bad";
  } else if (!board.imageHash) {
    text = "no image loaded"; cls = "";
  } else if (total === 0) {
    text = "no character boxes yet"; cls = "warn";
  } else if (board.lastSync) {
    const t = new Date(board.lastSync).toTimeString().slice(0, 8);
    text = "synced " + t + " \u00b7 " + analyzed + "/" + total + " embeddings";
    cls = analyzed === total ? "ok" : "warn";
  } else {
    text = analyzed + "/" + total + " embeddings \u00b7 not yet synced";
    cls = "warn";
  }

  if (el.textContent !== text) el.textContent = text;
  el.className = cls;
}

/* ============================================================
   Panel field sync
   ============================================================ */

export function syncPanelFields() {
  const e = {
    imageInfo:      document.getElementById("imageInfo"),
    addChar:        document.getElementById("addCharacterBboxBtn"),
    seed:           document.getElementById("seedDefaultBtn"),
    clear:          document.getElementById("clearBboxesBtn"),
    analyze:        document.getElementById("analyzeAllBtn"),
  };

  if (e.imageInfo) {
    const text = board.imageEl
      ? board.imageWidth + " \u00d7 " + board.imageHeight
        + "   [" + String(board.imageHash).slice(0, 8) + "\u2026]"
      : "No image loaded";
    if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
  }

  const hasImage = !!board.imageEl;
  const hasBboxes = board.characterBboxes.length > 0;
  const busy = !!board.analyzing;

  if (e.addChar) e.addChar.disabled = !hasImage || busy;
  if (e.seed)    e.seed.disabled    = !hasImage || busy;
  if (e.clear)   e.clear.disabled   = !hasBboxes || busy;
  if (e.analyze) e.analyze.disabled = !hasImage || !hasBboxes || busy;

  _refreshStatusLine();
}

/* ============================================================
   Actions
   ============================================================ */

function _pickImageAction() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.addEventListener("change", async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    flashStatus("uploading\u2026", "");
    try {
      const info = await uploadImageFile(file);
      const dataUrl = await _fileToDataUrl(file);
      setImage(info.hash, dataUrl, info.width, info.height);
      flashStatus("image loaded", "ok");
      _renderBboxList();
      syncPanelFields();
      _scheduleStateSync();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    } catch (e) {
      flashStatus("upload failed: " + e.message, "bad");
    }
  });
  input.click();
}

function _fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("could not read file"));
    reader.readAsDataURL(file);
  });
}

function _clearImageAction() {
  clearImage();
  _renderBboxList();
  syncPanelFields();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _importImageFromKritaAction() {
  flashStatus("importing from Krita\u2026", "");
  const res = await importFromKrita({});
  if (!res.success) {
    flashStatus("import failed: " + (res.message || "unknown"), "bad");
    return;
  }
  setImage(res.hash, res.data_url, res.width, res.height);
  flashStatus("imported " + res.width + "\u00d7" + res.height, "ok");
  _renderBboxList();
  syncPanelFields();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _refreshCanvasAction() {
  flashStatus("refreshing " + SLOT_ID + "\u2026", "");
  const state = await fetchActiveState();
  if (!state || state.daemon_reachable === false) {
    flashStatus(SLOT_ID + " refresh failed: daemon unreachable", "bad");
    return;
  }
  const playgrounds = (state.data && state.data.playgrounds) || [];
  const entry = playgrounds.find(p => p.id === PLAYGROUND_ID);
  const slots = (entry && entry.layer_slots) || [];
  const slot = slots.find(s => s.id === SLOT_ID);
  if (!slot) {
    flashStatus(SLOT_ID + " not registered", "warn");
    return;
  }
  const selector = slot.selector || { source: "active", layer_name: null };
  await fetchSelectedLayer(selector);
}

function _addCharacterBboxAction() {
  if (!board.imageEl) return;
  const n = board.characterBboxes.length;
  addCharacterBbox({
    x: Math.max(0.05, 0.30 - n * 0.05),
    y: 0.15,
    w: 0.40,
    h: 0.75,
  });
  selectBbox(board.characterBboxes.length - 1);
  _renderBboxList();
  syncPanelFields();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _seedDefaultAction() {
  if (!board.imageEl) return;
  seedDefault();
  _renderBboxList();
  syncPanelFields();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _clearAllAction() {
  clearAllBboxes();
  _renderBboxList();
  syncPanelFields();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _analyzeAllAction() {
  if (!board.imageHash) {
    flashStatus("load a reference image first", "warn");
    return;
  }
  if (board.characterBboxes.length === 0) {
    flashStatus("add at least one character box first", "warn");
    return;
  }

  board.analyzing = true;
  board.analyzeError = null;
  syncPanelFields();
  flashStatus("analyzing " + board.characterBboxes.length + " character(s)\u2026", "");

  const bboxes_px = board.characterBboxes.map(b => [
    b.x * board.imageWidth,
    b.y * board.imageHeight,
    (b.x + b.w) * board.imageWidth,
    (b.y + b.h) * board.imageHeight,
  ]);
  const image_b64 = (board.imageDataUrl || "")
    .replace(/^data:image\/png;base64,/, "");

  let res;
  try {
    res = await analyzeCharacters({
      image_b64: image_b64,
      bboxes_px: bboxes_px,
    });
  } catch (e) {
    res = { success: false, message: e.message };
  }

  board.analyzing = false;

  if (!res || !res.success) {
    board.analyzeError = (res && res.message) || "unknown";
    flashStatus("analyze failed: " + board.analyzeError, "bad");
    syncPanelFields();
    return;
  }

  const embs = res.reference_embeddings;
  if (!Array.isArray(embs) || embs.length !== board.characterBboxes.length) {
    board.analyzeError = "server returned wrong number of embeddings";
    flashStatus("analyze failed: " + board.analyzeError, "bad");
    syncPanelFields();
    return;
  }

  setReferenceEmbeddings(embs);
  board.analyzeError = null;
  flashStatus("analyzed " + embs.length + " character(s)", "ok");
  pgkitNotifyAll();
  _scheduleStateSync();
  _renderBboxList();
  syncPanelFields();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _kritaStatusAction() {
  const res = await checkKritaStatus();
  const el = document.getElementById("kritaStatus");
  if (!el) return;
  if (res.reachable) {
    el.textContent = "\u2713 reachable";
    el.className = "ok";
  } else {
    el.textContent = "\u2717 not reachable";
    el.className = "bad";
  }
}

/* ============================================================
   Install
   ============================================================ */

function _showDropOverlay(on) {
  const el = document.getElementById("dropOverlay");
  if (el) el.classList.toggle("visible", on);
}

function _installDropOverlay() {
  if (document.getElementById("dropOverlay")) return;
  const el = document.createElement("div");
  el.id = "dropOverlay";
  el.textContent = "Drop a reference image";
  document.body.appendChild(el);
}

export function installPanelBindings() {
  const ids = {
    pickImage:   "pickImageBtn",
    clearImage:  "clearImageBtn",
    importImg:   "importImageBtn",
    refresh:     "refreshCanvasBtn",
    addChar:     "addCharacterBboxBtn",
    seed:        "seedDefaultBtn",
    clearAll:    "clearBboxesBtn",
    analyzeAll:  "analyzeAllBtn",
    kritaCheck:  "kritaCheckBtn",
  };
  const el = {};
  for (const k in ids) el[k] = document.getElementById(ids[k]);

  if (el.pickImage)   el.pickImage.addEventListener("click", _pickImageAction);
  if (el.clearImage)  el.clearImage.addEventListener("click", _clearImageAction);
  if (el.importImg)   el.importImg.addEventListener("click", _importImageFromKritaAction);
  if (el.refresh)     el.refresh.addEventListener("click", _refreshCanvasAction);
  if (el.addChar)     el.addChar.addEventListener("click", _addCharacterBboxAction);
  if (el.seed)        el.seed.addEventListener("click", _seedDefaultAction);
  if (el.clearAll)    el.clearAll.addEventListener("click", _clearAllAction);
  if (el.analyzeAll)  el.analyzeAll.addEventListener("click", _analyzeAllAction);
  if (el.kritaCheck)  el.kritaCheck.addEventListener("click", _kritaStatusAction);

  _installDropOverlay();
  let _dragDepth = 0;
  window.addEventListener("dragenter", () => {
    _dragDepth++;
    _showDropOverlay(true);
  });
  window.addEventListener("dragleave", () => {
    _dragDepth--;
    if (_dragDepth <= 0) _showDropOverlay(false);
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", async (e) => {
    e.preventDefault();
    _dragDepth = 0;
    _showDropOverlay(false);
    const file = e.dataTransfer && e.dataTransfer.files
                 && e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) {
      flashStatus("uploading\u2026", "");
      try {
        const info = await uploadImageFile(file);
        const dataUrl = await _fileToDataUrl(file);
        setImage(info.hash, dataUrl, info.width, info.height);
        _renderBboxList();
        syncPanelFields();
        _scheduleStateSync();
        import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
      } catch (e) {
        flashStatus("upload failed: " + e.message, "bad");
      }
    }
  });

  _bootPull().catch(() => {});
}

async function _bootPull() {
  try {
    const res = await fetch("/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (data.success) {
      const v = (data.version || "").slice(0, 8);
      const el = document.getElementById("stateVersion");
      if (el) el.textContent = v || "—";
      _lastStateSnapshot = _stateSnapshot();
    }
  } catch (_) {}
  _renderBboxList();
  syncPanelFields();
}

export function magiBootSync() {
  _renderBboxList();
  syncPanelFields();
  _kritaStatusAction();
}

export function onDrawComplete() {
  _renderBboxList();
  syncPanelFields();
}
"""