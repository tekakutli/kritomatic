"""Interactive handlers for the interceptor.

The prompt is programmatic (via the `intercept.prompt` field).

CANVAS EXPANSION
================
`handleEdgeMouseDown/Move/Up` are wired by `pg_dispatch.js`.

The drag model is:
    · At mousedown, record the start expansion, the start mouse position,
      and the *current* zoom (`frozenZoom`).  Freezing the zoom means
      the drag conversion (screen delta → canvas-pixel delta) never
      changes mid-drag.
    · During move, compute new margins from the delta / frozenZoom,
      clamped to ≥ 0.
    · Rebuild `board.expandedCanvasEl` by shifting the previous canvas
      element (or drawing the payload at the new offset if this is the
      first expansion).  The canvas element is the shift source — not
      a base64 round-trip — so the draw is synchronous and can't race
      its own load.
    · Never touch `view.zoom`, `view.offsetX`, `view.offsetY`.  Zoom
      stays fixed across the drag, so the inner image does not
      shrink.  The frame edge tracks the cursor because at fixed zoom,
      `Δleft = Δscreen / zoom` exactly cancels the `x0` shift.

The base64 the wire needs (Krita open, inpaint run) is materialised
lazily from `board.expandedCanvasEl` via `_getExpandedB64()`.  There
is no `board.expandedCanvasB64` field — a single source of truth is
what fixes the second-drag-wipes-canvas bug.

AUGMENTED PAYLOAD
=================
Selection and context masks are both expanded to W'×H':

  · selection = original mask at (left, top), black elsewhere.
    The source's selection is what tells the model *which pixels to
    modify*.  Repositioning it keeps that set exactly the same in
    the expanded frame.

  · context   = full white on W'×H', always.
    The source's context was a *crop-region* hint — the workflow
    crops to `selection ∪ context`.  When expanding, the whole
    expanded frame is the crop region: the model needs to SEE the
    drawn context, and the selection (above) already restricts what
    gets MODIFIED.  Preserving the source's Mode A / Mode B
    distinction here would make Mode B expansion a silent no-op
    (the model would never see the drawn margins), so both modes
    collapse to full white on expansion.
"""

