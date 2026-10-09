"""
pg_krita.py — JS glue for the /krita endpoints.

Every caught error and every unhandled error or rejection is also
POSTed to /log on the local server, so it appears on the same
terminal that serves the board.  The browser console keeps the
original error; the terminal gets a copy.
"""

KRITA_JS = r"""
/* ==========================================================================
   ERROR REPORTING
   ========================================================================== */

function reportError(tag, message) {
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

async function refreshFromKrita() {
  if (_refreshInFlight) return;
  _refreshInFlight = true;

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "asking Krita which files are open…";
    statusEl.className   = "";
  }

  const paths = board.files.map(f => f.path);

  let payload;
  try {
    const res = await fetch("/krita/refresh", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ paths: paths, max_size: 512 }),
    });
    payload = await res.json();
  } catch (err) {
    reportError("refresh", err.message);
    if (statusEl) {
      statusEl.textContent = "Request failed: " + err.message;
      statusEl.className   = "bad";
    }
    _refreshInFlight = false;
    return;
  }

  _refreshInFlight = false;

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) ? payload.message : "unknown error";
    reportError("refresh", msg);
    if (statusEl) {
      statusEl.textContent = "Krita: " + msg;
      statusEl.className   = "bad";
    }
    return;
  }

  mergeIncoming(payload);

  if (payload.partial && payload.message && statusEl) {
    statusEl.textContent = payload.message;
    statusEl.className   = "warn";
    setTimeout(updateStatus, 1800);
  } else {
    const opens  = board.files.filter(f => f.open).length;
    const closed = board.files.filter(f => !f.open && !f.missing).length;
    const miss   = board.files.filter(f => f.missing).length;
    if (statusEl) {
      statusEl.textContent =
        opens + " open · " + closed + " closed" +
        (miss ? (" · " + miss + " missing") : "");
      statusEl.className = miss ? "warn" : "ok";
      setTimeout(updateStatus, 1600);
    }
  }

  syncFileList();
  draw();
}

/* ==========================================================================
   RETRIEVE OPEN
   ========================================================================== */

async function retrieveOpenFromKrita() {
  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "retrieving currently-open documents from Krita…";
    statusEl.className   = "";
  }

  const paths = board.files.map(f => f.path);

  let payload;
  try {
    const res = await fetch("/krita/refresh", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ paths: paths, max_size: 512 }),
    });
    payload = await res.json();
  } catch (err) {
    reportError("retrieve", err.message);
    if (statusEl) {
      statusEl.textContent = "Retrieve failed: " + err.message;
      statusEl.className   = "bad";
    }
    return;
  }

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) ? payload.message : "unknown error";
    reportError("retrieve", msg);
    if (statusEl) {
      statusEl.textContent = "Krita: " + msg;
      statusEl.className   = "bad";
    }
    return;
  }

  const incoming = payload.all_documents || [];
  const onBoard  = new Set();
  for (const f of board.files) {
    if (f.normPath) onBoard.add(f.normPath);
  }

  let added = 0;
  for (const doc of incoming) {
    const n = doc.norm_path;
    if (!n) continue;
    if (onBoard.has(n)) continue;
    const idx = addFileByPath(doc.file_name);
    if (idx >= 0) {
      board.files[idx].normPath = n;
      onBoard.add(n);
      added++;
    }
  }

  mergeIncoming(payload);

  syncFileList();
  draw();

  if (statusEl) {
    if (added > 0) {
      statusEl.textContent =
        "retrieved " + added + " new document" + (added === 1 ? "" : "s");
      statusEl.className = "ok";
    } else {
      statusEl.textContent = "no new open documents";
      statusEl.className   = "";
    }
    setTimeout(updateStatus, 1600);
  }
}

/* ==========================================================================
   MERGE POLICY
   ==========================================================================
   On refresh, a card that the user has never resized by hand keeps
   its height locked to width · aspect, so it mirrors the document's
   own proportions.  Once the user has dragged a corner on the card
   (`userSized === true`), the user's size wins and refresh only
   updates metadata. */

function mergeIncoming(payload) {
  const results = payload.results || [];
  const byPath  = new Map();
  for (const r of results) byPath.set(r.path, r);

  const activeName = payload.active || null;

  for (const f of board.files) {
    const r = byPath.get(f.path);
    if (!r) continue;

    if (r.norm_path) f.normPath = r.norm_path;

    if (r.open && r.doc) {
      const doc = r.doc;
      f.open     = true;
      f.active   = !!doc.active || (activeName && doc.name === activeName);
      f.modified = !!doc.modified;
      f.missing  = false;
      f.kritaName = doc.file_name || doc.name || f.kritaName;
      if (doc.data_url) f.thumb = doc.data_url;
      if (doc.width && doc.height) {
        f.docWidth  = doc.width;
        f.docHeight = doc.height;
        f.aspect    = doc.height / doc.width;
        // Preserve the user's size once they have set it.  Until
        // then, the card mirrors its document's own proportions.
        if (!f.userSized) {
          f.h = Math.round(f.w * f.aspect);
        }
      }
    } else {
      f.open     = false;
      f.active   = false;
      f.modified = false;
      f.missing  = !!r.missing;
    }
  }
}

/* ==========================================================================
   OPEN
   ========================================================================== */

async function openFileInKrita(file) {
  if (!file) return false;

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "opening " + file.name + " in Krita…";
    statusEl.className   = "";
  }

  try {
    const res = await fetch("/krita/open", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ path: file.path, add_view: true }),
    });
    const payload = await res.json();
    if (payload && payload.success) {
      if (statusEl) {
        statusEl.textContent = payload.message || ("opened " + file.name);
        statusEl.className   = "ok";
      }
      return true;
    } else {
      const msg = (payload && payload.message) ? payload.message : "unknown";
      reportError("open", msg);
      if (statusEl) {
        statusEl.textContent = "open failed: " + msg;
        statusEl.className   = "bad";
      }
      return false;
    }
  } catch (err) {
    reportError("open", err.message);
    if (statusEl) {
      statusEl.textContent = "open request failed: " + err.message;
      statusEl.className   = "bad";
    }
    return false;
  }
}

async function openAllMissing() {
  const missing = board.files.filter(f => !f.open && !f.missing);
  if (missing.length === 0) {
    flashStatus("every file already open", "ok");
    return;
  }

  const statusEl = document.getElementById("status");
  let opened = 0;
  for (let i = 0; i < missing.length; i++) {
    if (statusEl) {
      statusEl.textContent =
        "opening " + (i + 1) + " / " + missing.length + ": " + missing[i].name;
      statusEl.className = "";
    }
    const ok = await openFileInKrita(missing[i]);
    if (ok) opened++;
  }

  await refreshFromKrita();

  if (statusEl) {
    statusEl.textContent = "opened " + opened + " of " + missing.length;
    statusEl.className   = (opened === missing.length) ? "ok" : "warn";
    setTimeout(updateStatus, 1800);
  }
}

/* ==========================================================================
   ACTIVATE
   ========================================================================== */

async function activateFile(file) {
  if (!file) return;
  if (!file.open) {
    const ok = await openFileInKrita(file);
    if (ok) await refreshFromKrita();
    return;
  }

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "activating " + file.name + "…";
    statusEl.className   = "";
  }

  const key = file.kritaName || file.path;

  try {
    const res = await fetch("/krita/activate", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ name: key }),
    });
    const payload = await res.json();
    if (payload && payload.success) {
      if (statusEl) {
        statusEl.textContent = payload.message || ("activated " + file.name);
        statusEl.className   = "ok";
        setTimeout(updateStatus, 1400);
      }
    } else {
      const msg = (payload && payload.message) ? payload.message : "unknown";
      reportError("activate", msg);
      if (statusEl) {
        statusEl.textContent = "activate failed: " + msg;
        statusEl.className   = "bad";
      }
    }
  } catch (err) {
    reportError("activate", err.message);
    if (statusEl) {
      statusEl.textContent = "activate request failed: " + err.message;
      statusEl.className   = "bad";
    }
  }
}

/* ==========================================================================
   FILE PICKER
   ========================================================================== */

async function pickFilesViaServer() {
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
      if (statusEl) {
        statusEl.textContent = "no files picked";
        statusEl.className   = "";
        setTimeout(updateStatus, 1200);
      }
      return;
    }
    for (const p of paths) addFileByPath(p);
    syncFileList();
    draw();
    flashStatus("added " + paths.length + " file(s)", "ok");
  } catch (err) {
    reportError("picker", err.message);
    if (statusEl) {
      statusEl.textContent = "picker request failed: " + err.message;
      statusEl.className   = "bad";
    }
  }
}

/* ==========================================================================
   PROJECT OVERLAPS
   ==========================================================================
   Sends one batch of transform-based paste commands.  Each command
   tells the daemon: "take this document, apply this affine map, and
   paste the result into that document".  The daemon composites the
   source, transforms it, clips to the target's canvas, and adds the
   result as a new paint layer.

   On failure, the first error message is shown on the status line
   and the full response is logged to the console.  The same messages
   appear on the terminal that serves the board. */

async function projectSelectedOntoOverlaps() {
  try {
    const calc = computeProjectionRegions();

    if (!calc) {
      flashStatus("no card selected", "warn");
      return;
    }
    if (calc.reason) {
      flashStatus(calc.reason, "warn");
      return;
    }
    if (calc.regions.length === 0) {
      flashStatus(
        calc.skippedClosed > 0
          ? "overlaps only closed cards (open them first)"
          : "selected card overlaps nothing open",
        "warn"
      );
      return;
    }

    const statusEl = document.getElementById("status");
    if (statusEl) {
      statusEl.textContent =
        "projecting onto " + calc.regions.length + " card(s)…";
      statusEl.className = "";
    }

    let payload;
    try {
      const res = await fetch("/krita/paste_transforms", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ transforms: calc.regions }),
      });
      payload = await res.json();
    } catch (err) {
      reportError("project", "request failed: " + err.message);
      if (statusEl) {
        statusEl.textContent = "projection request failed: " + err.message;
        statusEl.className   = "bad";
      }
      return;
    }

    if (!payload || !payload.success) {
      const msg = (payload && payload.message) ? payload.message : "unknown";
      reportError("project", "endpoint failed: " + msg);
      if (statusEl) {
        statusEl.textContent = "projection failed: " + msg;
        statusEl.className   = "bad";
      }
      return;
    }

    await refreshFromKrita();

    const ok  = payload.successful || 0;
    const bad = payload.failed     || 0;

    let firstFailMsg = "";
    if (bad > 0) {
      for (const r of (payload.results || [])) {
        if (r.status === "error") { firstFailMsg = r.message || ""; break; }
      }
    }

    let msg = "projected onto " + ok + " card(s)";
    if (bad)      msg += " · " + bad + " failed";
    if (calc.skippedClosed > 0)
                  msg += " · " + calc.skippedClosed + " closed skipped";
    if (firstFailMsg) msg += " — " + firstFailMsg;

    flashStatus(msg, bad ? "bad" : "ok");
  } catch (err) {
    reportError("project", err.message);
    flashStatus("project: " + err.message, "bad");
  }
}
"""
