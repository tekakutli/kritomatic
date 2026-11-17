"""Panel handlers: refresh @thecontrolnet, preprocess, list, state sync,
and the Krita round-trip (push / refresh).

Preprocess flow: click "Preprocess images" → the browser POSTs
`@thecontrolnet`'s PNG to `/preprocess` → the server reads
`data/controlnets/controlnets_workflow.json`, finds every
ControlNetApply node, runs each one's preprocessor against the
image, and returns one entry per distinct preprocessor (`{title,
hash, dataUrl, width, height}` or `{title, error}`).

The panel stores those in `board.controlnets` and renders them as
clickable rows.  Clicking a done row sets
`board.viewingControlnetTag = title`; the canvas swaps to that
image (letterboxed into the fit rect).  Clicking "Back to
@thecontrolnet" resets the tag.

ENABLED / DISABLED ROWS
=======================
Each ready row carries a checkbox.  Its `enabled` flag defaults to
true and is user-toggleable.  Only enabled rows are serialised into
`/state`'s `controlnets` array — see `_doStateSync`'s filter — so a
disabled row is completely absent from the state the
`ControlNetsConditioning` node pulls at generation time.  That is
how "uncheck DEPTH" turns into "the node skips DEPTH this run":
the playground does not need a separate channel, the state list
itself is the channel.

Disabled rows are dimmed and their title struck through; the row
remains clickable for canvas preview (so a user can compare
preprocessed variants before choosing which to include).  Toggling
a checkbox rebuilds the list and re-schedules state sync — the
`:off` marker in `_stateKey` guarantees the key moves and the sync
fires.

Krita round-trip:
  · "Push to Krita" POSTs every done controlnet to
    `/krita/push_controlnets`.  The server creates (or replaces) a
    scratch doc named `kritomatic_controlnets`, one layer per
    controlnet image, layer names == titles.
  · "Refresh from Krita" POSTs the current titles to
    `/krita/refresh_controlnets`.  The server reads each layer,
    re-encodes as PNG, updates its own cache, and returns new
    `{title, hash, dataUrl, width, height}` per layer.  The panel
    updates the entries in place and schedules a state sync so the
    workflow sees the new hashes.

State sync fires after preprocess / refresh / toggle so the node's
`/state` pull sees the current hashes.  Per-entry shape in `/state`
mirrors the stub: `{id, name, image: {hash, width, height}}`.
"""

