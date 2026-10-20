"""
pg_krita.py — fetch glue for the /krita endpoints.

ES module.  Exports:

    reportError                POST /log, plus console.error
    refreshFromKrita           POST /krita/refresh, merge, redraw
    retrieveOpenFromKrita      add every open Krita doc to the board
    mergeIncoming              merge a refresh response into board.files
    openFileInKrita            POST /krita/open
    openAllMissing             open every closed file, sequentially
    activateFile               POST /krita/activate (or open first)
    pickFilesViaServer         POST /krita/pick_files (native dialog)
    importDroppedFile          stream a File from a drop event via
                               POST /krita/drop_file, or add directly
                               when the browser exposes file.path
    projectSelectedOntoOverlaps  POST /krita/paste_transforms

MERGE POLICY
============
On refresh, a card that has never been dragged-by-corner keeps its
height locked to `width × aspect`, so a freshly added card mirrors
its document's own proportions.  Once a corner drag has set
`userSized = true`, the user's height wins and refresh stops
touching it.  `mergeIncoming` ends with `pgkitNotifyAll()`, so a
remote client sees the change on the next animation frame.

DROP OVERLAY
============
The drop overlay (`#dropOverlay`) is created by `installDropOverlay`
at module load.  It must be a sibling of `<canvas>`, not a child of
`#ui` — a child would inherit the panel's `display: none` when a
remote client connects, hiding the drop target exactly when
someone on the playground machine wants to drop a file.

DROP PAYLOAD — RAW BYTES
========================
`/krita/drop_file` receives the file's raw bytes; the filename
travels in the `X-Filename` header, URL-encoded.  pgkit's
`server.serve()` gained `raw_routes` support precisely so board
could keep this wire format.
"""

