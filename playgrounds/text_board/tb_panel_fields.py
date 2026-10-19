"""
tb_panel_fields.py — editor semantics, the per-item debounce buffer,
and the card-width slider's DOM sync.

This is the file the buffered-editor refactor touched most: it holds
the code that replaces the "Apply changes" button.  Every editor
field applies on commit (blur / Enter), the wire sees the change on
the next frame, and the Krita round-trip is batched per item.

THE DEBOUNCE RULE
=================
Every edit through `_applyItemField` does three things:

    1. Mutates board.items[i][key] immediately.
    2. Queues a single-key patch into `_pendingTextEdits` keyed by
       item.id, and resets that item's 250ms timer.  When the timer
       fires, one POST goes to /krita/patch_text carrying every
       merged edit for that item.
    3. Calls pgkitNotifyLocal("item.<key>").  The wire therefore sees
       the change within one animation frame — before the Krita
       round-trip even starts.

The refresh that follows the patch is debounced globally: the first
timer to fire wins, and all edits made in the last 250ms share one
refresh.  See `_scheduleGlobalRefresh`.

SLIDER DOM SYNC
===============
`syncPanelSliders` and `updatePanelLabels` keep the card-width
slider and its numeric label in agreement with `board.defaultWidth`.
They are called by `pgkitBootSync` at startup and after any scale
change.  These functions were dropped in an earlier revision of the
migration split; the boot script's very first call to
`syncPanelSliders()` was throwing ReferenceError, which aborted the
rest of the boot and left the canvas and item list empty.

EDITOR SYNC — DOM-WRITE GUARDS
==============================
`_syncEditor` runs on every field change, every selection change,
and every refresh.  It used to look up all six editor elements with
`document.getElementById` each call and assign to `.value` /
`.disabled` unconditionally.

Two fixes, both local:

    · Element references are cached the first time `_syncEditor`
      runs, in `_editorEls`.  They do not need invalidation — the
      panel DOM is not rebuilt at runtime.

    · Each write is guarded by an equality check: if the DOM
      already holds the target value, the assignment is skipped.
      During a field edit, the just-edited input already has the
      user's typed value, so `_syncEditor` no longer re-writes it
      (which would steal the cursor).

The visual result is identical; the DOM mutation count per commit
drops from O(6) to O(1) — the one field that actually moved.
"""

