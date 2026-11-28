"""Panel: preprocess, list, apply nodes, state sync, Krita round-trip.

State sync goes through `makeStateSync` from pgruntime.  The factory
owns the debounce, the success-only commit of the dedupe key, and
the warn-once flash — the same discipline controlnets has always
had, now centralised.

Panel fields go through `syncImageInfo` from pgruntime (equality-
guarded internally, so `_lastImageSig` is gone).

Krita status check goes through `installKritaStatusCheck` from
pgruntime.  The factory installs the button listener and returns
the check function for programmatic use (from `controlnetsBootSync`
and from any future re-check trigger).
"""

PANEL_JS = r"""
import { board } from "./pg_state.js";
import {
  fetchActiveState,
  pushControlnets, refreshControlnets,
} from "./pg_client.js";
import { fetchSelectedLayer } from "./pg_autosync.js";
import {
  makeKritaClient, installKritaStatusCheck,
} from "./pgruntime/js/daemon_client.js";
import { makeStateSync } from "./pgruntime/js/state_sync.js";
import { syncImageInfo } from "./pgruntime/js/panel_fields.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { SYNC_DEBOUNCE_MS } from "./pg_tuning.js";

const PLAYGROUND_ID       = "controlnets";
const SLOT_ID             = "@thecontrolnet";
const CONTROLNETS_DOC_NAME = "kritomatic_controlnets";

let _pushInProgress    = false;
let _refreshInProgress = false;

/* ============================================================
   Krita status check
   ============================================================ */

const _kritaStatusClient = makeKritaClient();
const _kritaCheck = installKritaStatusCheck({ client: _kritaStatusClient });

/* ============================================================
   State sync
   ============================================================ */

function _stateKey() {
  const parts = [
    board.imageHash || "",
    board.imageWidth + "x" + board.imageHeight,
  ];
  for (const c of board.controlnets) {
    parts.push(c.title + ":" + (c.hash || "err"));
  }
  for (const n of board.applyNodes) {
    parts.push(n.title + "=" + (n.enabled !== false ? "1" : "0"));
  }
  return parts.join("|");
}

function _buildStatePayload() {
  const wireControlnets = board.controlnets
    .filter(c => c.status === "done")
    .map(c => ({
      id:    c.title,
      name:  c.title,
      image: { hash: c.hash, width: c.width, height: c.height },
    }));

  const applyEnabled = {};
  for (const n of board.applyNodes) {
    if (n && typeof n.title === "string" && n.title) {
      applyEnabled[n.title] = n.enabled !== false;
    }
  }

  const nextState = {
    image_hash:  board.imageHash || "",
    image_dims:  [board.imageWidth, board.imageHeight],
    controlnets: wireControlnets,
  };
  if (Object.keys(applyEnabled).length > 0) {
    nextState.apply_enabled = applyEnabled;
  }

  return { op: "set", state: nextState };
}

const _stateSync = makeStateSync({
  buildPayload: _buildStatePayload,
  keyFn:        _stateKey,
  url:          "/state",
  debounceMs:   SYNC_DEBOUNCE_MS,
  onSuccess: (data) => {
    board.lastSync  = Date.now();
    board.syncError = null;
    const v = (data.version || "").slice(0, 8);
    const el = document.getElementById("stateVersion");
    if (el) el.textContent = v || "\u2014";
    _refreshStatusLine();
    pgkitNotifyAll();
  },
  onError: (msg) => {
    board.syncError = msg;
    flashStatus("state sync failed: " + msg, "bad");
    _refreshStatusLine();
    pgkitNotifyAll();
  },
});

function _scheduleStateSync() {
  _stateSync.schedule();
}

/* ============================================================
   Status line
   ============================================================ */

function _refreshStatusLine() {
  const el = document.getElementById("statusLine");
  if (!el) return;

  let text, cls = "";
  if (board.preprocessRunning) {
    text = "preprocessing\u2026"; cls = "ok";
  } else if (_pushInProgress) {
    text = "pushing to Krita\u2026"; cls = "ok";
  } else if (_refreshInProgress) {
    text = "refreshing from Krita\u2026"; cls = "ok";
  } else if (board.syncError) {
    text = "state sync failed: " + board.syncError; cls = "bad";
  } else if (!board.imageHash) {
    text = "no image loaded"; cls = "";
  } else if (board.lastSync) {
    const t = new Date(board.lastSync).toTimeString().slice(0, 8);
    const total    = board.controlnets.length;
    const nApply   = board.applyNodes.length;
    const nApplyOn = board.applyNodes.filter(n => n.enabled !== false).length;
    text = "synced " + t
         + " \u00b7 " + board.imageWidth + "\u00d7" + board.imageHeight
         + " \u00b7 " + total + " preprocessor(s)"
         + " \u00b7 " + nApplyOn + "/" + nApply + " apply";
    cls = "ok";
  } else {
    text = "not yet synced"; cls = "warn";
  }

  if (el.textContent !== text) el.textContent = text;
  el.className = cls;
}

/* ============================================================
   Controlnet (preprocessor) list — no enable toggles here
   ============================================================ */

function _renderControlnetList() {
  const list = document.getElementById("controlnetList");
  if (!list) return;
  list.innerHTML = "";

  if (board.controlnets.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = board.imageEl
      ? "click Preprocess images"
      : "load @thecontrolnet first";
    list.appendChild(empty);
    return;
  }

  for (const c of board.controlnets) {
    const row = document.createElement("div");
    row.className = "controlnetRow"
      + (board.viewingControlnetTag === c.title ? " selected" : "");
    if (c.status === "done") row.dataset.clickable = "1";

    const title = document.createElement("span");
    title.className = "controlnetTitle";
    title.textContent = c.title;
    title.title = c.status === "error" ? c.error : c.title;
    row.appendChild(title);

    const status = document.createElement("span");
    status.className = "controlnetStatus " + (c.status || "pending");
    status.textContent =
        c.status === "done"    ? "ready"
      : c.status === "error"   ? "error"
      : c.status === "running" ? "running"
      : "pending";
    row.appendChild(status);

    if (c.status === "done") {
      row.addEventListener("click", () => {
        board.viewingControlnetTag = c.title;
        _renderControlnetList();
        syncPanelFields();
        import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
      });
    }

    list.appendChild(row);
  }
}

/* ============================================================
   Apply-node list — one row per ControlNetApplyAdvanced, with
   an independent enable checkbox
   ============================================================ */

function _renderApplyNodeList() {
  const list = document.getElementById("applyNodeList");
  if (!list) return;
  list.innerHTML = "";

  if (board.applyNodes.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "no Apply nodes in workflow file";
    list.appendChild(empty);
    return;
  }

  for (const n of board.applyNodes) {
    const enabled = n.enabled !== false;
    const row = document.createElement("div");
    row.className = "applyNodeRow" + (!enabled ? " disabled" : "");

    const cb = document.createElement("input");
    cb.type    = "checkbox";
    cb.checked = enabled;
    cb.title   = "Include " + n.title + " in generation. Unchecked "
               + "Apply nodes are omitted from apply_enabled, so "
               + "ControlNetsConditioning skips their chain step.";
    cb.addEventListener("change", () => {
      n.enabled = !!cb.checked;
      _renderApplyNodeList();
      _refreshStatusLine();
      _scheduleStateSync();
    });
    row.appendChild(cb);

    const title = document.createElement("span");
    title.className = "applyNodeTitle";
    title.textContent = n.title || "(untitled)";
    row.appendChild(title);

    if (n.controlnet_name) {
      const cn = document.createElement("span");
      cn.className = "applyNodeCn";
      cn.textContent = String(n.controlnet_name).split("/").pop();
      cn.title = n.controlnet_name;
      row.appendChild(cn);
    }

    list.appendChild(row);
  }
}

/* ============================================================
   Panel field sync
   ============================================================ */

export function syncPanelFields() {
  const imageInfo = document.getElementById("imageInfo");
  if (imageInfo) {
    syncImageInfo(imageInfo, board, { hint: "No image loaded" });
  }

  const backRow = document.getElementById("backToControlnetRow");
  if (backRow) {
    backRow.style.display =
      board.viewingControlnetTag ? "flex" : "none";
  }

  const preBtn = document.getElementById("preprocessBtn");
  if (preBtn) {
    preBtn.disabled = !board.imageEl || board.preprocessRunning;
    preBtn.textContent = board.preprocessRunning
      ? "Preprocessing\u2026"
      : "Preprocess images";
  }

  const hasImage  = !!board.imageEl;
  const anyDone   = board.controlnets.some(c => c.status === "done" && c.dataUrl);
  const anyLoaded = board.controlnets.some(c => c.status === "done");

  const pushBtn = document.getElementById("pushToKritaBtn");
  if (pushBtn) {
    pushBtn.disabled = !hasImage || !anyDone || _pushInProgress;
    pushBtn.textContent = _pushInProgress
      ? "\u2191 Pushing\u2026"
      : "\u2191 Push to Krita";
  }
  const refreshBtn = document.getElementById("refreshFromKritaBtn");
  if (refreshBtn) {
    refreshBtn.disabled = !anyLoaded || _refreshInProgress;
    refreshBtn.textContent = _refreshInProgress
      ? "\u2193 Refreshing\u2026"
      : "\u2193 Refresh from Krita";
  }

  // Safety net: catches state mutations that happened outside an
  // explicit mutation handler (e.g., autosync updating the image).
  // The factory dedupes by key and by pending timer, so calling it
  // every frame is cheap: on a steady-state frame the key matches
  // the last synced key and the timer check short-circuits.
  _scheduleStateSync();

  _refreshStatusLine();
}

/* ============================================================
   Refresh @thecontrolnet
   ============================================================ */

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
  syncPanelFields();
}

/* ============================================================
   Preprocess
   ============================================================ */

async function _preprocessAction() {
  if (!board.imageEl || !board.imageDataUrl) {
    flashStatus("load @thecontrolnet first", "warn");
    return;
  }

  board.preprocessRunning    = true;
  board.controlnets          = [];
  board.viewingControlnetTag = null;
  _renderControlnetList();
  syncPanelFields();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});

  const b64 = String(board.imageDataUrl)
    .replace(/^data:image\/png;base64,/, "");

  let data;
  try {
    const resp = await fetch("/preprocess", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_b64: b64 }),
    });
    data = await resp.json();
  } catch (e) {
    board.preprocessRunning = false;
    flashStatus("preprocess failed: " + e.message, "bad");
    syncPanelFields();
    return;
  }

  board.preprocessRunning = false;

  if (!data || !data.success) {
    flashStatus(
      "preprocess failed: " + ((data && data.message) || "unknown"),
      "bad",
    );
    syncPanelFields();
    return;
  }

  for (const item of (data.results || [])) {
    if (item.error) {
      board.controlnets.push({
        title: item.title, status: "error",
        hash: null, dataUrl: null,
        width: 0, height: 0, el: null,
        error: item.error,
      });
      continue;
    }
    const entry = {
      title:   item.title,
      status:  "done",
      hash:    item.hash,
      dataUrl: item.dataUrl,
      width:   item.width,
      height:  item.height,
      el:      null,
      error:   null,
    };
    board.controlnets.push(entry);

    const img = new Image();
    img.onload = () => {
      const e = board.controlnets.find(c => c.title === item.title);
      if (e) { e.el = img; }
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    };
    img.onerror = () => {};
    img.src = item.dataUrl;
  }

  const n = board.controlnets.filter(c => c.status === "done").length;
  const errs = board.controlnets.length - n;
  flashStatus(
    "preprocessed " + n + " image(s)"
      + (errs ? " (" + errs + " error(s))" : ""),
    errs ? "warn" : "ok",
  );

  _renderControlnetList();
  syncPanelFields();
  _scheduleStateSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Push to Krita
   ============================================================ */

async function _pushToKritaAction() {
  if (_pushInProgress) return;
  if (!board.imageEl) {
    flashStatus("load @thecontrolnet first", "warn");
    return;
  }
  const done = board.controlnets.filter(
    c => c.status === "done" && c.dataUrl
  );
  if (done.length === 0) {
    flashStatus("preprocess first", "warn");
    return;
  }

  _pushInProgress = true;
  syncPanelFields();
  flashStatus("pushing to Krita\u2026", "");

  const payload = {
    doc_name:   CONTROLNETS_DOC_NAME,
    doc_width:  board.imageWidth,
    doc_height: board.imageHeight,
    controlnets: done.map(c => ({
      title:   c.title,
      png_b64: String(c.dataUrl).replace(/^data:image\/png;base64,/, ""),
    })),
  };

  try {
    const data = await pushControlnets(payload);
    if (data.success) {
      flashStatus(
        "pushed " + (data.pushed || []).length + " layer(s) to "
          + (data.doc_name || CONTROLNETS_DOC_NAME),
        "ok",
      );
    } else {
      const failed = (data.failed || []);
      const detail = failed.length
        ? " (" + failed.join("; ") + ")"
        : "";
      flashStatus(
        "push failed: " + (data.message || "unknown") + detail,
        "bad",
      );
    }
  } catch (e) {
    flashStatus("push failed: " + e.message, "bad");
  } finally {
    _pushInProgress = false;
    syncPanelFields();
  }
}

/* ============================================================
   Refresh from Krita
   ============================================================ */

async function _refreshFromKritaAction() {
  if (_refreshInProgress) return;
  const done = board.controlnets.filter(c => c.status === "done");
  if (done.length === 0) {
    flashStatus("preprocess first", "warn");
    return;
  }

  _refreshInProgress = true;
  syncPanelFields();
  flashStatus("refreshing from Krita\u2026", "");

  const payload = {
    doc_name: CONTROLNETS_DOC_NAME,
    titles:   done.map(c => c.title),
  };

  try {
    const data = await refreshControlnets(payload);
    if (!data.success) {
      flashStatus(
        "refresh failed: " + (data.message || "unknown"),
        "bad",
      );
      return;
    }

    let n = 0, nErr = 0;
    for (const item of (data.results || [])) {
      if (item.error) {
        nErr++;
        console.warn("[refresh] " + item.title + ": " + item.error);
        continue;
      }
      const idx = board.controlnets.findIndex(c => c.title === item.title);
      if (idx < 0) continue;
      const entry = board.controlnets[idx];
      entry.hash    = item.hash;
      entry.dataUrl = item.dataUrl;
      entry.width   = item.width;
      entry.height  = item.height;
      entry.el      = null;
      const img = new Image();
      img.onload = () => {
        const e = board.controlnets.find(c => c.title === item.title);
        if (e) e.el = img;
        import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
      };
      img.onerror = () => {};
      img.src = item.dataUrl;
      n++;
    }

    flashStatus(
      "refreshed " + n + " layer(s)"
        + (nErr ? " (" + nErr + " error)" : ""),
      nErr ? "warn" : "ok",
    );

    _renderControlnetList();
    syncPanelFields();
    _scheduleStateSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  } catch (e) {
    flashStatus("refresh failed: " + e.message, "bad");
  } finally {
    _refreshInProgress = false;
    syncPanelFields();
  }
}

/* ============================================================
   Back to @thecontrolnet
   ============================================================ */

function _backToControlnetAction() {
  board.viewingControlnetTag = null;
  _renderControlnetList();
  syncPanelFields();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Install
   ============================================================ */

export function installPanelBindings() {
  const refresh     = document.getElementById("refreshCanvasBtn");
  const preprocess  = document.getElementById("preprocessBtn");
  const pushBtn     = document.getElementById("pushToKritaBtn");
  const refreshKBtn = document.getElementById("refreshFromKritaBtn");
  const back        = document.getElementById("backToControlnetBtn");

  if (refresh)     refresh.addEventListener("click", _refreshCanvasAction);
  if (preprocess)  preprocess.addEventListener("click", _preprocessAction);
  if (pushBtn)     pushBtn.addEventListener("click", _pushToKritaAction);
  if (refreshKBtn) refreshKBtn.addEventListener("click", _refreshFromKritaAction);
  if (back)        back.addEventListener("click", _backToControlnetAction);

  // Krita status check button is wired by installKritaStatusCheck().
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
      if (el) el.textContent = v || "\u2014";

      if (Array.isArray(data.apply_nodes)) {
        board.applyNodes = data.apply_nodes.map(n => ({
          title:              n.title || "",
          preprocessor_title: n.preprocessor_title || "",
          controlnet_name:    n.controlnet_name || "",
          enabled:            n.enabled !== false,
        }));
      }

      _renderApplyNodeList();
    }
  } catch (_) {}
  syncPanelFields();
}

export function controlnetsBootSync() {
  _renderControlnetList();
  _renderApplyNodeList();
  syncPanelFields();
  _kritaCheck();
  _bootPull().catch(() => {});
}

export function onDrawComplete() {
  syncPanelFields();
}
"""