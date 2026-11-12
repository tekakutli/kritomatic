"""Panel button handlers, pgkit registrations, state sync.

The state-sync path: any change to areas or globals calls
`_scheduleStateSync()`, which debounces and POSTs the full state +
mask b64s to `/state`.  The node fetches `/state` on every execute;
`/version` is cheap for IS_CHANGED.

`Export graph JSON` downloads the API-format graph the node would
build from the current state (works with ComfyUI closed).

`Fetch last run` asks the server to GET ComfyUI's native `/history`
for the most recent run.  The returned JSON is the *submitted*
graph, not the expanded one; the diagnostic summary explains this.

The displayed image is whatever the user picked or imported — no
auto-refresh after generation.  Use `+ From Krita` to pull the
current composite into the canvas."""

PANEL_BINDINGS_JS = r"""
import {
  board, setImage, clearImage,
} from "./pg_state.js";
import {
  addAreaFromB64, removeArea, selectArea, areaById,
} from "./pg_areas.js";
import { syncAreaList, setStateChangeHook } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import {
  importFromKrita, importKritaMasks, uploadImageFile,
  checkKritaStatus,
} from "./pg_client.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitRegisterField,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ============================================================
   State sync (debounced POST to /state)
   ============================================================ */

let _syncTimer = null;

function _maskHash(mask) {
  if (mask._hash) return mask._hash;
  let h = 2166136261 >>> 0;
  const s = mask.b64 || "";
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  mask._hash = s.length.toString(16) + "-" + h.toString(16);
  return mask._hash;
}

function _buildStatePayload() {
  const areas = board.areas.map(a => ({
    id: a.id, name: a.name,
    prompt: a.prompt, use_tagger: !!a.useTagger,
    strength:              a.strength,
    set_cond_area:         a.set_cond_area,
    mask_channel:          a.mask_channel,
    bbox:                  a.bbox,
    resize:                a.resize,
    tagger_mode:           a.tagger_mode,
    tagger_model:          a.tagger_model,
    tagger_threshold:      a.tagger_threshold,
    tagger_char_threshold: a.tagger_char_threshold,
    mask: {
      hash:   _maskHash(a.mask),
      width:  a.mask.w,
      height: a.mask.h,
    },
  }));
  const masks = {};
  for (const a of board.areas) {
    if (a.mask && a.mask.b64) masks[_maskHash(a.mask)] = a.mask.b64;
  }
  return {
    op: "set",
    state: { globals: { ...board.globals }, areas },
    masks,
  };
}

export function _scheduleStateSync() {
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_doStateSync, 250);
}

async function _doStateSync() {
  _syncTimer = null;
  try {
    const res = await fetch("/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(_buildStatePayload()),
    });
    const payload = await res.json();
    if (payload.success) {
      const v = (payload.version || "").slice(0, 8);
      const el = document.getElementById("stateVersion");
      if (el) el.textContent = v || "—";
      const st = document.getElementById("statusLine");
      if (st) {
        st.textContent = "synced \u00b7 v" + v;
        st.className = "ok";
      }
    }
  } catch (e) {
    const st = document.getElementById("statusLine");
    if (st) {
      st.textContent = "sync failed: " + e.message;
      st.className = "bad";
    }
  }
}

setStateChangeHook(_scheduleStateSync);

/* ============================================================
   Image actions
   ============================================================ */

function _fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("could not read file"));
    reader.readAsDataURL(file);
  });
}

async function _installImage(file) {
  flashStatus("uploading " + file.name + "\u2026", "");
  try {
    const info = await uploadImageFile(file);
    const dataUrl = await _fileToDataUrl(file);
    setImage(info.hash, dataUrl, info.width, info.height);
    flashStatus("image loaded", "ok");
    syncPanelFields();
    syncAreaList();
    _scheduleStateSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  } catch (e) {
    flashStatus("upload failed: " + e.message, "bad");
  }
}

function _pickImageAction() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (file) _installImage(file);
  });
  input.click();
}

function _clearImageAction() {
  clearImage();
  syncPanelFields();
  syncAreaList();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _importImageFromKritaAction() {
  flashStatus("importing image from Krita\u2026", "");
  try {
    const res = await importFromKrita({});
    if (!res.success) {
      flashStatus("import failed: " + (res.message || "unknown"), "bad");
      return;
    }
    setImage(res.hash, res.data_url, res.width, res.height);
    flashStatus("imported " + res.width + "\u00d7" + res.height, "ok");
    syncPanelFields();
    syncAreaList();
    _scheduleStateSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  } catch (e) {
    flashStatus("import failed: " + e.message, "bad");
  }
}

/* ============================================================
   Area actions
   ============================================================ */

function _slugName(s) {
  const slug = String(s || "").replace(/[^A-Za-z0-9_]/g, "_").slice(0, 40);
  return slug || undefined;
}

async function _importMasksFromKritaAction() {
  if (!board.imageHash) {
    flashStatus("import an image from Krita first", "warn");
    return;
  }
  flashStatus("importing masks from Krita\u2026", "");
  try {
    const res = await importKritaMasks({});
    if (!res.success) {
      flashStatus("import failed: " + (res.message || "unknown"), "bad");
      return;
    }
    const masks = res.masks || [];
    if (masks.length === 0) {
      const skipped = res.skipped || [];
      const why = skipped.length
        ? " (" + skipped.map(s => s.name + ": " + s.reason).join("; ") + ")"
        : "";
      flashStatus("no masks imported" + why, "warn");
      return;
    }
    for (const m of masks) {
      addAreaFromB64(m.mask_b64, {
        name: _slugName(m.name),
        prompt: "",
      });
    }
    syncPanelFields();
    syncAreaList();
    _scheduleStateSync();
    flashStatus("imported " + masks.length + " area(s)", "ok");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  } catch (e) {
    flashStatus("import failed: " + e.message, "bad");
  }
}

function _addBlankAreaAction() {
  if (!board.imageWidth || !board.imageHeight) {
    flashStatus("load an image first", "warn");
    return;
  }
  const W = board.imageWidth, H = board.imageHeight;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  cx.fillStyle = "#fff";
  cx.fillRect(0, 0, W, H);
  const b64 = c.toDataURL("image/png")
               .replace(/^data:image\/png;base64,/, "");
  addAreaFromB64(b64, { prompt: "" });
  syncPanelFields();
  syncAreaList();
  _scheduleStateSync();
  flashStatus("added blank area", "ok");
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _deleteAreaAction(payload) {
  const n = payload?.target?.name;
  if (typeof n !== "string" || !n.startsWith("area.")) return;
  const id = n.slice(5);
  if (!areaById(id)) return;
  removeArea(id);
  syncAreaList();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
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
    el.title = "Kritomatic daemon at localhost:12346";
  } else {
    el.textContent = "\u2717 not reachable";
    el.className = "bad";
    el.title = "Start Krita with the Kritomatic Daemon plugin enabled";
  }
}

/* ============================================================
   Save / Load
   ============================================================ */

function _saveStateAction() {
  const payload = _buildStatePayload();
  const out = {
    globals: payload.state.globals,
    areas: payload.state.areas.map(a => {
      const area = areaById(a.id);
      return { ...a, mask_b64: area.mask.b64 };
    }),
  };
  const text = JSON.stringify(out, null, 2);
  const url = "data:application/json;charset=utf-8," + encodeURIComponent(text);
  const a = document.createElement("a");
  a.href = url;
  a.download = "multimask_state.json";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  flashStatus("saved state", "ok");
}

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
        if (data.globals) Object.assign(board.globals, data.globals);
        if (Array.isArray(data.areas)) {
          board.areas = [];
          board.selectedAreaId = null;
          board.hoveredAreaId = null;
          board.nextAreaNum = 1;
          for (const a of data.areas) {
            if (!a.mask_b64) continue;
            addAreaFromB64(a.mask_b64, {
              name: a.name, prompt: a.prompt,
              useTagger: a.use_tagger,
              strength: a.strength,
              set_cond_area: a.set_cond_area,
              mask_channel: a.mask_channel,
              bbox: a.bbox, resize: a.resize,
              tagger_mode: a.tagger_mode,
              tagger_model: a.tagger_model,
              tagger_threshold: a.tagger_threshold,
              tagger_char_threshold: a.tagger_char_threshold,
            });
          }
        }
        syncPanelFields();
        syncAreaList();
        _scheduleStateSync();
        flashStatus("state loaded", "ok");
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
   Export graph JSON
   ============================================================ */

async function _exportGraphAction() {
  try {
    const res = await fetch("/export_graph", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await res.json();
    if (!payload.success) {
      flashStatus("export failed: " + (payload.message || "unknown"), "bad");
      return;
    }
    const text = JSON.stringify(payload.graph, null, 2);
    const url  = "data:application/json;charset=utf-8,"
               + encodeURIComponent(text);
    const stamp = new Date().toISOString()
                  .replace(/[:.]/g, "-").slice(0, 19);
    const a = document.createElement("a");
    a.href = url;
    a.download = "multimask_graph_" + stamp + ".json";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    const v = (payload.version || "").slice(0, 8);
    const fc = payload.final_conditioning || ["?", "?"];
    flashStatus(
      "exported " + Object.keys(payload.graph).length
      + " node(s) \u00b7 v" + v
      + " \u00b7 output " + fc[0] + "[" + fc[1] + "]",
      "ok",
    );
    if (payload.notes && payload.notes.length) {
      console.log("[multimask export]", payload.notes.join("\n"));
    }
  } catch (e) {
    flashStatus("export failed: " + e.message, "bad");
  }
}

/* ============================================================
   Fetch last run
   ============================================================ */

async function _fetchHistoryAction() {
  try {
    const res = await fetch("/fetch_history", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await res.json();
    if (!payload.success) {
      flashStatus("fetch failed: " + (payload.message || "unknown"), "bad");
      const infoEl = document.getElementById("lastRunInfo");
      if (infoEl) {
        infoEl.textContent = "fetch failed: " + (payload.message || "unknown");
        infoEl.className = "bad";
      }
      return;
    }

    const d = payload.diagnostic || {};
    const n  = d.submitted_node_count || 0;
    const clips = d.cliptexts || [];
    const hasMA = d.multi_area_node ? "present" : "absent";
    const stat  = d.status_str || "?";

    const out = {
      prompt_id:   payload.prompt_id,
      fetched_at:  new Date().toISOString(),
      comfy_url:   payload.comfy_url,
      diagnostic:  d,
      status:      payload.status,
      prompt:      payload.prompt,
      outputs:     payload.outputs,
      meta:        payload.meta,
    };
    const text = JSON.stringify(out, null, 2);
    const url  = "data:application/json;charset=utf-8,"
               + encodeURIComponent(text);
    const a = document.createElement("a");
    a.href = url;
    a.download = "last_run_" + String(payload.prompt_id).slice(0, 8) + ".json";
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);

    flashStatus(
      "fetched \u00b7 " + n + " node(s) \u00b7 "
      + clips.length + " CLIPTextEncode \u00b7 MultiArea: " + hasMA
      + " \u00b7 " + stat,
      "ok",
    );

    const infoEl = document.getElementById("lastRunInfo");
    if (infoEl) {
      const lines = [
        "Last run " + String(payload.prompt_id).slice(0, 8)
        + " \u00b7 " + n + " submitted nodes",
        "  CLIPTextEncode: " + clips.length
        + " \u00b7 MultiAreaConditioning: " + hasMA
        + " \u00b7 status: " + stat,
        "  outputs: " + ((d.output_node_ids || []).length || 0)
        + " node(s)",
      ];
      for (const ct of clips) {
        const t = (ct.text || "").slice(0, 120);
        lines.push("    [" + ct.node + "] " + t);
      }
      if (d.multi_area_node) {
        const mi = d.multi_area_node.inputs || {};
        const url0 = mi.playground_url ?? "(unset)";
        const filt = mi.area_filter   ?? "(unset)";
        lines.push("    MultiArea inputs: url=" + url0 + "  filter=" + filt);
      }
      lines.push("  note: /history shows the SUBMITTED graph, "
                 + "not the expanded one.");
      infoEl.textContent = lines.join("\n");
      infoEl.className = stat === "error" ? "bad" : "ok";
    }

    console.log("[multimask fetch_history] diagnostic:", d);
    for (const ct of clips) {
      console.log("  CLIPTextEncode", ct.node, ":", ct.text);
    }
    if (d.multi_area_node) {
      console.log("  MultiAreaConditioning inputs:",
                  d.multi_area_node.inputs);
    }
  } catch (e) {
    flashStatus("fetch failed: " + e.message, "bad");
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

export function installPanelBindings() {
  const el = {};
  for (const id of [
    "pickImageBtn", "clearImageBtn", "importImageBtn",
    "importMasksBtn", "blankAreaBtn", "kritaCheckBtn",
    "saveStateBtn", "loadStateBtn", "exportGraphBtn",
    "fetchHistoryBtn",
  ]) {
    el[id] = document.getElementById(id);
  }
  if (el.pickImageBtn)    el.pickImageBtn.addEventListener("click", _pickImageAction);
  if (el.clearImageBtn)   el.clearImageBtn.addEventListener("click", _clearImageAction);
  if (el.importImageBtn)  el.importImageBtn.addEventListener("click", _importImageFromKritaAction);
  if (el.importMasksBtn)  el.importMasksBtn.addEventListener("click", _importMasksFromKritaAction);
  if (el.blankAreaBtn)    el.blankAreaBtn.addEventListener("click", _addBlankAreaAction);
  if (el.kritaCheckBtn)   el.kritaCheckBtn.addEventListener("click", _kritaStatusAction);
  if (el.saveStateBtn)    el.saveStateBtn.addEventListener("click", _saveStateAction);
  if (el.loadStateBtn)    el.loadStateBtn.addEventListener("click", _loadStateAction);
  if (el.exportGraphBtn)  el.exportGraphBtn.addEventListener("click", _exportGraphAction);
  if (el.fetchHistoryBtn) el.fetchHistoryBtn.addEventListener("click", _fetchHistoryAction);

  // Combine-mode select
  const cms = document.getElementById("combineModeSelect");
  if (cms) {
    cms.value = board.globals.combine_mode;
    cms.addEventListener("change", () => {
      board.globals.combine_mode = cms.value;
      _scheduleStateSync();
    });
  }

  // Default strength input
  const dsi = document.getElementById("defaultStrengthInput");
  if (dsi) {
    dsi.value = board.globals.default_strength;
    dsi.addEventListener("change", () => {
      const v = parseFloat(dsi.value);
      if (isFinite(v)) {
        board.globals.default_strength = Math.max(0, Math.min(1, v));
        dsi.value = board.globals.default_strength;
        _scheduleStateSync();
      }
    });
  }

  // Bbox crop checkbox
  const bboxCb = document.getElementById("bboxCheckbox");
  if (bboxCb) {
    bboxCb.checked = !!board.globals.bbox;
    bboxCb.addEventListener("change", () => {
      board.globals.bbox = bboxCb.checked;
      _scheduleStateSync();
    });
  }

  // Drop-overlay install + drag/drop
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
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    _dragDepth = 0;
    _showDropOverlay(false);
    const file = e.dataTransfer && e.dataTransfer.files
                 && e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) _installImage(file);
  });
}

/* ============================================================
   pgkit interfaces
   ============================================================ */

pgkitRegisterLevel("areas", {
  label: "Areas",
  items: () => board.areas.map(a => ({
    name:  "area." + a.id,
    label: a.name,
    extra: {
      prompt:     a.prompt,
      use_tagger: !!a.useTagger,
      mask:       (a.mask.w || 0) + "\u00d7" + (a.mask.h || 0),
    },
  })),
  selected: () => board.selectedAreaId
    ? "area." + board.selectedAreaId
    : null,
  parent: () => null,
});

pgkitRegisterField("globals.bbox", {
  get: () => !!board.globals.bbox,
  set: (v) => {
    board.globals.bbox = !!v;
    const cb = document.getElementById("bboxCheckbox");
    if (cb) cb.checked = board.globals.bbox;
    _scheduleStateSync();
  },
  meta: { kind: "bool", label: "Bbox crop" },
});

pgkitRegisterAction("action.pickImage", {
  label: "+ Pick image\u2026", scope: "global",
  run: _pickImageAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.importImage", {
  label: "+ From Krita (image)", scope: "global",
  run: _importImageFromKritaAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.importMasks", {
  label: "+ From Krita (masks)", scope: "global",
  run: _importMasksFromKritaAction, meta: { variant: "primary" },
});
pgkitRegisterAction("action.blankArea", {
  label: "+ Blank area", scope: "global",
  run: _addBlankAreaAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.clearImage", {
  label: "Clear image", scope: "global",
  run: _clearImageAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.kritaStatus", {
  label: "Check Krita", scope: "global",
  run: _kritaStatusAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.saveState", {
  label: "Save state", scope: "global",
  run: _saveStateAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.loadState", {
  label: "Load state", scope: "global",
  run: _loadStateAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.exportGraph", {
  label: "Export graph JSON", scope: "global",
  run: _exportGraphAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.fetchHistory", {
  label: "Fetch last run", scope: "global",
  run: _fetchHistoryAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.deleteArea", {
  label: "\u00d7", scope: "row", level: "areas",
  appliesTo: () => true, run: _deleteAreaAction,
  meta: { variant: "danger" },
});

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;
  if (level === "areas") {
    if (!name.startsWith("area.")) return false;
    const id = name.slice(5);
    if (!areaById(id)) return false;
    selectArea(id);
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return true;
  }
  return false;
}

/* ============================================================
   Boot-time state restore
   ============================================================
   `multimaskBootSync` pulls the server's saved state on page load
   and rehydrates the area list.  The image is NOT auto-fetched from
   Krita — the user picks or imports one explicitly with the panel
   buttons.  There is no image poller. */

async function _applyServerState(payload) {
  if (payload.globals && typeof payload.globals === "object") {
    Object.assign(board.globals, payload.globals);
  }
  if (!Array.isArray(payload.areas)) return;

  board.areas = [];
  board.selectedAreaId = null;
  board.hoveredAreaId  = null;
  board.nextAreaNum    = 1;

  for (const a of payload.areas) {
    const h = a.mask && a.mask.hash;
    if (!h) continue;
    let b64 = null;
    try {
      const res = await fetch("/mask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hash: h }),
      });
      const m = await res.json();
      if (m.success) b64 = m.b64;
    } catch (e) {}
    if (!b64) continue;
    addAreaFromB64(b64, {
      id: a.id, name: a.name, prompt: a.prompt,
      useTagger: a.use_tagger,
      strength:              a.strength,
      set_cond_area:         a.set_cond_area,
      mask_channel:          a.mask_channel,
      bbox:                  a.bbox,
      resize:                a.resize,
      tagger_mode:           a.tagger_mode,
      tagger_model:          a.tagger_model,
      tagger_threshold:      a.tagger_threshold,
      tagger_char_threshold: a.tagger_char_threshold,
    });
  }
}

export async function multimaskBootSync() {
  syncPanelFields();
  syncAreaList();

  // Pull server state first.  Only push if empty — the previous
  // session's areas should survive a page reload.
  let pulled = null;
  try {
    const res = await fetch("/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    pulled = await res.json();
  } catch (e) {}

  if (pulled && pulled.success
      && Array.isArray(pulled.areas) && pulled.areas.length > 0) {
    await _applyServerState(pulled);
    syncPanelFields();
    syncAreaList();
  } else {
    _scheduleStateSync();
  }

  _kritaStatusAction();
}
"""