TB_PANEL_FIELDS_JS = r"""
/* ==========================================================================
   SLIDER SYNC
   ==========================================================================
   Called at boot (via pgkitBootSync) and after any scale change.
   The slider value and its numeric label must match `board.defaultWidth`. */

function syncPanelSliders() {
  const s = document.getElementById("cardWSlider");
  if (s) s.value = board.defaultWidth;
  updatePanelLabels();
}

function updatePanelLabels() {
  const v = document.getElementById("cardWVal");
  if (v) v.textContent = String(board.defaultWidth);
}

/* ==========================================================================
   DEBOUNCE BUFFER
   ========================================================================== */

const TEXT_DEBOUNCE_MS = 250;
const _pendingTextEdits = new Map();
let   _globalRefreshTimer = null;

function _queueItemTextPatch(itemId, key, value) {
  let entry = _pendingTextEdits.get(itemId);
  if (!entry) {
    entry = { patch: {}, timer: null };
    _pendingTextEdits.set(itemId, entry);
  }
  entry.patch[key] = value;
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = setTimeout(() => _flushItemTextEdits(itemId), TEXT_DEBOUNCE_MS);
}

async function _flushItemTextEdits(itemId) {
  const entry = _pendingTextEdits.get(itemId);
  if (!entry) return;
  _pendingTextEdits.delete(itemId);

  const it = board.items.find(i => i.id === itemId);
  if (!it) return;
  if (Object.keys(entry.patch).length === 0) return;

  try {
    const res = await fetch("/krita/patch_text", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        document_path: it.documentPath,
        layer_name:    it.layerName,
        text_index:    it.textIndex,
        patch:         entry.patch,
      }),
    });
    const payload = await res.json();
    if (!payload || !payload.success) {
      reportError("patch", (payload && payload.message) || "patch_text failed");
      return;
    }
  } catch (e) {
    reportError("patch", e.message);
    return;
  }
  _scheduleGlobalRefresh();
}

function _scheduleGlobalRefresh() {
  if (_globalRefreshTimer) clearTimeout(_globalRefreshTimer);
  _globalRefreshTimer = setTimeout(() => {
    _globalRefreshTimer = null;
    refreshFromKrita();
  }, TEXT_DEBOUNCE_MS);
}

/* ==========================================================================
   UNIFIED ITEM FIELD APPLIER
   ==========================================================================
   Used from both the editor's onchange handler and the field's
   `set` (which the remote client calls when a patch arrives).
   pgkitNotifyLocal is safe on the remote path because pgkit's
   `_pgkitApplyingRemote` guard returns early.  The Krita patch is
   still queued: a remote edit is a real edit of the shape. */

function _applyItemField(item, key, value) {
  item[key] = value;
  _queueItemTextPatch(item.id, key, value);
  pgkitNotifyLocal("item." + key);
  _syncEditor();
  draw();
}

/* ==========================================================================
   EDITOR SYNC (DOM ← MODEL)
   ==========================================================================
   Push the selected item's current values into the editor inputs.
   Called on every draw and after every selection change.

   Element references are cached lazily on the first call (they never
   change).  Every write is equality-guarded: if the DOM already
   holds the target value, no assignment happens.  See the module
   docstring, EDITOR SYNC — DOM-WRITE GUARDS. */

let _editorEls = null;

function _getEditorEls() {
  if (_editorEls) return _editorEls;
  _editorEls = {
    text:  document.getElementById("editText"),
    font:  document.getElementById("editFont"),
    size:  document.getElementById("editSize"),
    color: document.getElementById("editColor"),
    align: document.getElementById("editAlign"),
    rot:   document.getElementById("editRot"),
    sel:   document.getElementById("selLabel"),
  };
  return _editorEls;
}

function _syncEditor() {
  const els = _getEditorEls();
  const it  = selectedItem();

  if (!it) {
    if (els.text)  { els.text.disabled = true;  if (els.text.value  !== "") els.text.value  = ""; }
    if (els.font)  { els.font.disabled = true;  if (els.font.value  !== "") els.font.value  = ""; }
    if (els.size)  { els.size.disabled = true;  if (els.size.value  !== "") els.size.value  = ""; }
    if (els.color) { els.color.disabled = true; if (els.color.value !== "") els.color.value = ""; }
    if (els.align) { els.align.disabled = true; if (els.align.value !== "left") els.align.value = "left"; }
    if (els.rot)   { els.rot.disabled = true;   if (els.rot.value   !== "") els.rot.value   = ""; }
    if (els.sel)   { if (els.sel.textContent !== "\u2014") els.sel.textContent = "\u2014"; }
    return;
  }

  const textTarget  = it.text || "";
  const fontTarget  = it.fontFamily || "";
  const sizeTarget  = String(it.fontSize || "");
  const colorTarget = it.color || "";
  const alignTarget = it.alignment || "left";
  const rotTarget   = (it.rotationDeg || 0).toFixed(2);

  if (els.text) {
    els.text.disabled = false;
    if (document.activeElement !== els.text && els.text.value !== textTarget) {
      els.text.value = textTarget;
    }
  }
  if (els.font) {
    els.font.disabled = false;
    if (document.activeElement !== els.font && els.font.value !== fontTarget) {
      els.font.value = fontTarget;
    }
  }
  if (els.size) {
    els.size.disabled = false;
    if (document.activeElement !== els.size && els.size.value !== sizeTarget) {
      els.size.value = sizeTarget;
    }
  }
  if (els.color) {
    els.color.disabled = false;
    if (document.activeElement !== els.color && els.color.value !== colorTarget) {
      els.color.value = colorTarget;
    }
  }
  if (els.align) {
    els.align.disabled = false;
    if (els.align.value !== alignTarget) els.align.value = alignTarget;
  }
  if (els.rot) {
    els.rot.disabled = false;
    if (document.activeElement !== els.rot && els.rot.value !== rotTarget) {
      els.rot.value = rotTarget;
    }
  }

  const selTarget = (it.layerName || "?") + " \u00b7 idx " + it.textIndex;
  if (els.sel && els.sel.textContent !== selTarget) {
    els.sel.textContent = selTarget;
  }
}

/* ==========================================================================
   EDITOR BINDINGS (user edit → MODEL)
   ==========================================================================
   Every editor field applies on commit: onchange fires on blur or
   Enter, which is the same contract every scrub input honours.  The
   Apply button has been removed. */

function _installEditorBindings() {
  const els = _getEditorEls();
  const it  = () => selectedItem();

  if (els.text) els.text.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.text.value;
    if (v === item.text) return;
    _applyItemField(item, "text", v);
  });

  if (els.font) els.font.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.font.value.trim();
    if (!v || v === item.fontFamily) return;
    _applyItemField(item, "fontFamily", v);
  });

  if (els.size) els.size.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = parseFloat(els.size.value);
    if (!isFinite(v) || v === item.fontSize) return;
    _applyItemField(item, "fontSize", v);
  });

  if (els.color) els.color.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.color.value.trim();
    if (!v || v === item.color) return;
    _applyItemField(item, "color", v);
  });

  if (els.align) els.align.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.align.value;
    if (!v || v === item.alignment) return;
    _applyItemField(item, "alignment", v);
  });

  if (els.rot) els.rot.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = parseFloat(els.rot.value);
    if (!isFinite(v) || Math.abs(v - item.rotationDeg) < 0.01) return;
    _applyItemField(item, "rotationDeg", v);
  });
}

/* Install once, after the DOM is parsed. */
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _installEditorBindings);
} else {
  _installEditorBindings();
}
"""
