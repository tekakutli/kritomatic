"""Panel button handlers, pgkit actions, mask list levels, undo/redo,
and the boot-sync routine.

IMAGE SOURCE
============
The image is pulled exclusively from the daemon via `@thesegment`
(see `pg_autosync.js`).  There is no local-file pick, drop, or
clear-image path.  The two image-related controls in the panel are:

  · **Refresh @thesegment** — bypasses the spec-change gate to
    re-read the pixels behind a constant spec (e.g. the user painted
    on the layer in Krita, but the source/layer picker didn't move).
    Routes through `resolveCanvasSpec` + `fetchAndSetCanvas` so it
    goes through the same registry-first / config-fallback path as
    the poller.

  · **⟲ Rebind doc** — clears the pinned target document and
    re-captures from `activeDocument()`.  Use when another playground
    (e.g. inpaint's default doc) stole `activeDocument()` at boot;
    the daemon now targets the doc the user is actually looking at.

Both are also exposed as pgkit actions (`action.refreshCanvas`,
`action.rebindDoc`) for scripting clients.

UPDATE LOG
==========
Inpainting section and its placeholder button have been removed —
inpainting lives in its own playground (`playgrounds/inpaint`).  Every
other action — export/import, Krita actions, pgkit registrations,
mask-row select/delete, `pgkitHandleSelect`, `segmentBootSync` — is
unchanged."""

