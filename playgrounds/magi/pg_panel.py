"""Panel: character list, scratch-rect-driven Add Character flow,
state sync, label editing, crop viewing, save/load.

Workflow:
  1. Load image (file / Krita / drop).
  2. Drag + resize the scratch rect on the canvas.
  3. Click "Add Character" → crop the scratch rect, analyze, add to
     list.
  4. Repeat 2-3 for more characters.
  5. Click a list row to view that character's crop.
  6. "← Back to @thecharacter" to return.
  7. Edit labels inline; delete rows; clear all.
  8. Save state → downloads a self-contained JSON (image + chars +
     crops + 768-d embeddings).  Load state → restores it.
"""

PANEL_JS = r"""
import {
  board, setImage, applyCanvasImage, clearImage,
  addCharacterBbox, deleteCharacterBbox,
  clearAllBboxes, setCharacterLabel,
  setReferenceEmbedding, setViewingCharacter,
  setScratchRect,
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

/* ============================================================
   Crop helper
   ============================================================ */

function _cropFromImage(imageEl, bboxNorm) {
  const W = imageEl.naturalWidth  || imageEl.width;
  const H = imageEl.naturalHeight || imageEl.height;
  const x = Math.floor(bboxNorm.x * W);
  const y = Math.floor(bboxNorm.y * H);
  const w = Math.max(1, Math.floor(bboxNorm.w * W));
  const h = Math.max(1, Math.floor(bboxNorm.h * H));
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  c.getContext("2d").drawImage(imageEl, x, y, w, h, 0, 0, w, h);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

/* ============================================================
   Character list
   ============================================================ */

let _lastListSig = "";

function _listSig() {
  return JSON.stringify({
    n: board.characterBboxes.length,
    v: board.viewingCharacterIdx,
    e: board.referenceEmbeddings.map(r => r ? 1 : 0),
    l: board.characterBboxes.map(b => b.label || ""),
  });
}

function _renderBboxList() {
  const list = document.getElementById("bboxList");
  if (!list) return;

  const sig = _listSig();
  if (sig === _lastListSig) return;
  _lastListSig = sig;

  // Preserve focus state so typing in a label input isn't interrupted
  const ae = document.activeElement;
  const focusedIdx = (ae && ae.classList
    && ae.classList.contains("bboxLabelInput"))
    ? parseInt(ae.dataset.idx, 10) : -1;
  const focusedSelStart = (focusedIdx >= 0) ? ae.selectionStart : 0;
  const focusedSelEnd   = (focusedIdx >= 0) ? ae.selectionEnd   : 0;

  list.innerHTML = "";

  if (board.characterBboxes.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = board.imageEl
      ? "no characters yet \u2014 position the rect, then Add Character"
      : "load a reference image first";
    list.appendChild(e);
    return;
  }

  for (let i = 0; i < board.characterBboxes.length; i++) {
    const bbox = board.characterBboxes[i];
    const hasEmbed = board.referenceEmbeddings[i] !== null;
    const isViewing = board.viewingCharacterIdx === i;

    const row = document.createElement("div");
    row.className = "bboxRow" + (isViewing ? " selected" : "");

    // Thumbnail
    const thumb = document.createElement("img");
    thumb.className = "bboxThumb";
    thumb.alt = "";
    thumb.src = bbox.crop_b64
      ? ("data:image/png;base64," + bbox.crop_b64)
      : "";
    row.appendChild(thumb);

    // Index
    const lbl = document.createElement("span");
    lbl.className = "bboxLabel";
    lbl.textContent = "#" + (i + 1);
    row.appendChild(lbl);

    // Editable label
    const input = document.createElement("input");
    input.type = "text";
    input.className = "bboxLabelInput";
    input.placeholder = "unlabeled";
    input.value = bbox.label || "";
    input.dataset.idx = String(i);
    input.addEventListener("input", () => {
      setCharacterLabel(i, input.value);
      _refreshStatusLine();
    });
    input.addEventListener("click", (e) => e.stopPropagation());
    input.addEventListener("mousedown", (e) => e.stopPropagation());
    row.appendChild(input);

    // Embedding indicator
    const emb = document.createElement("span");
    emb.className = "bboxEmbed " + (hasEmbed ? "yes" : "no");
    emb.textContent = hasEmbed ? "768d" : "—";
    row.appendChild(emb);

    // Delete
    const del = document.createElement("button");
    del.className = "bboxDel";
    del.textContent = "\u00d7";
    del.title = "Remove this character";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteCharacterBbox(i);
      _renderBboxList();
      syncPanelFields();
      _scheduleStateSync();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    });
    row.appendChild(del);

    // Row click → view that character's crop
    row.addEventListener("click", () => {
      setViewingCharacter(i);
      syncPanelFields();
      _renderBboxList();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    });

    list.appendChild(row);
  }

  // Restore focus + cursor position after rebuild
  if (focusedIdx >= 0 && focusedIdx < list.children.length) {
    const newInput = list.children[focusedIdx]
      ?.querySelector?.(".bboxLabelInput");
    if (newInput) {
      newInput.focus();
      try {
        newInput.setSelectionRange(focusedSelStart, focusedSelEnd);
      } catch (_) { /* ignore */ }
    }
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
  } else if (board.viewingCharacterIdx >= 0
             && board.characterBboxes[board.viewingCharacterIdx]) {
    const label = board.characterBboxes[board.viewingCharacterIdx].label
      || "unlabeled";
    text = "viewing character " + (board.viewingCharacterIdx + 1)
         + " \u00b7 " + label;
    cls = "ok";
  } else if (total === 0) {
    text = "position the scratch rect, then Add Character";
    cls = "warn";
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
    imageInfo:    document.getElementById("imageInfo"),
    addChar:      document.getElementById("addCharacterBtn"),
    backToCanvas: document.getElementById("backToCanvasBtn"),
    backRow:      document.getElementById("backRow"),
    clear:        document.getElementById("clearBboxesBtn"),
    saveState:    document.getElementById("saveStateBtn"),
    loadState:    document.getElementById("loadStateBtn"),
  };

  if (e.imageInfo) {
    const text = board.imageEl
      ? board.imageWidth + " \u00d7 " + board.imageHeight
        + "   [" + String(board.imageHash).slice(0, 8) + "\u2026]"
      : "No image loaded";
    if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
  }

  const hasImage = !!board.imageEl;
  const busy = !!board.analyzing;
  const hasAnything = !!board.imageHash
                    || board.characterBboxes.length > 0;

  if (e.addChar)  e.addChar.disabled = !hasImage || busy;
  if (e.clear)    e.clear.disabled   = !hasImage
                                     || board.characterBboxes.length === 0
                                     || busy;
  if (e.saveState) e.saveState.disabled = !hasAnything || busy;
  if (e.loadState) e.loadState.disabled = busy;
  if (e.backRow)  e.backRow.style.display =
    board.viewingCharacterIdx >= 0 ? "flex" : "none";

  _refreshStatusLine();
}

/* ============================================================
   Image actions
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
      _lastListSig = "";
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
  _lastListSig = "";
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
  _lastListSig = "";
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

/* ============================================================
   Add Character
   ============================================================ */

async function _addCharacterAction() {
  if (!board.imageHash || !board.imageEl) {
    flashStatus("load a reference image first", "warn");
    return;
  }
  if (board.viewingCharacterIdx >= 0) {
    setViewingCharacter(-1);
  }

  const s = board.scratchRect;
  if (!s || s.w <= 0 || s.h <= 0) {
    flashStatus("drag the scratch rect first", "warn");
    return;
  }

  const bboxes_px = [[
    Math.floor(s.x * board.imageWidth),
    Math.floor(s.y * board.imageHeight),
    Math.floor((s.x + s.w) * board.imageWidth),
    Math.floor((s.y + s.h) * board.imageHeight),
  ]];
  const image_b64 = (board.imageDataUrl || "")
    .replace(/^data:image\/png;base64,/, "");

  board.analyzing = true;
  board.analyzeError = null;
  syncPanelFields();
  flashStatus("analyzing\u2026", "");

  let res;
  try {
    res = await analyzeCharacters({ image_b64, bboxes_px });
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
  if (!Array.isArray(embs) || embs.length !== 1) {
    board.analyzeError = "server returned wrong number of embeddings";
    flashStatus("analyze failed: " + board.analyzeError, "bad");
    syncPanelFields();
    return;
  }

  const crop_b64 = _cropFromImage(board.imageEl, s);

  const newIdx = addCharacterBbox({
    x: s.x, y: s.y, w: s.w, h: s.h,
    label:    "",
    crop_b64: crop_b64,
  });
  setReferenceEmbedding(newIdx, embs[0]);

  board.analyzeError = null;
  flashStatus("added character " + (newIdx + 1), "ok");

  _lastListSig = "";
  _scheduleStateSync();
  _renderBboxList();
  syncPanelFields();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Back to canvas
   ============================================================ */

function _backToCanvasAction() {
  setViewingCharacter(-1);
  syncPanelFields();
  _renderBboxList();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Clear all
   ============================================================ */

function _clearAllAction() {
  clearAllBboxes();
  _lastListSig = "";
  _renderBboxList();
  syncPanelFields();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Save state
   ============================================================
   Writes a single self-contained JSON containing the source image
   (as data URL), the scratch rect, and every character with its
   frozen bbox, editable label, crop PNG (base64), and 768-d
   reference embedding.  Uses a Blob for the download so large
   payloads (multi-MB image + crops + embeddings) do not hit the
   data:-URL size limits some browsers impose.

   Format is versioned (`format: "magi-state-v1"`) so future loaders
   can reject old files cleanly. */

function _saveStateAction() {
  if (!board.imageHash && board.characterBboxes.length === 0) {
    flashStatus("nothing to save", "warn");
    return;
  }

  const characters = board.characterBboxes.map((b, i) => ({
    x: b.x, y: b.y, w: b.w, h: b.h,
    label: b.label || "",
    crop_b64: b.crop_b64 || null,
    reference_embedding: board.referenceEmbeddings[i] || null,
  }));

  const state = {
    format: "magi-state-v1",
    saved_at: new Date().toISOString(),
    image_hash: board.imageHash,
    image_dims: [board.imageWidth, board.imageHeight],
    image_data_url: board.imageDataUrl || null,
    scratch_rect: { ...board.scratchRect },
    characters: characters,
  };

  const text = JSON.stringify(state, null, 2);
  const blob = new Blob([text], { type: "application/json" });
  const url  = URL.createObjectURL(blob);
  const stamp = new Date().toISOString()
                .replace(/[:.]/g, "-").slice(0, 19);
  const a = document.createElement("a");
  a.href = url;
  a.download = "magi_state_" + stamp + ".json";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  flashStatus(
    "saved " + characters.length + " character(s)",
    "ok",
  );
}

/* ============================================================
   Load state
   ============================================================
   Replaces the current characters with the loaded ones, and
   replaces the image too if the state has one and its hash does
   not match the currently-loaded image (avoids the reload flash
   when loading a state that was saved against the same image).

   Restores: image, characters (bboxes + labels + crops +
   embeddings), scratch rect.  Everything is then re-pushed to
   `/state` so `DrawatoonSetIP` sees it immediately.

   Case handling:
     · state has image + hash matches current  → keep image, reload chars
     · state has image + hash differs          → reload image, then chars
     · state has no image                      → keep current image (or
                                                 none) and warn if there
                                                 isn't one to display. */

function _loadStateAction() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "application/json,.json";
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(String(reader.result || ""));
        if (data.format !== "magi-state-v1") {
          flashStatus(
            "unrecognized state file (expected magi-state-v1)",
            "bad",
          );
          return;
        }

        const characters = Array.isArray(data.characters)
          ? data.characters : [];
        const hasImage = !!data.image_data_url
                       && Array.isArray(data.image_dims)
                       && data.image_dims.length === 2;
        const dims = data.image_dims || [0, 0];

        // ---- Image ----
        // setImage clears characters + scratch_rect; clearAllBboxes
        // does the same when we're keeping the current image.
        if (hasImage && data.image_hash !== board.imageHash) {
          setImage(
            data.image_hash || "loaded",
            data.image_data_url,
            dims[0], dims[1],
          );
        } else {
          clearAllBboxes();
        }

        // ---- Characters ----
        for (const c of characters) {
          const idx = addCharacterBbox({
            x: Number(c.x) || 0,
            y: Number(c.y) || 0,
            w: Number(c.w) || 0,
            h: Number(c.h) || 0,
            label: String(c.label || ""),
            crop_b64: c.crop_b64 || null,
          });
          const emb = c.reference_embedding;
          if (Array.isArray(emb) && emb.length === 768) {
            setReferenceEmbedding(idx, emb);
          }
        }

        // ---- Scratch rect ----
        if (data.scratch_rect && typeof data.scratch_rect === "object") {
          setScratchRect(data.scratch_rect);
        }

        // ---- Flash summary ----
        let warn = null;
        if (!hasImage && !board.imageHash) {
          warn = "no image \u2014 load one to view bboxes on canvas";
        } else if (hasImage && data.image_hash !== board.imageHash
                   && !board.imageHash) {
          // State has image, we attempted load; imageHash was set
          // synchronously in setImage so this branch is unreachable
          // in practice — kept for clarity if setImage's contract
          // ever changes.
          warn = "image loading\u2026";
        }
        flashStatus(
          "loaded " + characters.length + " character(s)"
          + (warn ? " \u2014 " + warn : ""),
          warn ? "warn" : "ok",
        );

        _lastListSig = "";
        _renderBboxList();
        syncPanelFields();
        _scheduleStateSync();
        import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
      } catch (e) {
        flashStatus("load failed: " + e.message, "bad");
      }
    };
    reader.readAsText(file);
  });
  input.click();
}

/* ============================================================
   Krita status
   ============================================================ */

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
   Drop overlay
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

/* ============================================================
   Install
   ============================================================ */

export function installPanelBindings() {
  const ids = {
    pickImage:   "pickImageBtn",
    clearImage:  "clearImageBtn",
    importImg:   "importImageBtn",
    refresh:     "refreshCanvasBtn",
    addChar:     "addCharacterBtn",
    backToCanvas:"backToCanvasBtn",
    clearAll:    "clearBboxesBtn",
    saveState:   "saveStateBtn",
    loadState:   "loadStateBtn",
    kritaCheck:  "kritaCheckBtn",
  };
  const el = {};
  for (const k in ids) el[k] = document.getElementById(ids[k]);

  if (el.pickImage)    el.pickImage.addEventListener("click", _pickImageAction);
  if (el.clearImage)   el.clearImage.addEventListener("click", _clearImageAction);
  if (el.importImg)    el.importImg.addEventListener("click", _importImageFromKritaAction);
  if (el.refresh)      el.refresh.addEventListener("click", _refreshCanvasAction);
  if (el.addChar)      el.addChar.addEventListener("click", _addCharacterAction);
  if (el.backToCanvas) el.backToCanvas.addEventListener("click", _backToCanvasAction);
  if (el.clearAll)     el.clearAll.addEventListener("click", _clearAllAction);
  if (el.saveState)    el.saveState.addEventListener("click", _saveStateAction);
  if (el.loadState)    el.loadState.addEventListener("click", _loadStateAction);
  if (el.kritaCheck)   el.kritaCheck.addEventListener("click", _kritaStatusAction);

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
        _lastListSig = "";
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