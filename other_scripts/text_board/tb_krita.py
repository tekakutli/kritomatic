"""
tb_krita.py — JS glue for the /krita endpoints.

Every caught error and every unhandled error or rejection is also
POSTed to /log so it appears on the terminal that serves the board.
After the first successful refresh, if nothing has been fit yet, the
board fits to content once; after that the user's zoom and pan are
authoritative.
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

  mergeIncomingShapes(payload.shapes || []);

  if (statusEl) {
    const n = board.items.length;
    statusEl.textContent = "loaded " + n + " text shape" + (n === 1 ? "" : "s");
    statusEl.className   = "ok";
    setTimeout(updateStatus, 1600);
  }

  syncItemList();
  _syncEditor();
  draw();

  // Auto-fit only the first time the board actually has content.
  // An empty first refresh must not consume the auto-fit flag, or
  // a slow daemon leaves the view parked at the wrong place for the
  // rest of the session.
  if (!board._autoFitDone && board.items.length > 0) {
    board._autoFitDone = true;
    fitAll();
    console.log("[board] auto-fit on first non-empty refresh;",
                "items =", board.items.length);
  }
}

/* ==========================================================================
   APPLY CHANGES
   ========================================================================== */

async function applySelectedChanges() {
  const it = selectedItem();
  if (!it) {
    flashStatus("no card selected", "warn");
    return;
  }

  const textEl  = document.getElementById("editText");
  const fontEl  = document.getElementById("editFont");
  const sizeEl  = document.getElementById("editSize");
  const colorEl = document.getElementById("editColor");
  const alignEl = document.getElementById("editAlign");
  const rotEl   = document.getElementById("editRot");

  const patch = {};
  const newText = textEl.value;
  if (newText !== it.text) patch.text = newText;

  const newFont = fontEl.value.trim();
  if (newFont && newFont !== it.fontFamily) patch.font_family = newFont;

  const newSizeRaw = parseFloat(sizeEl.value);
  if (isFinite(newSizeRaw) && newSizeRaw !== it.fontSize)
    patch.font_size = newSizeRaw;

  const newColor = colorEl.value.trim();
  if (newColor && newColor !== it.color) patch.color = newColor;

  const newAlign = alignEl.value;
  if (newAlign && newAlign !== it.alignment) patch.alignment = newAlign;

  const newRotRaw = parseFloat(rotEl.value);
  if (isFinite(newRotRaw) &&
      Math.abs(newRotRaw - it.rotationDeg) > 0.01)
    patch.rotation_deg = newRotRaw;

  if (Object.keys(patch).length === 0) {
    flashStatus("no changes to apply", "warn");
    return;
  }

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "applying " +
      Object.keys(patch).length + " change(s)…";
    statusEl.className = "";
  }

  let payload;
  try {
    const res = await fetch("/krita/patch_text", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        document_path: it.documentPath,
        layer_name:    it.layerName,
        text_index:    it.textIndex,
        patch:         patch,
      }),
    });
    payload = await res.json();
  } catch (err) {
    reportError("apply", err.message);
    if (statusEl) {
      statusEl.textContent = "apply request failed: " + err.message;
      statusEl.className   = "bad";
    }
    return;
  }

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) ? payload.message : "unknown";
    reportError("apply", msg);
    if (statusEl) {
      statusEl.textContent = "apply failed: " + msg;
      statusEl.className   = "bad";
    }
    return;
  }

  flashStatus("applied " + Object.keys(patch).length + " change(s)", "ok");
  await refreshFromKrita();
}

async function activateItemDocument(it) {
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

async function openOneFileInKrita(path) {
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

async function openAllClosed() {
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

  if (statusEl) {
    statusEl.textContent = "opened " + opened + " of " + closed.length;
    statusEl.className   = (opened === closed.length) ? "ok" : "warn";
    setTimeout(updateStatus, 1800);
  }
}
"""
