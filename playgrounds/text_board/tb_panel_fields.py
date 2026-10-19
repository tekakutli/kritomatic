"""
tb_panel_fields.py — editor semantics and the per-item debounce buffer.

Exports: installEditorBindings, _syncEditor, applyItemField,
syncPanelSliders, updatePanelLabels.
"""

TB_PANEL_FIELDS_JS = r"""
import { board, itemId } from "./tb_core.js";
import { pgkitNotifyLocal, pgkitNotifyEntities } from "./pgkit/remote_client.js";

/* ==========================================================================
   SLIDER SYNC
   ========================================================================== */

export function syncPanelSliders() {
  const s = document.getElementById("cardWSlider");
  if (s) s.value = board.defaultWidth;
  updatePanelLabels();
}

export function updatePanelLabels() {
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
      console.error("[patch]", (payload && payload.message) || "patch_text failed");
      return;
    }
  } catch (e) {
    console.error("[patch]", e.message);
    return;
  }
  _scheduleGlobalRefresh();
}

function _scheduleGlobalRefresh() {
  if (_globalRefreshTimer) clearTimeout(_globalRefreshTimer);
  _globalRefreshTimer = setTimeout(() => {
    _globalRefreshTimer = null;
    // Deferred to avoid a static import cycle with tb_krita.
    import("./tb_krita.js").then(m => m.refreshFromKrita()).catch(() => {});
  }, TEXT_DEBOUNCE_MS);
}

/* ==========================================================================
   UNIFIED ITEM FIELD APPLIER
   ========================================================================== */

export function applyItemField(item, key, value) {
  item[key] = value;
  _queueItemTextPatch(item.id, key, value);
  pgkitNotifyLocal("item." + key);
  _syncEditor();
  // draw is called by the caller (via panel_scrubs set wrappers).
}

/* ==========================================================================
   EDITOR SYNC
   ========================================================================== */

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

export function _syncEditor() {
  const els = _getEditorEls();
  const idx = board.selectedIdx;
  const it  = (idx >= 0 && idx < board.items.length)
              ? board.items[idx] : null;

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
   EDITOR BINDINGS
   ========================================================================== */

export function installEditorBindings() {
  const els = _getEditorEls();
  const it  = () => {
    const idx = board.selectedIdx;
    return (idx >= 0 && idx < board.items.length) ? board.items[idx] : null;
  };

  if (els.text) els.text.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.text.value;
    if (v === item.text) return;
    applyItemField(item, "text", v);
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  });

  if (els.font) els.font.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.font.value.trim();
    if (!v || v === item.fontFamily) return;
    applyItemField(item, "fontFamily", v);
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  });

  if (els.size) els.size.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = parseFloat(els.size.value);
    if (!isFinite(v) || v === item.fontSize) return;
    applyItemField(item, "fontSize", v);
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  });

  if (els.color) els.color.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.color.value.trim();
    if (!v || v === item.color) return;
    applyItemField(item, "color", v);
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  });

  if (els.align) els.align.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = els.align.value;
    if (!v || v === item.alignment) return;
    applyItemField(item, "alignment", v);
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  });

  if (els.rot) els.rot.addEventListener("change", () => {
    const item = it();
    if (!item) return;
    const v = parseFloat(els.rot.value);
    if (!isFinite(v) || Math.abs(v - item.rotationDeg) < 0.01) return;
    applyItemField(item, "rotationDeg", v);
    import("./tb_dispatch.js").then(m => m.draw()).catch(() => {});
  });
}
"""
