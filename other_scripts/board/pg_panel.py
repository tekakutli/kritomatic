"""
pg_panel.py — panel bindings.

Buttons
    Refresh from Krita    sync state for the files on the board
    + Add files…          server-side native file picker
    Retrieve open         add every open document from Krita that is
                          not yet on the board
    Open all              open every closed file in Krita
    Project overlaps      copy the selected card's overlapping region
                          into each overlapped Krita document as a new
                          layer (also bound to P)
    Arrange grid / Fit    layout helpers
    Reset view            zoom = 1, pan = centre
    Clear                 empty the board (does not touch Krita)
    Save board            download layout + paths as JSON
    Load board            pick a JSON file; applies layout, then opens
                          every missing file in Krita, then refreshes

Slider
    Card width            reference width for the board.  Existing
                          cards are rescaled proportionally so their
                          relative sizes are preserved.

Scrub inputs
    Scale                 selected card's width / defaultWidth
    Rotation              selected card's rotation, in degrees
"""

PANEL_JS = r"""
/* ==========================================================================
   CONSTANTS
   ========================================================================== */

const SCRUB_DRAG_THRESHOLD = 3;

const SCALE_SCRUB_RATE      = 0.005;
const SCALE_SNAP_STEP       = 0.25;
const ROTATION_SCRUB_RATE   = 0.5;
const ROTATION_SNAP_STEP    = 15;

/* ==========================================================================
   SLIDER
   ========================================================================== */

function syncPanelSliders() {
  const s = document.getElementById("cardWSlider");
  if (s) s.value = board.defaultWidth;
  updatePanelLabels();
}

function updatePanelLabels() {
  const v = document.getElementById("cardWVal");
  if (v) v.textContent = String(board.defaultWidth);
}

function selectedFile() {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length)
    return null;
  return board.files[board.selectedIdx];
}

/* ==========================================================================
   CARD FIELDS (Scale, Rotation)
   ========================================================================== */

function _syncCardFields() {
  const scaleEl = document.getElementById("cardScaleVal");
  const rotEl   = document.getElementById("cardRotVal");
  const f = selectedFile();

  if (!f) {
    if (scaleEl) {
      scaleEl.disabled = true;
      if (document.activeElement !== scaleEl) scaleEl.value = "\u2014";
    }
    if (rotEl) {
      rotEl.disabled = true;
      if (document.activeElement !== rotEl) rotEl.value = "\u2014";
    }
    return;
  }

  if (scaleEl) {
    scaleEl.disabled = false;
    if (document.activeElement !== scaleEl) {
      const denom = board.defaultWidth > 0 ? board.defaultWidth : 1;
      scaleEl.value = (f.w / denom).toFixed(2);
    }
  }
  if (rotEl) {
    rotEl.disabled = false;
    if (document.activeElement !== rotEl) {
      rotEl.value = (f.rotation * 180 / Math.PI).toFixed(1);
    }
  }
}

/* ==========================================================================
   SCRUB-INPUT INSTALLER
   ========================================================================== */

function _installScrubInput(el, opts) {
  let drag = null;

  function onMove(e) {
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    if (!drag.moved) {
      if (Math.abs(dx) < SCRUB_DRAG_THRESHOLD) return;
      drag.moved = true;
    }

    let raw = drag.startValue + dx * opts.rate;
    if (opts.snapStep && e.shiftKey) {
      const delta = raw - drag.startValue;
      const snapped = Math.round(delta / opts.snapStep) * opts.snapStep;
      raw = drag.startValue + snapped;
    }
    opts.write(raw);
    draw();
  }

  function onUp() {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    const wasMoved = drag && drag.moved;
    drag = null;
    if (!wasMoved) {
      el.focus();
      el.select();
    }
  }

  el.addEventListener("mousedown", (e) => {
    if (el.disabled) return;
    if (document.activeElement === el) return;
    e.preventDefault();
    drag = { startX: e.clientX, startValue: opts.read(), moved: false };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const v = parseFloat(el.value);
      if (isFinite(v)) opts.write(v);
      el.blur();
      draw();
    } else if (e.key === "Escape") {
      e.preventDefault();
      el.blur();
      _syncCardFields();
    }
  });

  el.addEventListener("blur", () => {
    const v = parseFloat(el.value);
    if (isFinite(v)) opts.write(v);
    _syncCardFields();
    draw();
  });
}

/* ==========================================================================
   FILE LIST
   ========================================================================== */

function syncFileList() {
  const list = document.getElementById("fileList");
  if (!list) return;
  list.innerHTML = "";

  if (board.files.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no files yet — click + Add files… or Retrieve open";
    list.appendChild(e);
    return;
  }

  for (let i = 0; i < board.files.length; i++) {
    const f = board.files[i];
    const state = fileState(f);
    const isSel = (i === board.selectedIdx);

    const row = document.createElement("div");
    row.className = "fileRow"
      + (isSel ? " selected" : "")
      + (state === "closed"  ? " closed"  : "")
      + (state === "missing" ? " missing" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "fileSwatch";
    const hue = f.color || board.palette[0];
    sw.style.background = _rgba(hue, isSel ? 0.95 : 0.55);
    sw.style.border     = "1px solid " + _rgba(hue, isSel ? 1.0 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("span");
    lb.className = "fileLabel";
    lb.textContent = f.name;
    lb.title = f.path;
    row.appendChild(lb);

    const st = document.createElement("span");
    st.className = "fileState " + state;
    if (state === "open")        st.textContent = "●";
    else if (state === "closed") st.textContent = "○";
    else                          st.textContent = "✗";
    st.title = (state === "open")
      ? (f.modified ? "open in Krita (unsaved changes)" : "open in Krita")
      : (state === "closed" ? "not open in Krita" : "file missing on disk");
    row.appendChild(st);

    if (state === "closed") {
      const openBtn = document.createElement("button");
      openBtn.className = "fileAct open";
      openBtn.textContent = "Open";
      openBtn.title = "Open this file in Krita";
      openBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const ok = await openFileInKrita(f);
        if (ok) await refreshFromKrita();
      });
      row.appendChild(openBtn);
    }

    const del = document.createElement("button");
    del.className = "fileDel";
    del.textContent = "×";
    del.title = "Remove from the board (does not close it in Krita)";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      removeFile(i);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      board.selectedIdx = i;
      bringToTop(i);
      syncFileList();
      draw();
    });

    list.appendChild(row);
  }
}

/* ==========================================================================
   STATUS FLASH
   ========================================================================== */

let _statusTimer = null;
function flashStatus(msg, cls) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.className = cls || "";
  if (_statusTimer) clearTimeout(_statusTimer);
  _statusTimer = setTimeout(() => {
    _statusTimer = null;
    updateStatus();
  }, 1800);
}

/* ==========================================================================
   BINDINGS
   ========================================================================== */

(function installPanel() {
  const refreshBtn  = document.getElementById("refreshBtn");
  const addBtn      = document.getElementById("addFilesBtn");
  const retrieveBtn = document.getElementById("retrieveBtn");
  const openAllBtn  = document.getElementById("openAllBtn");
  const projectBtn  = document.getElementById("projectBtn");
  const gridBtn     = document.getElementById("gridBtn");
  const fitBtn      = document.getElementById("fitBtn");
  const resetBtn    = document.getElementById("resetBtn");
  const clearBtn    = document.getElementById("clearBtn");
  const saveBtn     = document.getElementById("saveSceneBtn");
  const loadBtn     = document.getElementById("loadSceneBtn");
  const slider      = document.getElementById("cardWSlider");
  const scaleInput  = document.getElementById("cardScaleVal");
  const rotInput    = document.getElementById("cardRotVal");

  if (refreshBtn)  refreshBtn.addEventListener("click", refreshFromKrita);
  if (addBtn)      addBtn.addEventListener("click", pickFilesViaServer);
  if (retrieveBtn) retrieveBtn.addEventListener("click", retrieveOpenFromKrita);
  if (openAllBtn)  openAllBtn.addEventListener("click", openAllMissing);

  if (projectBtn) projectBtn.addEventListener("click", () => {
    console.log("[panel] project button clicked");
    try {
      projectSelectedOntoOverlaps();
    } catch (err) {
      console.error("[panel] project handler threw:", err);
      flashStatus("project error: " + err.message, "bad");
    }
  });

  if (gridBtn) gridBtn.addEventListener("click", () => {
    arrangeGrid();
    draw();
    flashStatus("arranged " + board.files.length + " cards", "ok");
  });

  if (fitBtn) fitBtn.addEventListener("click", fitAll);

  if (resetBtn) resetBtn.addEventListener("click", () => {
    view.zoom = 1;
    view.panX = window.innerWidth  / 2;
    view.panY = window.innerHeight / 2;
    draw();
  });

  if (clearBtn) clearBtn.addEventListener("click", () => {
    if (board.files.length === 0) return;
    board.files = [];
    board.selectedIdx = -1;
    syncFileList();
    draw();
    flashStatus("board cleared", "ok");
  });

  if (saveBtn) saveBtn.addEventListener("click", saveBoardJSON);
  if (loadBtn) loadBtn.addEventListener("click", promptLoadBoard);

  if (slider) slider.addEventListener("input", () => {
    const v = parseInt(slider.value, 10);
    if (!isFinite(v) || v <= 0) return;
    const oldDefault = board.defaultWidth;
    if (oldDefault <= 0) {
      board.defaultWidth = v;
    } else {
      const factor = v / oldDefault;
      for (const f of board.files) {
        f.w *= factor;
        f.h *= factor;
      }
      board.defaultWidth = v;
    }
    updatePanelLabels();
    draw();
  });

  if (scaleInput) _installScrubInput(scaleInput, {
    rate:     SCALE_SCRUB_RATE,
    snapStep: SCALE_SNAP_STEP,
    read: () => {
      const f = selectedFile();
      if (!f) return 1;
      const denom = board.defaultWidth > 0 ? board.defaultWidth : 1;
      return f.w / denom;
    },
    write: (raw) => {
      const f = selectedFile();
      if (!f) return;
      const clamped = Math.max(0.05, Math.min(20, raw));
      const oldScale = f.w / board.defaultWidth;
      if (oldScale <= 0) return;
      const factor = clamped / oldScale;
      f.w *= factor;
      f.h *= factor;
    },
  });

  if (rotInput) _installScrubInput(rotInput, {
    rate:     ROTATION_SCRUB_RATE,
    snapStep: ROTATION_SNAP_STEP,
    read: () => {
      const f = selectedFile();
      if (!f) return 0;
      return f.rotation * 180 / Math.PI;
    },
    write: (raw) => {
      const f = selectedFile();
      if (!f) return;
      f.rotation = raw * Math.PI / 180;
    },
  });
})();
"""
