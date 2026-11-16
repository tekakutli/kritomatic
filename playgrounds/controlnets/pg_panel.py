"""Panel handlers: Krita status, refresh @thecontrolnet, state sync.

The only image-load path is the daemon's `@thecontrolnet` slot
selector: autosync picks up selector changes automatically, and the
"Refresh @thecontrolnet" button re-pulls on demand.  There is no
local-file pick, no clear, and no composite import — every button
that could put pixels on the canvas while the slot still pointed
somewhere else is gone, so the canvas can never drift from what the
future node will fetch.

State sync is debounced and posts `{op: "set", state: {image_hash,
image_dims, controlnets}}` whenever the image changes.  The future
node's `/version` endpoint picks up the change via the hash-of-state.
"""

PANEL_JS = r"""
import { board } from "./pg_state.js";
import {
  checkKritaStatus, fetchActiveState,
} from "./pg_client.js";
import { fetchSelectedLayer } from "./pg_autosync.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { SYNC_DEBOUNCE_MS } from "./pg_tuning.js";

const PLAYGROUND_ID = "controlnets";
const SLOT_ID       = "@thecontrolnet";

/* ============================================================
   State sync
   ============================================================ */

let _syncTimer = null;
let _lastSyncedKey = null;

function _stateKey() {
  return (board.imageHash || "") + "|"
       + board.imageWidth + "x" + board.imageHeight + "|"
       + board.controlnets.length;
}

function _scheduleStateSync() {
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_doStateSync, SYNC_DEBOUNCE_MS);
}

async function _doStateSync() {
  _syncTimer = null;
  const key = _stateKey();
  try {
    const res = await fetch("/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        op: "set",
        state: {
          image_hash: board.imageHash || "",
          image_dims: [board.imageWidth, board.imageHeight],
          controlnets: board.controlnets || [],
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
  if (board.syncError) {
    text = "state sync failed: " + board.syncError; cls = "bad";
  } else if (!board.imageHash) {
    text = "no image loaded"; cls = "";
  } else if (board.lastSync) {
    const t = new Date(board.lastSync).toTimeString().slice(0, 8);
    text = "synced " + t
         + " \u00b7 " + board.imageWidth + "\u00d7" + board.imageHeight
         + " \u00b7 " + board.controlnets.length + " controlnet(s)";
    cls = "ok";
  } else {
    text = "not yet synced"; cls = "warn";
  }

  if (el.textContent !== text) el.textContent = text;
  el.className = cls;
}

/* ============================================================
   Panel fields
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

  // Trigger state sync when the image or controlnets list changed.
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
  const refresh    = document.getElementById("refreshCanvasBtn");
  const kritaCheck = document.getElementById("kritaCheckBtn");

  if (refresh)    refresh.addEventListener("click", _refreshCanvasAction);
  if (kritaCheck) kritaCheck.addEventListener("click", _kritaStatusAction);
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
      // Seed _lastSyncedKey so we don't immediately re-sync the
      // server's own state back at it.
      _lastSyncedKey = _stateKey();
    }
  } catch (_) {}
  syncPanelFields();
}

export function controlnetsBootSync() {
  syncPanelFields();
  _kritaStatusAction();
  _bootPull().catch(() => {});
}

export function onDrawComplete() {
  syncPanelFields();
}
"""