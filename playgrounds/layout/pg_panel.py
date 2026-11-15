"""Panel: bbox list, buttons, ratio dropdown, state sync.

`board.viewer.model` is hardcoded to `"PixArtMS_Sigma_XL_2_512"` in
`pg_state.js` — drawatoon is a 512-px model, the model choice is not
user-facing.  The ratio dropdown is the only Frame control the user
sees.
"""

PANEL_JS = r"""
import {
  board, setImage, applyCanvasImage, clearImage,
  addBbox, deleteBbox, clearAllBboxes, seedDefault, selectBbox,
  resetViewerToFit,
} from "./pg_state.js";
import {
  uploadImageFile, importFromKrita, checkKritaStatus,
  fetchActiveState, fetchResourceImage,
} from "./pg_client.js";
import { fetchSelectedLayer } from "./pg_autosync.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

const PLAYGROUND_ID = "layout";
const SLOT_ID       = "@thelayout";

// Mirror of pixart_res table keys.  Kept in sync with
// kritomatic_nodes/PixArt/conf.py — node uses exact dims, this list
// is only used to populate the dropdown and compute display aspect
// via float(ratio).
const RATIO_KEYS = [
  "0.25", "0.26", "0.27", "0.28",
  "0.32", "0.33", "0.35", "0.40",
  "0.42", "0.48", "0.50", "0.52",
  "0.57", "0.60", "0.68", "0.72",
  "0.78", "0.82", "0.88", "0.94",
  "1.00", "1.07", "1.13", "1.21",
  "1.29", "1.38", "1.46", "1.67",
  "1.75", "2.00", "2.09", "2.40",
  "2.50", "2.89", "3.00", "3.11",
  "3.62", "3.75", "3.88", "4.00",
];

/* ============================================================
   State sync
   ============================================================ */

let _syncTimer = null;
let _lastStateSnapshot = "";

function _stateSnapshot() {
  return JSON.stringify({
    t: board.textBboxes,
    c: board.characterBboxes,
    r: board.referenceEmbeddings,
    d: [board.imageWidth, board.imageHeight],
    v: board.viewer,
  });
}

function _buildPayload() {
  const toWire = (list) => list.map(b => [
    b.x, b.y, b.x + b.w, b.y + b.h,
  ]);

  return {
    op: "set",
    state: {
      text_bboxes:          toWire(board.textBboxes),
      character_bboxes:     toWire(board.characterBboxes),
      reference_embeddings: board.referenceEmbeddings.map(r => r || null),
      image_dims:           [board.imageWidth, board.imageHeight],
      viewer: {
        x: board.viewer.x,
        y: board.viewer.y,
        w: board.viewer.w,
        h: board.viewer.h,
        model: board.viewer.model,
        ratio: board.viewer.ratio,
      },
    },
  };
}

function _scheduleStateSync() {
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_doStateSync, 250);
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
      const v = (data.version || "").slice(0, 8);
      const el = document.getElementById("stateVersion");
      if (el) el.textContent = v || "—";
      const sl = document.getElementById("statusLine");
      if (sl) {
        sl.textContent = "synced \u00b7 v" + v;
        sl.className = "ok";
      }
    } else {
      const sl = document.getElementById("statusLine");
      if (sl) {
        sl.textContent = "sync failed: " + (data.message || "unknown");
        sl.className = "bad";
      }
    }
  } catch (e) {
    const sl = document.getElementById("statusLine");
    if (sl) {
      sl.textContent = "sync failed: " + e.message;
      sl.className = "bad";
    }
  }
  pgkitNotifyAll();
}

window._layoutOnDragEnd = _scheduleStateSync;

/* ============================================================
   Bbox list
   ============================================================ */

function _renderBboxList() {
  const list = document.getElementById("bboxList");
  if (!list) return;
  list.innerHTML = "";

  if (board.textBboxes.length === 0 && board.characterBboxes.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = board.imageEl
      ? "no bboxes yet \u2014 click + Text region or + Character"
      : "load an image first";
    list.appendChild(e);
    return;
  }

  const _row = (kind, idx, bbox) => {
    const isSel = board.selectedKind === kind && board.selectedIdx === idx;
    const row = document.createElement("div");
    row.className = "bboxRow" + (isSel ? " selected" : "");
    if (isSel) {
      row.style.borderColor = kind === 'text'
        ? "rgba(120,220,255,0.85)"
        : "rgba(255,200,90,0.85)";
    }

    const sw = document.createElement("span");
    sw.className = "bboxSwatch";
    sw.style.background = kind === 'text'
      ? "rgb(120,220,255)"
      : "rgb(255,200,90)";
    row.appendChild(sw);

    const lbl = document.createElement("span");
    lbl.className = "bboxLabel";
    lbl.textContent = (kind === 'text' ? "Text " : "Char ") + (idx + 1);
    row.appendChild(lbl);

    const dims = document.createElement("span");
    dims.className = "bboxDims";
    const pctW = Math.round(bbox.w * 100);
    const pctH = Math.round(bbox.h * 100);
    dims.textContent = pctW + "% \u00d7 " + pctH + "%";
    row.appendChild(dims);

    const del = document.createElement("button");
    del.className = "bboxDel";
    del.textContent = "\u00d7";
    del.title = "Remove bbox";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteBbox(kind, idx);
      _renderBboxList();
      _scheduleStateSync();
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      selectBbox(kind, idx);
      _renderBboxList();
    });

    list.appendChild(row);
  };

  for (let i = 0; i < board.textBboxes.length; i++) {
    _row('text', i, board.textBboxes[i]);
  }
  for (let i = 0; i < board.characterBboxes.length; i++) {
    _row('character', i, board.characterBboxes[i]);
  }
}

/* ============================================================
   Panel sync
   ============================================================ */

export function syncPanelFields() {
  const e = {
    imageInfo:      document.getElementById("imageInfo"),
    addText:        document.getElementById("addTextBboxBtn"),
    addChar:        document.getElementById("addCharacterBboxBtn"),
    seed:           document.getElementById("seedDefaultBtn"),
    clear:          document.getElementById("clearBboxesBtn"),
    viewerRatio:    document.getElementById("viewerRatio"),
  };

  if (e.imageInfo) {
    const text = board.imageEl
      ? board.imageWidth + " \u00d7 " + board.imageHeight
        + "   [" + String(board.imageHash).slice(0, 8) + "\u2026]"
      : "No image loaded";
    if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
  }

  if (e.viewerRatio && document.activeElement !== e.viewerRatio) {
    if (e.viewerRatio.value !== board.viewer.ratio) {
      e.viewerRatio.value = board.viewer.ratio;
    }
  }

  const hasImage = !!board.imageEl;
  if (e.addText) e.addText.disabled = !hasImage;
  if (e.addChar) e.addChar.disabled = !hasImage;
  if (e.seed)    e.seed.disabled    = !hasImage;
  if (e.clear)   e.clear.disabled   =
    !(board.textBboxes.length + board.characterBboxes.length);
}

/* ============================================================
   Buttons
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
      _scheduleStateSync();
      _renderBboxList();
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
  _scheduleStateSync();
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
  const selector = slot.selector
    || { source: "active", layer_name: null };
  await fetchSelectedLayer(selector);
}

function _addTextBboxAction() {
  if (!board.imageEl) return;
  const n = board.textBboxes.length;
  addBbox('text', {
    x: 0.10,
    y: Math.min(0.85, 0.02 + n * 0.15),
    w: 0.80,
    h: 0.10,
  });
  selectBbox('text', board.textBboxes.length - 1);
  _renderBboxList();
  _scheduleStateSync();
}

function _addCharacterBboxAction() {
  if (!board.imageEl) return;
  const n = board.characterBboxes.length;
  addBbox('character', {
    x: Math.max(0.05, 0.30 - n * 0.05),
    y: 0.15,
    w: 0.40,
    h: 0.75,
  });
  selectBbox('character', board.characterBboxes.length - 1);
  _renderBboxList();
  _scheduleStateSync();
}

function _seedDefaultAction() {
  if (!board.imageEl) return;
  seedDefault();
  _renderBboxList();
  _scheduleStateSync();
}

function _clearAllAction() {
  clearAllBboxes();
  _renderBboxList();
  _scheduleStateSync();
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
  el.textContent = "Drop an image";
  document.body.appendChild(el);
}

function _installViewerDropdowns() {
  const ratioSel = document.getElementById("viewerRatio");
  if (!ratioSel) return;

  if (ratioSel.options.length === 0) {
    for (const k of RATIO_KEYS) {
      const opt = document.createElement("option");
      opt.value = k;
      opt.textContent = k;
      ratioSel.appendChild(opt);
    }
  }
  ratioSel.value = board.viewer.ratio;

  ratioSel.addEventListener("change", () => {
    board.viewer.ratio = ratioSel.value;
    resetViewerToFit();
    _scheduleStateSync();
    _renderBboxList();
  });
}

export function installPanelBindings() {
  const ids = {
    pickImage:  "pickImageBtn",
    clearImage: "clearImageBtn",
    importImg:  "importImageBtn",
    refresh:    "refreshCanvasBtn",
    addText:    "addTextBboxBtn",
    addChar:    "addCharacterBboxBtn",
    seed:       "seedDefaultBtn",
    clearAll:   "clearBboxesBtn",
    kritaCheck: "kritaCheckBtn",
  };
  const el = {};
  for (const k in ids) el[k] = document.getElementById(ids[k]);

  if (el.pickImage)   el.pickImage.addEventListener("click", _pickImageAction);
  if (el.clearImage)  el.clearImage.addEventListener("click", _clearImageAction);
  if (el.importImg)   el.importImg.addEventListener("click", _importImageFromKritaAction);
  if (el.refresh)     el.refresh.addEventListener("click", _refreshCanvasAction);
  if (el.addText)     el.addText.addEventListener("click", _addTextBboxAction);
  if (el.addChar)     el.addChar.addEventListener("click", _addCharacterBboxAction);
  if (el.seed)        el.seed.addEventListener("click", _seedDefaultAction);
  if (el.clearAll)    el.clearAll.addEventListener("click", _clearAllAction);
  if (el.kritaCheck)  el.kritaCheck.addEventListener("click", _kritaStatusAction);

  _installViewerDropdowns();
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
        _scheduleStateSync();
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

export function layoutBootSync() {
  _renderBboxList();
  syncPanelFields();
  _kritaStatusAction();
}

export function onDrawComplete() {
  _renderBboxList();
  syncPanelFields();
}
"""