PANEL_BINDINGS_JS = r"""
import { board } from "./pg_state.js";
import {
  setMasks, addMaskFromB64, restoreMasks, replaceMaskAt,
  selectedMask, selectedMaskIdx,
  clearPointsOnSelectedMask, clearStagingPoints,
  clearMaskSelection, replaceMaskSelection,
} from "./pg_masks.js";
import {
  snapshotRefine, snapshotSegmentAll,
  pushOp, pushRedo, popOp, popRedo,
} from "./pg_oplog.js";
import { clearSelectedMaskRegion } from "./pg_region.js";
import { compositeRefineWithRegion } from "./pg_composite.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { runSegmentAll, runRefine,
         interruptSegmentation, fetchStatus }
  from "./pg_comfy_client.js";
import { checkKritaStatus, applyToKrita, extractMaskToLayer,
         exportMaskAsLayer, importKritaMasks, fetchActiveState }
  from "./pg_krita_client.js";
import { resolveCanvasSpec, fetchAndSetCanvas } from "./pg_autosync.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ============================================================
   Manual @thesegment refresh
   ============================================================ */

async function _refreshCanvasAction() {
  flashStatus("refreshing @thesegment\u2026", "");
  let state;
  try {
    state = await fetchActiveState();
  } catch (e) {
    flashStatus("@thesegment refresh failed: " + e.message, "bad");
    return;
  }
  if (!state || !state.success) {
    flashStatus(
      "@thesegment refresh failed: "
        + ((state && state.message) || "no active state"),
      "bad",
    );
    return;
  }

  // Same registry-first / config-fallback spec resolution the
  // autosync poller uses, so the manual button and the automatic
  // path can never diverge.
  const spec = resolveCanvasSpec(state.data || null);
  if (!spec) {
    flashStatus(
      "@thesegment spec not set \u2014 pick a source in the docker",
      "warn",
    );
    return;
  }

  await fetchAndSetCanvas(spec);
}

/* ============================================================
   Rebind target document
   ============================================================ */

async function _rebindDocAction() {
  flashStatus("rebinding target document\u2026", "");
  try {
    const res = await fetch("/rebind_document", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!data.success) {
      flashStatus(
        "rebind failed: " + (data.message || "unknown"),
        "bad",
      );
      return;
    }
    flashStatus(data.message || "rebound", "ok");
    await _refreshCanvasAction();
  } catch (e) {
    flashStatus("rebind failed: " + e.message, "bad");
  }
}

/* ============================================================
   Segment All
   ============================================================ */

export async function runSegmentAllAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;

  const snapshot = snapshotSegmentAll();
  flashStatus("detecting\u2026", "");

  const payload = await runSegmentAll();
  if (!payload) return;

  pushOp(snapshot);
  setMasks(payload.masks);
  board.stagingPoints = [];

  flashStatus(
    "detected " + payload.masks.length + " object(s) in "
    + payload.timing.toFixed(2) + "s", "ok",
  );
  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Refine (region-constrained when a region exists)
   ============================================================ */

async function _unionMasksB64(b64List) {
  if (b64List.length === 0) return null;
  if (b64List.length === 1) return b64List[0];
  const imgs = await Promise.all(b64List.map(b64 => new Promise((res, rej) => {
    const i = new Image();
    i.onload = () => res(i);
    i.onerror = rej;
    i.src = "data:image/png;base64," + b64;
  })));
  const W = imgs[0].width, H = imgs[0].height;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  cx.globalCompositeOperation = "lighter";
  for (const im of imgs) cx.drawImage(im, 0, 0, W, H);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

export async function runRefineAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;

  const maskIdx = selectedMaskIdx();
  if (maskIdx < 0) {
    flashStatus("Select a single mask to refine", "warn");
    return;
  }
  const mask = board.masks[maskIdx];
  if (!mask || !mask.loaded) {
    flashStatus("Mask not loaded yet", "warn");
    return;
  }

  const snapshot = snapshotRefine(maskIdx);
  const modelLabel = board.refineModel.toUpperCase();
  const regionSuffix = mask.regionCanvas ? " (region)" : "";
  flashStatus(
    "refining mask #" + (maskIdx + 1)
    + " via " + modelLabel + regionSuffix + "\u2026", "");

  const payload = await runRefine(maskIdx);
  if (!payload) return;

  pushOp(snapshot);

  try {
    let combinedB64 = await _unionMasksB64(payload.masks);
    if (!combinedB64) {
      flashStatus("refine returned no mask", "warn");
      popOp();
      return;
    }

    if (mask.regionCanvas) {
      try {
        combinedB64 = await compositeRefineWithRegion(
          snapshot.prevB64, combinedB64, mask.regionCanvas,
        );
      } catch (e) {
        flashStatus(
          "region composite failed: " + e.message
          + " (used full refine)", "warn");
      }
    }

    replaceMaskAt(maskIdx, combinedB64, mask.points);
    const backend = payload.backend || modelLabel.toLowerCase();
    flashStatus(
      "refined mask #" + (maskIdx + 1)
      + " via " + backend
      + (mask.regionCanvas ? " (region)" : "")
      + " in " + payload.timing.toFixed(2) + "s", "ok",
    );
  } catch (e) {
    flashStatus("refine failed: " + e.message, "bad");
    popOp();
  }

  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Undo / redo
   ============================================================
   Undo pops the "before" state off opLog, captures the current
   "after" state into redoLog, and applies the "before".
   Redo pops the "after" state off redoLog, captures the current
   "before" state back into opLog (without clearing redoLog, so a
   second redo can follow), and applies the "after".

   pushOp's default clearRedo=true invalidates the redo history on
   every new action, which is the standard contract. The redo path
   passes clearRedo=false so this "undo the redo" op leaves the
   remaining redo history intact. */

function _captureCurrentRefine(maskIdx) {
  const m = board.masks[maskIdx];
  if (!m) return null;
  return {
    type: 'refine',
    maskIdx,
    prevB64: m.b64,
    points: m.points.map(p => ({ x: p.x, y: p.y, isPositive: p.isPositive })),
  };
}

function _captureCurrentSegmentAll() {
  return {
    type: 'segmentAll',
    prevMasks: board.masks.map(m => ({
      b64: m.b64,
      points: m.points.map(p => ({ x: p.x, y: p.y, isPositive: p.isPositive })),
      textPrompt: m.textPrompt || "",
    })),
    prevSelection: board.selectedMaskIndices.slice(),
    prevStagingPoints: board.stagingPoints.map(p =>
      ({ x: p.x, y: p.y, isPositive: p.isPositive })),
  };
}

export async function undoAction() {
  if (board.running) return;
  const op = popOp();
  if (!op) { flashStatus("nothing to undo", "warn"); return; }

  if (op.type === 'refine') {
    const after = _captureCurrentRefine(op.maskIdx);
    if (after) pushRedo(after);
    replaceMaskAt(op.maskIdx, op.prevB64);
    flashStatus("undid mask #" + (op.maskIdx + 1) + " edit", "ok");
  } else if (op.type === 'segmentAll') {
    const after = _captureCurrentSegmentAll();
    pushRedo(after);
    restoreMasks(op.prevMasks, op.prevSelection);
    if (op.prevStagingPoints) {
      board.stagingPoints = op.prevStagingPoints.map(p =>
        ({ x: p.x, y: p.y, isPositive: p.isPositive }));
    }
    flashStatus("undid Segment All", "ok");
  }

  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

export async function redoAction() {
  if (board.running) return;
  const op = popRedo();
  if (!op) { flashStatus("nothing to redo", "warn"); return; }

  if (op.type === 'refine') {
    const before = _captureCurrentRefine(op.maskIdx);
    // clearRedo=false keeps the rest of the redo history intact so a
    // second redo can follow without re-pushing.
    if (before) pushOp(before, false);
    replaceMaskAt(op.maskIdx, op.prevB64);
    flashStatus("redid mask #" + (op.maskIdx + 1) + " edit", "ok");
  } else if (op.type === 'segmentAll') {
    const before = _captureCurrentSegmentAll();
    pushOp(before, false);
    restoreMasks(op.prevMasks, op.prevSelection);
    if (op.prevStagingPoints) {
      board.stagingPoints = op.prevStagingPoints.map(p =>
        ({ x: p.x, y: p.y, isPositive: p.isPositive }));
    }
    flashStatus("redid Segment All", "ok");
  }

  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Misc actions
   ============================================================ */

async function _cancelAction() {
  if (!board.running) return;
  await interruptSegmentation();
  flashStatus("cancel requested", "warn");
}

function _clearPointsAction() {
  const changed = selectedMask()
    ? clearPointsOnSelectedMask()
    : clearStagingPoints();
  if (!changed) return;
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _clearRegionAction() {
  if (!clearSelectedMaskRegion()) return;
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _clearMasksAction() {
  setMasks([]);
  board.opLog = [];
  board.redoLog = [];
  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _checkStatusAction() {
  const s = await fetchStatus();
  if (!s.reachable) {
    flashStatus("ComfyUI unreachable: " + (s.message || ""), "bad");
    return;
  }
  const parts = [
    "ComfyUI \u2713",
    "SAM3 " + (s.has_sam3 ? "\u2713" : "\u2717"),
    "SAM2 " + (s.has_sam2 ? "\u2713" : "\u2717"),
  ];
  flashStatus(parts.join(" \u00b7 "),
              s.has_sam3 && s.has_sam2 ? "ok" : "warn");
}

/* ============================================================
   Export options helpers
   ============================================================ */

function _getExportToKrita() {
  const el = document.getElementById("exportToKrita");
  return el ? !!el.checked : true;
}

function _getExportPositionArg() {
  const atCurrent = document.getElementById("exportAtCurrentLayer");
  const useCurrent = atCurrent ? !!atCurrent.checked : true;
  if (!useCurrent) return "";

  const targetEl = document.getElementById("kritaTargetLayer");
  const targetName = targetEl ? (targetEl.value || "").trim() : "";
  return targetName || "@active";
}

/* ============================================================
   Export (checkbox-aware)
   ============================================================ */

function _downloadDataUrl(dataUrl, filename) {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function _exportAction() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) { flashStatus("no masks selected", "warn"); return; }

  if (_getExportToKrita()) {
    const aboveLayer = _getExportPositionArg();
    const edge = _getKritaEdgeOptions();
    flashStatus("exporting " + indices.length + " mask(s) to Krita\u2026", "");
    let ok = 0, failed = 0;
    let firstError = "";
    let lastLayer = "";
    for (const i of indices) {
      const m = board.masks[i];
      if (!m || !m.b64) continue;
      try {
        const res = await exportMaskAsLayer({
          maskB64: m.b64,
          name: "Mask_" + (i + 1),
          aboveLayer: aboveLayer,
          invert: edge.invert,
          expandPixels: edge.expandPixels,
          contractPixels: edge.contractPixels,
          smoothIterations: edge.smoothIterations,
          featherRadius: edge.featherRadius,
        });
        if (res && res.success) {
          ok += 1;
          const nm = (res.data || {}).layer_name;
          if (nm) lastLayer = nm;
        } else {
          failed += 1;
          if (!firstError) firstError = (res && res.message) || "unknown";
        }
      } catch (e) {
        failed += 1;
        if (!firstError) firstError = e.message;
      }
    }
    if (ok === 0) {
      flashStatus("export failed: " + firstError, "bad");
      if (/unreachable|connect|refused|timeout/i.test(firstError)) {
        _kritaMarkUnreachable(firstError);
      }
      return;
    }
    let msg = "exported " + ok + " mask layer(s) to Krita";
    if (failed) msg += " (" + failed + " failed: " + firstError + ")";
    flashStatus(msg, failed ? "warn" : "ok");
    _kritaMarkReachable();
    return;
  }

  // Legacy: save as PNG files.
  let count = 0;
  for (const i of indices) {
    const m = board.masks[i];
    if (!m || !m.b64) continue;
    _downloadDataUrl(
      "data:image/png;base64," + m.b64,
      "mask_" + String(i + 1).padStart(2, "0") + ".png",
    );
    count += 1;
    await _sleep(120);
  }
  flashStatus("exported " + count + " mask file(s)", "ok");
}

async function _exportCombinedAction() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) { flashStatus("no masks selected", "warn"); return; }
  const first = board.masks[indices[0]];
  if (!first || !first.img) { flashStatus("masks not loaded yet", "warn"); return; }
  const W = first.img.width, H = first.img.height;
  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  octx.fillStyle = "black";
  octx.fillRect(0, 0, W, H);
  octx.globalCompositeOperation = "lighter";
  for (const i of indices) {
    const m = board.masks[i];
    if (!m || !m.img) continue;
    octx.drawImage(m.img, 0, 0, W, H);
  }
  const combinedB64 = off.toDataURL("image/png")
                          .replace(/^data:image\/png;base64,/, "");

  if (_getExportToKrita()) {
    const aboveLayer = _getExportPositionArg();
    const edge = _getKritaEdgeOptions();
    flashStatus("exporting combined mask to Krita\u2026", "");
    try {
      const res = await exportMaskAsLayer({
        maskB64: combinedB64,
        name: "Combined_Mask",
        aboveLayer: aboveLayer,
        invert: edge.invert,
        expandPixels: edge.expandPixels,
        contractPixels: edge.contractPixels,
        smoothIterations: edge.smoothIterations,
        featherRadius: edge.featherRadius,
      });
      if (res && res.success) {
        const nm = (res.data || {}).layer_name || "Combined_Mask";
        flashStatus("exported combined mask \u2192 " + nm, "ok");
        _kritaMarkReachable();
      } else {
        const msg = (res && res.message) || "unknown";
        flashStatus("export failed: " + msg, "bad");
        if (/unreachable|connect|refused|timeout/i.test(msg)) {
          _kritaMarkUnreachable(msg);
        }
      }
    } catch (e) {
      flashStatus("export failed: " + e.message, "bad");
    }
    return;
  }

  _downloadDataUrl(off.toDataURL("image/png"), "masks_combined.png");
  flashStatus("exported combined file (" + indices.length + " mask(s))", "ok");
}

/* ============================================================
   Export to Krita (specific modes)
   ============================================================ */

function _kritaMarkReachable() {
  const el = document.getElementById("kritaStatus");
  if (!el) return;
  el.textContent = "\u2713 reachable";
  el.className = "ok";
  el.title = "Kritomatic daemon at localhost:12346";
}

function _kritaMarkUnreachable(reason) {
  const el = document.getElementById("kritaStatus");
  if (!el) return;
  el.textContent = "\u2717 " + (reason || "not reachable");
  el.className = "bad";
  el.title = "Start Krita with the Kritomatic Daemon plugin enabled";
  try {
    console.error("[segment] krita:", reason || "not reachable");
  } catch (_) {}
}

async function _kritaStatusRefresh() {
  const s = await checkKritaStatus();
  if (s.reachable) _kritaMarkReachable();
  else             _kritaMarkUnreachable(s.message || "not reachable");
}

function _kritaGetTargetLayer() {
  const el = document.getElementById("kritaTargetLayer");
  return el ? (el.value || "").trim() : "";
}

function _getKritaSelMode() {
  const el = document.getElementById("kritaSelMode");
  return el ? el.value : "replace";
}

function _getKritaEdgeOptions() {
  const featherEl  = document.getElementById("kritaFeather");
  const expandEl   = document.getElementById("kritaExpand");
  const contractEl = document.getElementById("kritaContract");
  const smoothEl   = document.getElementById("kritaSmooth");
  const invertEl   = document.getElementById("kritaInvert");

  const readInt = (el, dflt) => {
    if (!el) return dflt;
    const n = parseInt(el.value, 10);
    return isFinite(n) ? Math.max(0, n) : dflt;
  };

  return {
    featherRadius:    readInt(featherEl, 0),
    expandPixels:     readInt(expandEl, 0),
    contractPixels:   readInt(contractEl, 0),
    smoothIterations: readInt(smoothEl, 0),
    invert:           invertEl ? !!invertEl.checked : false,
  };
}

function _getKritaExtractOptions() {
  const blendEl    = document.getElementById("kritaBlendMode");
  const opacityEl  = document.getElementById("kritaOpacity");
  const opEnableEl = document.getElementById("kritaOpacityEnabled");
  const groupEl    = document.getElementById("kritaGroup");

  const blendMode = blendEl ? (blendEl.value || "") : "";

  let opacity = "";
  if (opEnableEl && opEnableEl.checked && opacityEl) {
    const n = parseInt(opacityEl.value, 10);
    if (isFinite(n)) {
      const pct = Math.max(0, Math.min(100, n));
      opacity = String(Math.round(pct * 255 / 100));
    }
  }

  const groupName = groupEl ? (groupEl.value || "").trim() : "";

  return { blendMode, opacity, groupName };
}

async function _kritaExportSelection() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) {
    flashStatus("no masks selected", "warn");
    return;
  }
  const b64s = indices.map(i => board.masks[i].b64);
  const unionB64 = await _unionMasksB64(b64s);
  if (!unionB64) {
    flashStatus("could not combine masks", "bad");
    return;
  }
  const selMode = _getKritaSelMode();
  const edge = _getKritaEdgeOptions();
  flashStatus("sending selection to Krita\u2026", "");
  const res = await applyToKrita({
    maskB64: unionB64,
    mode: "selection",
    selMode: selMode,
    invert: edge.invert,
    expandPixels: edge.expandPixels,
    contractPixels: edge.contractPixels,
    smoothIterations: edge.smoothIterations,
    featherRadius: edge.featherRadius,
  });
  if (res.success) {
    flashStatus("selection applied to Krita (" + selMode + ")", "ok");
    _kritaMarkReachable();
  } else {
    flashStatus("failed: " + (res.message || "unknown"), "bad");
    if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
      _kritaMarkUnreachable(res.message);
    }
  }
}

async function _kritaExportTransparency() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) {
    flashStatus("no masks selected", "warn");
    return;
  }
  const b64s = indices.map(i => board.masks[i].b64);
  const unionB64 = await _unionMasksB64(b64s);
  if (!unionB64) {
    flashStatus("could not combine masks", "bad");
    return;
  }
  const edge = _getKritaEdgeOptions();
  flashStatus("sending transparency mask to Krita\u2026", "");
  const res = await applyToKrita({
    maskB64: unionB64,
    mode: "transparency",
    layerName: _kritaGetTargetLayer(),
    invert: edge.invert,
    expandPixels: edge.expandPixels,
    contractPixels: edge.contractPixels,
    smoothIterations: edge.smoothIterations,
    featherRadius: edge.featherRadius,
  });
  if (res.success) {
    const d = res.data || {};
    const target = d.layer_name ? (" on " + d.layer_name) : "";
    flashStatus("transparency mask applied" + target, "ok");
    _kritaMarkReachable();
  } else {
    flashStatus("failed: " + (res.message || "unknown"), "bad");
    if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
      _kritaMarkUnreachable(res.message);
    }
  }
}

async function _kritaExportExtract(mode) {
  // mode is 'extract_new_layer' or 'extract_duplicate'
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) {
    flashStatus("no masks selected", "warn");
    return;
  }
  const b64s = indices.map(i => board.masks[i].b64);
  const unionB64 = await _unionMasksB64(b64s);
  if (!unionB64) {
    flashStatus("could not combine masks", "bad");
    return;
  }
  const edge = _getKritaEdgeOptions();
  const extra = _getKritaExtractOptions();
  const isNewLayer = (mode === "extract_new_layer");
  const verb = isNewLayer ? "extracting to new layer" : "duplicating with mask";
  flashStatus(verb + "\u2026", "");
  const res = await extractMaskToLayer({
    maskB64: unionB64,
    mode: mode,
    layerName: _kritaGetTargetLayer(),
    newLayerName: "",
    blendMode: extra.blendMode,
    opacity: extra.opacity,
    groupName: extra.groupName,
    invert: edge.invert,
    expandPixels: edge.expandPixels,
    contractPixels: edge.contractPixels,
    smoothIterations: edge.smoothIterations,
    featherRadius: edge.featherRadius,
  });
  if (res.success) {
    const d = res.data || {};
    const nameSuffix = d.new_layer ? (" \u2192 " + d.new_layer) : "";
    const warnings = (d.post_warnings || []).join("; ");
    flashStatus(
      (isNewLayer ? "extracted" : "duplicated") + nameSuffix +
      (warnings ? " \u2014 " + warnings : ""),
      warnings ? "warn" : "ok"
    );
    _kritaMarkReachable();
  } else {
    flashStatus("failed: " + (res.message || "unknown"), "bad");
    if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
      _kritaMarkUnreachable(res.message);
    }
  }
}

/* ============================================================
   Import masks from Krita
   ============================================================ */

async function _kritaImportMasksAction() {
  flashStatus("importing masks from Krita\u2026", "");
  try {
    const res = await importKritaMasks({});
    if (!res.success) {
      flashStatus("import failed: " + (res.message || "unknown"), "bad");
      if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
        _kritaMarkUnreachable(res.message);
      }
      return;
    }

    const masks = res.masks || [];
    const skipped = res.skipped || [];
    const selectionPath = res.selection_path || "unknown";

    if (masks.length === 0) {
      const reason = skipped.length
        ? " (" + skipped.map(s => s.name + ": " + s.reason).join("; ") + ")"
        : "";
      flashStatus("no masks to import" + reason, "warn");
      _kritaMarkReachable();
      return;
    }

    let dimWarn = "";
    if (board.imageWidth && board.imageHeight) {
      const mism = masks.find(m =>
        m.width !== board.imageWidth || m.height !== board.imageHeight);
      if (mism) {
        dimWarn = " \u2014 mask '" + mism.name + "' is "
                  + mism.width + "\u00d7" + mism.height
                  + ", image is "
                  + board.imageWidth + "\u00d7" + board.imageHeight
                  + " (will scale)";
      }
    }

    const startIdx = board.masks.length;
    for (const m of masks) {
      addMaskFromB64(m.mask_b64);
    }
    board.selectedMaskIndices = masks.map((_, i) => startIdx + i);

    const skippedMsg = skipped.length
      ? " (" + skipped.length + " skipped)"
      : "";
    const multiMsg = (selectionPath === "active_only" && masks.length === 1)
      ? " (active layer only; multi-select unavailable)"
      : "";
    flashStatus(
      "imported " + masks.length + " mask(s)" + skippedMsg + multiMsg + dimWarn,
      dimWarn || multiMsg ? "warn" : "ok"
    );
    _kritaMarkReachable();

    syncMaskList();
    syncPanelFields();
    pgkitNotifyAll();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  } catch (e) {
    flashStatus("import failed: " + e.message, "bad");
  }
}

/* ============================================================
   Install buttons
   ============================================================ */

export function installPanelBindings() {
  const el = {};
  const ids = [
    "runBtn", "segmentAllBtn", "cancelBtn", "undoBtn", "redoBtn",
    "clearPointsBtn", "clearRegionBtn",
    "kritaImportMasksBtn",
    "refreshCanvasBtn", "kritaCheckBtn", "rebindDocBtn",
    "exportBtn", "exportCombinedBtn",
    "kritaSelectionBtn", "kritaTransparencyBtn",
    "kritaExtractNewLayerBtn", "kritaExtractDuplicateBtn",
  ];
  for (const id of ids) el[id] = document.getElementById(id);

  if (el.runBtn)        el.runBtn.addEventListener("click", runRefineAction);
  if (el.segmentAllBtn) el.segmentAllBtn.addEventListener("click", runSegmentAllAction);
  if (el.cancelBtn)     el.cancelBtn.addEventListener("click", _cancelAction);
  if (el.undoBtn)       el.undoBtn.addEventListener("click", undoAction);
  if (el.redoBtn)       el.redoBtn.addEventListener("click", redoAction);
  if (el.clearPointsBtn) el.clearPointsBtn.addEventListener("click", _clearPointsAction);
  if (el.clearRegionBtn) el.clearRegionBtn.addEventListener("click", _clearRegionAction);
  if (el.kritaImportMasksBtn) el.kritaImportMasksBtn.addEventListener("click", _kritaImportMasksAction);
  if (el.refreshCanvasBtn)    el.refreshCanvasBtn.addEventListener("click", _refreshCanvasAction);
  if (el.kritaCheckBtn)       el.kritaCheckBtn.addEventListener("click", _kritaStatusRefresh);
  if (el.rebindDocBtn)        el.rebindDocBtn.addEventListener("click", _rebindDocAction);
  if (el.exportBtn)     el.exportBtn.addEventListener("click", _exportAction);
  if (el.exportCombinedBtn) el.exportCombinedBtn.addEventListener("click", _exportCombinedAction);

  if (el.kritaSelectionBtn)    el.kritaSelectionBtn.addEventListener("click", _kritaExportSelection);
  if (el.kritaTransparencyBtn) el.kritaTransparencyBtn.addEventListener("click", _kritaExportTransparency);
  if (el.kritaExtractNewLayerBtn) el.kritaExtractNewLayerBtn.addEventListener("click",
    () => _kritaExportExtract("extract_new_layer"));
  if (el.kritaExtractDuplicateBtn) el.kritaExtractDuplicateBtn.addEventListener("click",
    () => _kritaExportExtract("extract_duplicate"));

  const toKritaEl = document.getElementById("exportToKrita");
  const atCurrentEl = document.getElementById("exportAtCurrentLayer");
  if (toKritaEl && atCurrentEl) {
    const sync = () => {
      atCurrentEl.disabled = !toKritaEl.checked;
    };
    sync();
    toKritaEl.addEventListener("change", sync);
  }

  const statusEl = document.getElementById("statusLine");
  if (statusEl) {
    statusEl.style.cursor = "pointer";
    statusEl.title = "Click to re-check ComfyUI";
    statusEl.addEventListener("click", _checkStatusAction);
  }
}

/* ============================================================
   Actions
   ============================================================ */

pgkitRegisterAction("action.run", {
  label: "Refine", scope: "global", run: runRefineAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.segmentAll", {
  label: "Segment All", scope: "global", run: runSegmentAllAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.undo", {
  label: "Undo", scope: "global", run: undoAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.redo", {
  label: "Redo", scope: "global", run: redoAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.cancel", {
  label: "Cancel", scope: "global", run: _cancelAction,
  meta: { variant: "danger" },
});
pgkitRegisterAction("action.clearPoints", {
  label: "Clear points", scope: "global", run: _clearPointsAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.clearRegion", {
  label: "Clear region", scope: "global", run: _clearRegionAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.clearMasks", {
  label: "Clear masks", scope: "global", run: _clearMasksAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.checkStatus", {
  label: "Check connection", scope: "global", run: _checkStatusAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.exportSelected", {
  label: "Export selected", scope: "global", run: _exportAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.exportCombined", {
  label: "Export combined", scope: "global", run: _exportCombinedAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.refreshCanvas", {
  label: "Refresh @thesegment", scope: "global", run: _refreshCanvasAction,
  meta: { variant: "plain",
          description: "Re-fetch the current @thesegment from Krita." },
});
pgkitRegisterAction("action.rebindDoc", {
  label: "Rebind doc", scope: "global", run: _rebindDocAction,
  meta: { variant: "plain",
          description: "Re-pin segment to the currently active Krita document." },
});
pgkitRegisterAction("action.kritaCheck", {
  label: "Check Krita", scope: "global", run: _kritaStatusRefresh,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.kritaImportMasks", {
  label: "Import masks from Krita", scope: "global",
  run: _kritaImportMasksAction,
  meta: { variant: "plain",
          description: "Read the selected mask or paint layer(s) in " +
                       "Krita as base64 masks and append them to the " +
                       "playground mask stack. Multi-selection works on " +
                       "Krita builds that expose the selection via " +
                       "View.selectedNodes / Document.selectedNodes / " +
                       "Node.isSelected; otherwise the active layer is " +
                       "imported." },
});
pgkitRegisterAction("action.kritaSelection", {
  label: "Set as selection", scope: "global", run: _kritaExportSelection,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.kritaTransparency", {
  label: "Add transparency mask", scope: "global", run: _kritaExportTransparency,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.kritaExtractNewLayer", {
  label: "Extract as new layer", scope: "global",
  run: () => _kritaExportExtract("extract_new_layer"),
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.kritaExtractDuplicate", {
  label: "Duplicate with mask", scope: "global",
  run: () => _kritaExportExtract("extract_duplicate"),
  meta: { variant: "primary" },
});

/* ============================================================
   Masks level + row actions
   ============================================================ */

pgkitRegisterLevel("masks", {
  label: "Masks",
  items: () => board.masks.map((m, i) => ({
    name:  "mask." + i,
    label: "Mask " + (i + 1),
    extra: { size: m.w + "\u00d7" + m.h,
             selected: board.selectedMaskIndices.indexOf(i) >= 0,
             points: (m.points || []).length,
             hasRegion: !!m.regionCanvas },
  })),
  selected: () => board.selectedMaskIndices.length > 0
    ? "mask." + board.selectedMaskIndices[0] : null,
  parent: () => null,
});

pgkitRegisterAction("action.selectMask", {
  label: "Select", scope: "row", level: "masks",
  appliesTo: () => true,
  run: (payload) => {
    const n = payload?.target?.name;
    if (typeof n !== "string" || !n.startsWith("mask.")) return;
    const idx = parseInt(n.slice(5), 10);
    if (!(idx >= 0 && idx < board.masks.length)) return;
    replaceMaskSelection(idx);
    syncMaskList();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.deleteMask", {
  label: "\u00d7", scope: "row", level: "masks",
  appliesTo: () => true,
  run: (payload) => {
    const n = payload?.target?.name;
    if (typeof n !== "string" || !n.startsWith("mask.")) return;
    const idx = parseInt(n.slice(5), 10);
    if (!(idx >= 0 && idx < board.masks.length)) return;
    board.masks.splice(idx, 1);
    const next = [];
    for (const i of board.selectedMaskIndices) {
      if (i < idx) next.push(i);
      else if (i > idx) next.push(i - 1);
    }
    board.selectedMaskIndices = next;
    if (board.hoveredMaskIdx === idx) board.hoveredMaskIdx = -1;
    else if (board.hoveredMaskIdx > idx) board.hoveredMaskIdx -= 1;
    syncMaskList();
    pgkitNotifyAll();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { variant: "danger" },
});

/* ============================================================
   pgkitHandleSelect
   ============================================================ */

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;
  if (level === "masks") {
    if (!name.startsWith("mask.")) return false;
    const idx = parseInt(name.slice(5), 10);
    if (!(idx >= 0 && idx < board.masks.length)) return false;
    replaceMaskSelection(idx);
    syncMaskList();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return true;
  }
  return false;
}

export function segmentBootSync() {
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  _checkStatusAction();
  _kritaStatusRefresh();
}
"""