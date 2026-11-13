"""
tbox_krita.py — fetch glue for the /krita endpoints.

ES module.  Exports the async fetch wrappers, error reporters, and
open/refresh helpers.  All POSTs return JSON with a `success` flag;
a non-success response is mirrored to the server's terminal via
`console.error`, which pgkit/panel_core.js's console override routes
to `POST ./__log`.

EXPORTS
=======
    reportError(tag, message)     console.error only (mirrored by
                                  pgkit/panel_core.js's override)
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

REPORTERROR — WHY IT IS A THIN WRAPPER
======================================
Earlier this file carried two error channels:

    1. a POST to /log, which text_boxes_server.py printed to the
       terminal; and
    2. a `console.error` call.

With `pgkit/panel_core.js`'s console override in place, the second
channel already mirrors to the server's terminal (`POST ./__log`
→ `pgkit/server.py::_handle_log`).  The first channel is therefore
redundant — each error was landing in the terminal twice, once as
`[text_boxes] js/{tag}: ...` from /log and once as
`[text_boxes] console-error: [{tag}] ...` from ./__log.

`reportError` is now a single-channel wrapper over `console.error`.
The `/log` route is kept in `text_boxes_server.py` (harmless dead
endpoint) so any external caller posting there still gets a print,
but nothing in this file reaches for it.

The window-level hooks (`error` and `unhandledrejection`) that used
to live here are also removed.  `pgkit/panel_core.js` installs one
pair of hooks for the whole page; doubling them meant every uncaught
error and unhandled rejection landed in the terminal twice — once as
`js-error` / `js-rejection` from panel_core, once as `console-error`
from this file's own hook that called `reportError` which called
`console.error`.
"""

KRITA_JS = r"""
import { board, fitAll, addWantedPath, closedWantedPaths,
         mergeIncomingShapes }
  from "./tbox_core.js";
import { syncItemList }      from "./tbox_panel_lists.js";
import { _syncEditor }       from "./tbox_panel_fields.js";
import { draw }              from "./tbox_dispatch.js";
import { flashStatus }       from "./pgkit/panel_core.js";

/* ==========================================================================
   ERROR REPORTING
   ==========================================================================
   Thin wrapper over console.error.  pgkit/panel_core.js's console
   override mirrors every console.error call to POST ./__log, which
   the playground server prints to its stdout.  This is the one
   channel for text_boxes's handled errors (try/catch failures).  See
   the module docstring, REPORTERROR — WHY IT IS A THIN WRAPPER.

   The tag becomes a bracketed prefix inside the message: a caller
   that passes tag="refresh" and message="file not found" produces a
   terminal line reading
       [text_boxes] console-error: [refresh] file not found
   where `[text_boxes]` is the document.title-derived playground
   prefix and `console-error` is the class set by panel_core's
   override. */

export function reportError(tag, message) {
  try {
    console.error("[" + tag + "] " + message);
  } catch (_) {}
}

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