PANEL_JS = r"""
import { board } from "./pg_state.js";
import {
  checkKritaStatus, fetchActiveState,
  pushControlnets, refreshControlnets,
} from "./pg_client.js";
import { fetchSelectedLayer } from "./pg_autosync.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { SYNC_DEBOUNCE_MS } from "./pg_tuning.js";

const PLAYGROUND_ID       = "controlnets";
const SLOT_ID             = "@thecontrolnet";
const CONTROLNETS_DOC_NAME = "kritomatic_controlnets";

let _pushInProgress    = false;
let _refreshInProgress = false;

/* ============================================================
   State sync
   ============================================================ */

let _syncTimer = null;
let _lastSyncedKey = null;

function _stateKey() {
  const parts = [
    board.imageHash || "",
    board.imageWidth + "x" + board.imageHeight,
  ];
  for (const c of board.controlnets) {
    parts.push(
      c.title + ":" + (c.hash || "err")
      + (c.status === "done" && c.enabled === false ? ":off" : "")
    );
  }
  return parts.join("|");
}

function _scheduleStateSync() {
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_doStateSync, SYNC_DEBOUNCE_MS);
}

async function _doStateSync() {
  _syncTimer = null;
  const key = _stateKey();
  const wireControlnets = board.controlnets
    .filter(c => c.status === "done" && c.enabled !== false)
    .map(c => ({
      id:    c.title,
      name:  c.title,
      image: { hash: c.hash, width: c.width, height: c.height },
    }));

  try {
    const res = await fetch("/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        op: "set",
        state: {
          image_hash:  board.imageHash || "",
          image_dims:  [board.imageWidth, board.imageHeight],
          controlnets: wireControlnets,
        },
      }),
    });
    const data = await res.json();
    if (data.success) {
      _lastSyncedKey = key;
      board.lastSync = Date.now();
      board.syncError = null;
      const v = (data.version || "").slice(0, 8);
      const el = document.getElementById("stateVersion");
      if (el) el.textContent = v || "—";
    } else {
      board.syncError = data.message || "state sync failed";
      flashStatus("state sync failed: " + board.syncError, "bad");
    }
  } catch (e) {
    board.syncError = e.message;
    flashStatus("state sync failed: " + e.message, "bad");
  }
  _refreshStatusLine();
  pgkitNotifyAll();
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
    const total   = board.controlnets.length;
    const enabled = board.controlnets.filter(c => c.enabled !== false).length;
    text = "synced " + t
         + " \u00b7 " + board.imageWidth + "\u00d7" + board.imageHeight
         + " \u00b7 " + enabled + "/" + total + " controlnet(s)";
    cls = "ok";
  } else {
    text = "not yet synced"; cls = "warn";
  }

  if (el.textContent !== text) el.textContent = text;
  el.className = cls;
}

/* ============================================================
   Controlnet list
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
    const enabled = (c.status !== "done") || c.enabled !== false;
    const row = document.createElement("div");
    row.className = "controlnetRow"
      + (board.viewingControlnetTag === c.title ? " selected" : "")
      + (!enabled ? " disabled" : "");
    if (c.status === "done") row.dataset.clickable = "1";

    // Checkbox — only on ready rows.  stopPropagation keeps a
    // checkbox click from also triggering the row's click handler
    // (which switches the canvas to that image).
    if (c.status === "done") {
      const cb = document.createElement("input");
      cb.type    = "checkbox";
      cb.checked = c.enabled !== false;
      cb.title   = "Include this ControlNet in generation. Unchecked "
                 + "controlnets are omitted from /state, so "
                 + "ControlNetsConditioning skips their step.";
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", () => {
        c.enabled = !!cb.checked;
        _renderControlnetList();
        _refreshStatusLine();
        _scheduleStateSync();
      });
      row.appendChild(cb);
    }

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
   Panel field sync
   ============================================================ */

let _lastImageSig = "";

export function syncPanelFields() {
  const imageInfo = document.getElementById("imageInfo");
  if (imageInfo) {
    const sig = (board.imageHash || "")
              + "|" + board.imageWidth + "x" + board.imageHeight;
    if (sig !== _lastImageSig) {
      _lastImageSig = sig;
      const text = board.imageEl
        ? board.imageWidth + " \u00d7 " + board.imageHeight
          + "   [" + String(board.imageHash || "").slice(0, 8) + "\u2026]"
        : "No image loaded";
      if (imageInfo.textContent !== text) imageInfo.textContent = text;
    }
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

  // State sync on image / controlnets change.
  const key = _stateKey();
  if (key !== _lastSyncedKey && _syncTimer === null) {
    _scheduleStateSync();
  }

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
      enabled: true,
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
   Install
   ============================================================ */

export function installPanelBindings() {
  const refresh     = document.getElementById("refreshCanvasBtn");
  const preprocess  = document.getElementById("preprocessBtn");
  const pushBtn     = document.getElementById("pushToKritaBtn");
  const refreshKBtn = document.getElementById("refreshFromKritaBtn");
  const back        = document.getElementById("backToControlnetBtn");
  const kritaCheck  = document.getElementById("kritaCheckBtn");

  if (refresh)     refresh.addEventListener("click", _refreshCanvasAction);
  if (preprocess)  preprocess.addEventListener("click", _preprocessAction);
  if (pushBtn)     pushBtn.addEventListener("click", _pushToKritaAction);
  if (refreshKBtn) refreshKBtn.addEventListener("click", _refreshFromKritaAction);
  if (back)        back.addEventListener("click", _backToControlnetAction);
  if (kritaCheck)  kritaCheck.addEventListener("click", _kritaStatusAction);
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
      _lastSyncedKey = _stateKey();
    }
  } catch (_) {}
  syncPanelFields();
}

export function controlnetsBootSync() {
  _renderControlnetList();
  syncPanelFields();
  _kritaStatusAction();
  _bootPull().catch(() => {});
}

export function onDrawComplete() {
  syncPanelFields();
}
"""