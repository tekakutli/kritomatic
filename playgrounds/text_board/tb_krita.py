"""
tb_krita.py — fetch glue for the /krita endpoints.

ES module.  Exports the async fetch wrappers, error reporters, and
open/refresh helpers.  All POSTs return JSON with a `success` flag;
a non-success response has its `message` mirrored to `/log` on the
local server, so failures also appear on the terminal.

EXPORTS
=======
    reportError(tag, message)     POST /log, plus console.error
    refreshFromKrita()            POST /krita/dump_text, merge the
                                  result into the board, then
                                  refresh the panel and canvas
    activateItemDocument(it)      POST /krita/activate
    pickFilesViaServer()          POST /krita/pick_files (native
                                  dialog on the server machine)
    openOneFileInKrita(path)      POST /krita/open, returns boolean
    openAllClosed()               open every wanted path that is
                                  not already open, then refresh

The single-flight `_refreshInFlight` guard prevents two concurrent
`dump_text` requests from clobbering each other's responses.
"""

KRITA_JS = r"""
import { board, fitAll, addWantedPath, closedWantedPaths,
         mergeIncomingShapes }
  from "./tb_core.js";
import { syncItemList }      from "./tb_panel_lists.js";
import { _syncEditor }       from "./tb_panel_fields.js";
import { draw }              from "./tb_dispatch.js";
import { flashStatus }       from "./pgkit/panel_core.js";

/* ==========================================================================
   ERROR REPORTING
   ========================================================================== */

export function reportError(tag, message) {
  try {
    fetch("/log", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ tag: tag, message: String(message) }),
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

/* ==========================================================================
   REFRESH
   ========================================================================== */

let _refreshInFlight = false;

export async function refreshFromKrita() {
  if (_refreshInFlight) return;
  _refreshInFlight = true;
  console.log("[refresh] → POST /krita/dump_text");

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "pulling every text shape from Krita…";
    statusEl.className   = "";
  }

  let payload;
  try {
    const res = await fetch("/krita/dump_text", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    "{}",
    });
    console.log("[refresh] ← HTTP " + res.status);
    payload = await res.json();
  } catch (err) {
    console.error("[refresh] fetch or parse failed:", err);
    reportError("refresh", err.message);
    if (statusEl) {
      statusEl.textContent = "Request failed: " + err.message;
      statusEl.className   = "bad";
    }
    _refreshInFlight = false;
    return;
  }

  _refreshInFlight = false;
  console.log("[refresh] payload:", payload);

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) ? payload.message : "unknown error";
    console.error("[refresh] daemon reported error:", msg);
    reportError("refresh", msg);
    if (statusEl) {
      statusEl.textContent = "Krita: " + msg;
      statusEl.className   = "bad";
    }
    return;
  }

  mergeIncomingShapes(payload.shapes || []);

  if (statusEl) {
    const n = board.items.length;
    statusEl.textContent = "loaded " + n + " text shape" + (n === 1 ? "" : "s");
    statusEl.className   = "ok";
  }

  syncItemList();
  _syncEditor();
  draw();

  if (!board._autoFitDone && board.items.length > 0) {
    board._autoFitDone = true;
    fitAll();
  }
}

/* ==========================================================================
   ACTIVATE
   ========================================================================== */

export async function activateItemDocument(it) {
  if (!it || !it.documentPath) return;
  try {
    await fetch("/krita/activate", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ name: it.documentPath }),
    });
  } catch (err) {
    reportError("activate", err.message);
  }
}

/* ==========================================================================
   FILE PICKER
   ========================================================================== */

export async function pickFilesViaServer() {
  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "opening file picker…";
    statusEl.className   = "";
  }
  try {
    const res = await fetch("/krita/pick_files", { method: "POST" });
    const payload = await res.json();
    if (!payload || !payload.success) {
      const msg = (payload && payload.message) ? payload.message : "unknown";
      reportError("picker", msg);
      if (statusEl) {
        statusEl.textContent = "picker failed: " + msg;
        statusEl.className   = "bad";
      }
      return;
    }
    const paths = payload.paths || [];
    if (paths.length === 0) {
      flashStatus("no files picked", "warn");
      return;
    }
    for (const p of paths) addWantedPath(p);
    syncItemList();
    draw();
    flashStatus("added " + paths.length + " file(s); opening…", "ok");
    await openAllClosed();
  } catch (err) {
    reportError("picker", err.message);
    flashStatus("picker request failed: " + err.message, "bad");
  }
}

/* ==========================================================================
   OPEN
   ========================================================================== */

export async function openOneFileInKrita(path) {
  if (!path) return false;
  try {
    const res = await fetch("/krita/open", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ path: path, add_view: true }),
    });
    const payload = await res.json();
    if (payload && payload.success) return true;
    const msg = (payload && payload.message) ? payload.message : "unknown";
    reportError("open", msg);
    return false;
  } catch (err) {
    reportError("open", err.message);
    return false;
  }
}

export async function openAllClosed() {
  const closed = closedWantedPaths();
  if (closed.length === 0) {
    await refreshFromKrita();
    flashStatus("everything already open", "ok");
    return;
  }

  const statusEl = document.getElementById("status");
  let opened = 0;
  for (let i = 0; i < closed.length; i++) {
    if (statusEl) {
      statusEl.textContent =
        "opening " + (i + 1) + " / " + closed.length + ": " + closed[i].name;
      statusEl.className = "";
    }
    const ok = await openOneFileInKrita(closed[i].path);
    if (ok) opened++;
  }

  await refreshFromKrita();
}
"""