KRITA_JS = r"""
import {
  board, view, addFileByPath, fitAll, computeProjectionRegions,
} from "./pg_core.js";
import { syncFileList } from "./pg_panel_lists.js";
import { _syncCardFields } from "./pg_panel_fields.js";
import { draw, updateStatus } from "./pg_dispatch.js";
import {
  pgkitNotifyAll, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

export function reportError(tag, message) {
  try {
    fetch("/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag, message: String(message) }),
    }).catch(() => {});
  } catch (_) {}
  try { console.error("[" + tag + "] " + message); } catch (_) {}
}

window.addEventListener("error", (e) => {
  const loc = (e.filename || "") + ":" + (e.lineno || "") + ":" + (e.colno || "");
  reportError("uncaught", (e.message || "unknown error") + " @ " + loc);
});
window.addEventListener("unhandledrejection", (e) => {
  let reason = e.reason;
  if (reason && reason.message) reason = reason.message;
  reportError("unhandled-rejection", reason || "no reason given");
});

(function installDropOverlay() {
  if (document.getElementById("dropOverlay")) return;
  const el = document.createElement("div");
  el.id = "dropOverlay";
  el.textContent = "Drop files to add them to the board";
  document.body.appendChild(el);
})();

/* ==========================================================================
   REFRESH
   ========================================================================== */

let _refreshInFlight = false;

export async function refreshFromKrita() {
  if (_refreshInFlight) return;
  _refreshInFlight = true;

  const statusEl = document.getElementById("status");
  if (statusEl) { statusEl.textContent = "asking Krita which files are open…"; statusEl.className = ""; }

  const paths = board.files.map(f => f.path);
  let payload;
  try {
    const res = await fetch("/krita/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, max_size: 512 }),
    });
    payload = await res.json();
  } catch (err) {
    reportError("refresh", err.message);
    if (statusEl) { statusEl.textContent = "Request failed: " + err.message; statusEl.className = "bad"; }
    _refreshInFlight = false;
    return;
  }

  _refreshInFlight = false;

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) ? payload.message : "unknown error";
    reportError("refresh", msg);
    if (statusEl) { statusEl.textContent = "Krita: " + msg; statusEl.className = "bad"; }
    return;
  }

  mergeIncoming(payload);

  if (payload.partial && payload.message && statusEl) {
    statusEl.textContent = payload.message; statusEl.className = "warn";
    setTimeout(updateStatus, 1800);
  } else {
    const opens  = board.files.filter(f => f.open).length;
    const closed = board.files.filter(f => !f.open && !f.missing).length;
    const miss   = board.files.filter(f => f.missing).length;
    if (statusEl) {
      statusEl.textContent = opens + " open · " + closed + " closed" + (miss ? (" · " + miss + " missing") : "");
      statusEl.className = miss ? "warn" : "ok";
      setTimeout(updateStatus, 1600);
    }
  }

  syncFileList();
  draw();
}

export async function retrieveOpenFromKrita() {
  const statusEl = document.getElementById("status");
  if (statusEl) { statusEl.textContent = "retrieving currently-open documents from Krita…"; statusEl.className = ""; }

  const paths = board.files.map(f => f.path);
  let payload;
  try {
    const res = await fetch("/krita/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, max_size: 512 }),
    });
    payload = await res.json();
  } catch (err) {
    reportError("retrieve", err.message);
    if (statusEl) { statusEl.textContent = "Retrieve failed: " + err.message; statusEl.className = "bad"; }
    return;
  }

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) ? payload.message : "unknown error";
    reportError("retrieve", msg);
    if (statusEl) { statusEl.textContent = "Krita: " + msg; statusEl.className = "bad"; }
    return;
  }

  const incoming = payload.all_documents || [];
  const onBoard = new Set();
  for (const f of board.files) if (f.normPath) onBoard.add(f.normPath);

  let added = 0;
  for (const doc of incoming) {
    const n = doc.norm_path;
    if (!n || onBoard.has(n)) continue;
    const idx = addFileByPath(doc.file_name);
    if (idx >= 0) { board.files[idx].normPath = n; onBoard.add(n); added++; }
  }

  mergeIncoming(payload);
  syncFileList();
  draw();

  if (statusEl) {
    statusEl.textContent = added > 0 ? "retrieved " + added + " new document" + (added === 1 ? "" : "s") : "no new open documents";
    statusEl.className = added > 0 ? "ok" : "";
    setTimeout(updateStatus, 1600);
  }
}

export function mergeIncoming(payload) {
  const results = payload.results || [];
  const byPath = new Map();
  for (const r of results) byPath.set(r.path, r);
  const activeName = payload.active || null;

  for (const f of board.files) {
    const r = byPath.get(f.path);
    if (!r) continue;
    if (r.norm_path) f.normPath = r.norm_path;

    if (r.open && r.doc) {
      const doc = r.doc;
      f.open = true;
      f.active = !!doc.active || (activeName && doc.name === activeName);
      f.modified = !!doc.modified;
      f.missing = false;
      f.kritaName = doc.file_name || doc.name || f.kritaName;
      if (doc.data_url) f.thumb = doc.data_url;
      if (doc.width && doc.height) {
        f.docWidth = doc.width; f.docHeight = doc.height;
        f.aspect = doc.height / doc.width;
        if (!f.userSized) f.h = Math.round(f.w * f.aspect);
      }
    } else {
      f.open = false; f.active = false; f.modified = false;
      f.missing = !!r.missing;
    }
  }

  pgkitNotifyAll();
}

/* ==========================================================================
   OPEN
   ========================================================================== */

export async function openFileInKrita(file) {
  if (!file) return false;
  const statusEl = document.getElementById("status");
  if (statusEl) { statusEl.textContent = "opening " + file.name + " in Krita…"; statusEl.className = ""; }

  try {
    const res = await fetch("/krita/open", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: file.path, add_view: true }),
    });
    const payload = await res.json();
    if (payload && payload.success) {
      if (statusEl) { statusEl.textContent = payload.message || ("opened " + file.name); statusEl.className = "ok"; }
      return true;
    }
    const msg = (payload && payload.message) ? payload.message : "unknown";
    reportError("open", msg);
    if (statusEl) { statusEl.textContent = "open failed: " + msg; statusEl.className = "bad"; }
    return false;
  } catch (err) {
    reportError("open", err.message);
    if (statusEl) { statusEl.textContent = "open request failed: " + err.message; statusEl.className = "bad"; }
    return false;
  }
}

export async function openAllMissing() {
  const missing = board.files.filter(f => !f.open && !f.missing);
  if (missing.length === 0) { flashStatus("every file already open", "ok"); return; }
  const statusEl = document.getElementById("status");
  let opened = 0;
  for (let i = 0; i < missing.length; i++) {
    if (statusEl) { statusEl.textContent = "opening " + (i+1) + " / " + missing.length + ": " + missing[i].name; statusEl.className = ""; }
    if (await openFileInKrita(missing[i])) opened++;
  }
  await refreshFromKrita();
  if (statusEl) {
    statusEl.textContent = "opened " + opened + " of " + missing.length;
    statusEl.className = (opened === missing.length) ? "ok" : "warn";
    setTimeout(updateStatus, 1800);
  }
}

export async function activateFile(file) {
  if (!file) return;
  if (!file.open) {
    const ok = await openFileInKrita(file);
    if (ok) await refreshFromKrita();
    return;
  }

  const statusEl = document.getElementById("status");
  if (statusEl) { statusEl.textContent = "activating " + file.name + "…"; statusEl.className = ""; }
  const key = file.kritaName || file.path;

  try {
    const res = await fetch("/krita/activate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: key }),
    });
    const payload = await res.json();
    if (payload && payload.success) {
      if (statusEl) { statusEl.textContent = payload.message || ("activated " + file.name); statusEl.className = "ok"; setTimeout(updateStatus, 1400); }
    } else {
      const msg = (payload && payload.message) ? payload.message : "unknown";
      reportError("activate", msg);
      if (statusEl) { statusEl.textContent = "activate failed: " + msg; statusEl.className = "bad"; }
    }
  } catch (err) {
    reportError("activate", err.message);
    if (statusEl) { statusEl.textContent = "activate request failed: " + err.message; statusEl.className = "bad"; }
  }
}

/* ==========================================================================
   FILE PICKER
   ========================================================================== */

export async function pickFilesViaServer() {
  const statusEl = document.getElementById("status");
  if (statusEl) { statusEl.textContent = "opening file picker…"; statusEl.className = ""; }
  try {
    const res = await fetch("/krita/pick_files", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const payload = await res.json();
    if (!payload || !payload.success) {
      const msg = (payload && payload.message) ? payload.message : "unknown";
      reportError("picker", msg);
      if (statusEl) { statusEl.textContent = "picker failed: " + msg; statusEl.className = "bad"; }
      return;
    }
    const paths = payload.paths || [];
    if (paths.length === 0) { if (statusEl) { statusEl.textContent = "no files picked"; statusEl.className = ""; setTimeout(updateStatus, 1200); } return; }
    for (const p of paths) addFileByPath(p);
    syncFileList(); draw();
    flashStatus("added " + paths.length + " file(s)", "ok");
  } catch (err) {
    reportError("picker", err.message);
    if (statusEl) { statusEl.textContent = "picker request failed: " + err.message; statusEl.className = "bad"; }
  }
}

export async function importDroppedFile(file) {
  if (!file) return false;
  if (typeof file.path === "string" && file.path) {
    return addFileByPath(file.path) >= 0;
  }
  let res;
  try {
    res = await fetch("/krita/drop_file", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name || "dropped_file"),
      },
      body: file,
    });
  } catch (err) { reportError("drop", "upload failed: " + err.message); return false; }

  let payload;
  try { payload = await res.json(); }
  catch (err) { reportError("drop", "bad response: " + err.message); return false; }
  if (!payload || !payload.success) {
    reportError("drop", (payload && payload.message) || "upload failed");
    return false;
  }
  return addFileByPath(payload.path) >= 0;
}

/* ==========================================================================
   PROJECT OVERLAPS
   ========================================================================== */

export async function projectSelectedOntoOverlaps() {
  try {
    const calc = computeProjectionRegions();
    if (!calc) { flashStatus("no card selected", "warn"); return; }
    if (calc.reason) { flashStatus(calc.reason, "warn"); return; }
    if (calc.regions.length === 0) {
      flashStatus(
        calc.skippedClosed > 0
          ? "overlaps only closed cards (open them first)"
          : "selected card overlaps nothing open",
        "warn");
      return;
    }

    const statusEl = document.getElementById("status");
    if (statusEl) { statusEl.textContent = "projecting onto " + calc.regions.length + " card(s)…"; statusEl.className = ""; }

    let payload;
    try {
      const res = await fetch("/krita/paste_transforms", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transforms: calc.regions }),
      });
      payload = await res.json();
    } catch (err) {
      reportError("project", "request failed: " + err.message);
      if (statusEl) { statusEl.textContent = "projection request failed: " + err.message; statusEl.className = "bad"; }
      return;
    }

    if (!payload || !payload.success) {
      const msg = (payload && payload.message) ? payload.message : "unknown";
      reportError("project", "endpoint failed: " + msg);
      if (statusEl) { statusEl.textContent = "projection failed: " + msg; statusEl.className = "bad"; }
      return;
    }

    await refreshFromKrita();

    const ok = payload.successful || 0, bad = payload.failed || 0;
    let firstFailMsg = "";
    if (bad > 0) {
      for (const r of (payload.results || [])) {
        if (r.status === "error") { firstFailMsg = r.message || ""; break; }
      }
    }
    let msg = "projected onto " + ok + " card(s)";
    if (bad) msg += " · " + bad + " failed";
    if (calc.skippedClosed > 0) msg += " · " + calc.skippedClosed + " closed skipped";
    if (firstFailMsg) msg += " — " + firstFailMsg;
    flashStatus(msg, bad ? "bad" : "ok");
  } catch (err) {
    reportError("project", err.message);
    flashStatus("project: " + err.message, "bad");
  }
}
"""