PANEL_BINDINGS_JS = r"""
import {
  board, view, _scheduleDraw, getCachedImage,
  hasExpansion, resetExpansionState,
} from "./pg_state.js";
import { syncPanelFields }  from "./pg_panel_fields.js";
import {
  interceptList, interceptItem, interceptRunBody, interceptDiscard,
  interceptKritaOpen, interceptKritaRefresh,
} from "./pg_client.js";
import { currentFitRect, edgeHitAt, fitView } from "./pg_view.js";
import { flashStatus } from "./pgkit/panel_core.js";
import {
  pgkitRegisterAction, pgkitRegisterField,
} from "./pgkit/remote_client.js";

const POLL_MS = 2000;

/* ============================================================
   Poll loop / task refresh
   ============================================================ */

async function _refreshList() {
  const res = await interceptList();
  if (!res.success) return;

  const prevStatus = {};
  for (const [tid, t] of Object.entries(board.tasks)) {
    prevStatus[tid] = t.status;
  }

  board.tasks = {};
  board.order = [];
  for (const t of res.tasks) {
    board.tasks[t.task_id] = t;
    board.order.push(t.task_id);
  }

  if (board.selectedTaskId) {
    const now = board.tasks[board.selectedTaskId];
    if (now && prevStatus[board.selectedTaskId] !== now.status) {
      await _refreshItem(board.selectedTaskId);
    }
  }
  _scheduleDraw();
}

async function _refreshItem(taskId) {
  const res = await interceptItem(taskId);
  if (!res.success) return;
  board.selectedItem = res.task;
  _scheduleDraw();
}

export async function _selectTask(taskId) {
  board.selectedTaskId = taskId;
  board.selectedItem   = null;
  board.view           = "input";
  board.prompt         = "";
  resetExpansionState();
  board.kritaDocumentName = null;
  board.kritaFileHint     = null;
  await _refreshItem(taskId);

  const item = board.selectedItem;
  if (item && item.payload) {
    if (item.payload.canvas_b64)    getCachedImage(item.payload.canvas_b64);
    if (item.payload.selection_b64) getCachedImage(item.payload.selection_b64);
    if (item.payload.context_b64)   getCachedImage(item.payload.context_b64);
    _fitWhenReady();
  }
  _scheduleDraw();
}

function _fitWhenReady() {
  const item = board.selectedItem;
  if (!item || !item.payload) return;
  const img = getCachedImage(item.payload.canvas_b64);
  if (!img) return;
  if (img.complete && img.naturalWidth) {
    fitView();
  } else {
    img.addEventListener("load", () => {
      fitView();
      _scheduleDraw();
    }, { once: true });
  }
}

/* ============================================================
   Edge drag — grows the frame at fixed zoom
   ============================================================ */

let _edgeDrag = null;

export function isEdgeDragging() { return _edgeDrag !== null; }

function _canExpand(item) {
  if (!item || !item.payload) return false;
  return (item.payload.output_rotation || 0) === 0;
}

export function handleEdgeMouseDown(sx, sy) {
  if (!board.selectedItem) return false;
  if (board.view !== "input") return false;
  if (!_canExpand(board.selectedItem)) return false;

  const side = edgeHitAt(sx, sy);
  if (side === null) return false;

  _edgeDrag = {
    side,
    startExpansion: { ...board.expansion },
    startMouseX: sx,
    startMouseY: sy,
    frozenZoom: view.zoom,
  };
  return true;
}

export function handleEdgeMouseMove(sx, sy) {
  if (!_edgeDrag) return false;

  const dx = (sx - _edgeDrag.startMouseX) / _edgeDrag.frozenZoom;
  const dy = (sy - _edgeDrag.startMouseY) / _edgeDrag.frozenZoom;

  const oldExp = { ...board.expansion };
  const newExp = { ..._edgeDrag.startExpansion };
  switch (_edgeDrag.side) {
    case "left":   newExp.left   = Math.max(0, Math.round(_edgeDrag.startExpansion.left   - dx)); break;
    case "right":  newExp.right  = Math.max(0, Math.round(_edgeDrag.startExpansion.right  + dx)); break;
    case "top":    newExp.top    = Math.max(0, Math.round(_edgeDrag.startExpansion.top    - dy)); break;
    case "bottom": newExp.bottom = Math.max(0, Math.round(_edgeDrag.startExpansion.bottom + dy)); break;
  }

  if (newExp.left === oldExp.left && newExp.top === oldExp.top
      && newExp.right === oldExp.right && newExp.bottom === oldExp.bottom) {
    return true;
  }

  board.expansion = newExp;
  _rebuildExpandedCanvas(oldExp, newExp);
  _scheduleDraw();
  return true;
}

export function handleEdgeMouseUp() {
  if (!_edgeDrag) return false;
  _edgeDrag = null;
  _scheduleDraw();
  return true;
}

/* Rebuild `board.expandedCanvasEl` after an expansion change.

   The shift source is `board.expandedCanvasEl` — the *current* live
   canvas element.  On the very first expansion it's null, so we draw
   the payload at the new offset.  On subsequent expansions we draw
   the previous canvas shifted by (Δleft, Δtop).  Because the draw
   source is a synchronous canvas element, there's no async load to
   race — the previous code's b64-string source was that race, and
   it silently wiped the canvas on the second tick of a drag.

   If expansion returns to all-zero, drop the element. */
function _rebuildExpandedCanvas(oldExp, newExp) {
  const item = board.selectedItem;
  if (!item) return;

  const payloadImg = getCachedImage(item.payload.canvas_b64);
  if (!payloadImg || !payloadImg.complete || !payloadImg.naturalWidth) {
    if (payloadImg) {
      payloadImg.addEventListener("load", () => {
        _rebuildExpandedCanvas(
          { left: 0, top: 0, right: 0, bottom: 0 },
          board.expansion,
        );
        _scheduleDraw();
      }, { once: true });
    }
    return;
  }

  const W = payloadImg.naturalWidth;
  const H = payloadImg.naturalHeight;
  const Wp = W + newExp.left + newExp.right;
  const Hp = H + newExp.top  + newExp.bottom;

  if (Wp === W && Hp === H) {
    board.expandedCanvasEl = null;
    return;
  }

  const c = document.createElement("canvas");
  c.width  = Wp;
  c.height = Hp;
  const cx = c.getContext("2d");
  cx.imageSmoothingEnabled = true;
  cx.imageSmoothingQuality = "high";

  if (board.expandedCanvasEl) {
    const ddx = newExp.left - oldExp.left;
    const ddy = newExp.top  - oldExp.top;
    cx.drawImage(board.expandedCanvasEl, ddx, ddy);
  } else {
    cx.drawImage(payloadImg, newExp.left, newExp.top);
  }

  board.expandedCanvasEl = c;
}

/* Lazily materialise the base64 the wire needs.  Never cached — a
   large canvas's `toDataURL` is the expensive step, so it runs only
   on the two user-triggered wire calls (Krita open, inpaint run),
   never per-mousemove. */
function _getExpandedB64() {
  if (!board.expandedCanvasEl) return null;
  return board.expandedCanvasEl.toDataURL("image/png")
           .replace(/^data:image\/png;base64,/, "");
}

/* ============================================================
   Augmented payload construction
   ============================================================ */

function _buildAugmentedPayload(item, exp) {
  const selImg = getCachedImage(item.payload.selection_b64);

  if (!selImg || !selImg.complete || !selImg.naturalWidth) {
    return null;
  }
  if (!board.expandedCanvasEl) return null;

  const Wp = board.expandedCanvasEl.width;
  const Hp = board.expandedCanvasEl.height;
  const canvas_b64 = _getExpandedB64();
  if (!canvas_b64) return null;

  // Selection: original mask at (left, top), black elsewhere.
  // Preserves the source's "which pixels to modify" set exactly —
  // the model only edits where this is white, and those pixels are
  // still the ones the source inpaint wanted edited.  No Mode A/B
  // distinction here because selection was never about crop region.
  const sc = document.createElement("canvas");
  sc.width = Wp; sc.height = Hp;
  const scx = sc.getContext("2d");
  scx.fillStyle = "#000";
  scx.fillRect(0, 0, Wp, Hp);
  scx.drawImage(selImg, exp.left, exp.top);
  const selection_b64 = sc.toDataURL("image/png")
                         .replace(/^data:image\/png;base64,/, "");

  // Context: full white on the whole expanded frame, always.
  //
  // The workflow's InpaintCropImproved crops to selection ∪ context.
  // When expanding, the whole expanded frame is the crop region —
  // the model needs to SEE the drawn context, and the selection
  // (above) already restricts what gets MODIFIED.  Preserving the
  // source's Mode B rect here would silently be a no-op: the crop
  // would stay rect-sized, the model would never see the drawn
  // margins, and the user would see no benefit from expansion.
  const cc = document.createElement("canvas");
  cc.width = Wp; cc.height = Hp;
  const ccx = cc.getContext("2d");
  ccx.fillStyle = "#fff";
  ccx.fillRect(0, 0, Wp, Hp);
  const context_b64 = cc.toDataURL("image/png")
                       .replace(/^data:image\/png;base64,/, "");

  return { canvas_b64, selection_b64, context_b64 };
}

/* ============================================================
   Run / discard
   ============================================================ */

async function _runAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  const summary = board.tasks[tid];
  if (!summary || summary.status !== "queued") return;

  const item = board.selectedItem;
  if (!item || !item.payload) return;

  if (hasExpansion() && !_canExpand(item)) {
    flashStatus("expansion not supported when source payload is rotated",
                "warn");
    return;
  }

  const runBtn     = document.getElementById("runBtn");
  const discardBtn = document.getElementById("discardBtn");
  if (runBtn)     runBtn.disabled     = true;
  if (discardBtn) discardBtn.disabled = true;

  flashStatus("inpainting\u2026", "");

  const runBody = {
    task_id: tid,
    prompt:  board.prompt || undefined,
  };

  if (hasExpansion()) {
    const aug = _buildAugmentedPayload(item, board.expansion);
    if (!aug) {
      flashStatus("could not build augmented payload", "bad");
      _scheduleDraw();
      return;
    }
    runBody.augmented_canvas_b64    = aug.canvas_b64;
    runBody.augmented_selection_b64 = aug.selection_b64;
    runBody.augmented_context_b64   = aug.context_b64;
    runBody.expansion = { ...board.expansion };
  }

  const res = await interceptRunBody(runBody);

  if (res.success) {
    flashStatus("inpainted in " + ((res.timing || 0).toFixed(2)) + "s",
                "ok");
    board.view = "result";
  } else {
    flashStatus("inpaint failed: " + (res.message || "unknown"), "bad");
  }

  await _refreshList();
  await _refreshItem(tid);
  _scheduleDraw();
}

async function _discardAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  const res = await interceptDiscard(tid);
  if (res.success) flashStatus("discarded", "ok");
  else             flashStatus("discard failed: " + (res.message || "unknown"), "bad");
  await _refreshList();
  if (board.selectedTaskId === tid) await _refreshItem(tid);
  _scheduleDraw();
}

function _toggleView() {
  if (!board.selectedItem || !board.selectedItem.result) return;
  board.view = (board.view === "result") ? "input" : "result";
  _scheduleDraw();
}

/* ============================================================
   Krita round-trip
   ============================================================ */

async function _openInKritaAction() {
  if (!board.expandedCanvasEl) {
    flashStatus("expand the canvas first", "warn");
    return;
  }
  if (!_canExpand(board.selectedItem)) {
    flashStatus("expansion not supported when source payload is rotated",
                "warn");
    return;
  }

  flashStatus("opening in Krita\u2026", "");
  const b64 = _getExpandedB64();
  if (!b64) {
    flashStatus("could not materialise canvas", "bad");
    return;
  }
  const res = await interceptKritaOpen(b64);
  if (!res.success) {
    flashStatus("open failed: " + (res.message || "unknown"), "bad");
    return;
  }
  board.kritaDocumentName = res.document_name || null;
  board.kritaFileHint     = res.file_hint || null;
  flashStatus("opened in Krita: " + (board.kritaFileHint
                                     || board.kritaDocumentName), "ok");
  _scheduleDraw();
}

async function _refreshFromKritaAction() {
  if (!board.kritaDocumentName) {
    flashStatus("open in Krita first", "warn");
    return;
  }
  flashStatus("refreshing from Krita\u2026", "");
  const res = await interceptKritaRefresh(board.kritaDocumentName);
  if (!res.success) {
    flashStatus("refresh failed: " + (res.message || "unknown"), "bad");
    return;
  }

  const exp  = board.expansion;
  const item = board.selectedItem;
  const payloadImg = item
    ? getCachedImage(item.payload.canvas_b64) : null;
  if (payloadImg && payloadImg.complete) {
    const W = payloadImg.naturalWidth;
    const H = payloadImg.naturalHeight;
    const expectedW = W + exp.left + exp.right;
    const expectedH = H + exp.top  + exp.bottom;
    if (res.width !== expectedW || res.height !== expectedH) {
      flashStatus(
        "refresh dims mismatch: got " + res.width + "\u00d7" + res.height
        + ", expected " + expectedW + "\u00d7" + expectedH,
        "warn");
      return;
    }
  }

  const img = new Image();
  await new Promise((resolve, reject) => {
    img.onload  = resolve;
    img.onerror = reject;
    img.src = "data:image/png;base64," + res.canvas_b64;
  });
  const c = document.createElement("canvas");
  c.width  = img.naturalWidth;
  c.height = img.naturalHeight;
  c.getContext("2d").drawImage(img, 0, 0);
  board.expandedCanvasEl = c;

  flashStatus("refreshed: " + res.width + "\u00d7" + res.height, "ok");
  _scheduleDraw();
}

function _resetExpansionAction() {
  resetExpansionState();
  board.kritaDocumentName = null;
  board.kritaFileHint     = null;
  _fitWhenReady();
  flashStatus("expansion reset", "ok");
  _scheduleDraw();
}

/* ============================================================
   Install
   ============================================================ */

export function installPanelBindings() {
  const runBtn            = document.getElementById("runBtn");
  const discardBtn        = document.getElementById("discardBtn");
  const toggleLink        = document.getElementById("toggleViewLink");
  const openKritaBtn      = document.getElementById("openKritaBtn");
  const refreshKritaBtn   = document.getElementById("refreshKritaBtn");
  const resetExpansionBtn = document.getElementById("resetExpansionBtn");

  if (runBtn)             runBtn.addEventListener("click", _runAction);
  if (discardBtn)         discardBtn.addEventListener("click", _discardAction);
  if (toggleLink)         toggleLink.addEventListener("click", _toggleView);
  if (openKritaBtn)       openKritaBtn.addEventListener("click", _openInKritaAction);
  if (refreshKritaBtn)    refreshKritaBtn.addEventListener("click", _refreshFromKritaAction);
  if (resetExpansionBtn)  resetExpansionBtn.addEventListener("click", _resetExpansionAction);

  if (board.pollTimer) clearInterval(board.pollTimer);
  board.pollTimer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    _refreshList();
  }, POLL_MS);
}

export function interceptorBootSync() {
  syncPanelFields();
  _refreshList();
  _scheduleDraw();
}

/* ============================================================
   pgkit interfaces
   ============================================================ */

pgkitRegisterField("intercept.prompt", {
  get: () => {
    if (board.prompt) return board.prompt;
    const item = board.selectedItem;
    if (item && item.payload && typeof item.payload.prompt === "string") {
      return item.payload.prompt;
    }
    return "";
  },
  set: (v) => {
    board.prompt = (v === null || v === undefined) ? "" : String(v);
  },
  meta: {
    kind: "text",
    label: "Inpaint prompt (override)",
    visible: false,
  },
});

pgkitRegisterAction("action.run", {
  label: "Inpaint", scope: "global", run: _runAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.discard", {
  label: "Discard", scope: "global", run: _discardAction,
  meta: { variant: "danger" },
});
pgkitRegisterAction("action.kritaOpen", {
  label: "Open in Krita", scope: "global", run: _openInKritaAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.kritaRefresh", {
  label: "Refresh from Krita", scope: "global", run: _refreshFromKritaAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.resetExpansion", {
  label: "Reset expansion", scope: "global", run: _resetExpansionAction,
  meta: { variant: "plain" },
});
"""
