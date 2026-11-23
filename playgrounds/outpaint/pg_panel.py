"""Panel: area size scrub inputs, generated stack, send-to-krita,
refresh, krita check.

AREA DIM SCRUB INPUTS
=====================
`#areaW` and `#areaH` are scrub widgets driven by `_installScrubInput`
from `pgkit/panel_core.js` — the same primitive the inpaint
playground uses for view rotation.  Behaviour:
  · Click to focus and type an exact value (integer pixels).
  · Drag sideways to scrub.  `rate: 1` means 1 pixel of horizontal
    drag = 1 pixel of area dimension.
  · Shift-drag snaps to multiples of 8 (matching the `snapStep: 8`
    option), which helps land on common power-of-two-ish resolutions
    without typing.
  · The `write` callback clamps the requested value to
    `[8, 16384]`, then hands it to `setAreaDims()`.  `setAreaDims`
    additionally enforces `area ≥ viewer.px*` and
    `area ≥ VIEWER_MIN_SIZE_PX`, so the viewer is always fully
    contained inside the area.  Whatever the clamp chain produces is
    written back into the other input (only if that input is not
    focused), so the two widgets stay in sync when `setAreaDims`
    bumps one dimension to accommodate the viewer's other constraint.

VIEWER ASPECT
=============
There is no aspect-ratio dropdown.  The viewer *is* the currently
displayed image: its aspect is derived from
`board.areaImageW / board.areaImageH` inside `viewerAspect()`
(see `pg_state.js`), and `applyCanvasImage()` re-fits the viewer
whenever a new source image arrives with a different aspect.  The
contain-fit inside the viewer therefore degenerates to a full fill,
with no letterboxing.

GENERATED STACK
===============
Thumbnails in the panel's "Generated" section are driven by
`board.generatedStack`.  The panel rebuilds the strip whenever
`window` dispatches `pg:stackChanged` (which `pg_state.js` fires
on push / preview / delete / preview-source).  A `_lastStackKey`
deduplicates renders so a spurious event from a source refresh does
not rebuild the DOM.

The strip is a CSS grid (`auto-fill` columns, `max-height` +
`overflow-y: auto`), so new entries wrap to new rows and overflow
scrolls vertically instead of running off the right edge of the
panel.  See `pg_base.py`'s GENERATED SECTION.

Clicking a thumbnail → `previewGenerated(idx)` in `pg_state.js`,
which saves the source view and shows the generated item at full
size.  The active thumbnail gets the `.active` class.

The delete (×) button on each thumbnail → `deleteFromStack(idx)`.
If the deleted item was the active preview, the panel advances to
the adjacent item (or back to source if the stack becomes empty).

`Show source` button → `previewSource()`, restoring the saved
source view.

SEND TO KRITA
=============
One button, `_sendToKritaAction`.  Requires
`getPreviewed() != null` — refuses to run when the source is
showing.  Reads `entry.b64 / entry.width / entry.height` from the
currently-previewed stack entry (NOT from `board.areaImageDataUrl`,
which may not match if the user changed preview between render and
click).  The server-side flow is unchanged: create a new document
at the image dims, then paste into it.

The button's `disabled` state and text label are kept in sync by
`_updateSendBtn`, called after every stack change.

BOOT AUTO-FIT
=============
`_fitAreaIfOverflow()` runs once at boot.  If the initial area is
larger than the window, it sets `view.zoom` so the whole area fits —
this is the one place where the scale is derived from window size,
so the boundary that previously re-appeared on every drag is now
strictly boot-only.  Growth during drag does not re-trigger it; the
user's wheel zoom owns the scale for the rest of the session.  See
`pg_state.py`'s module docstring, RENDER SCALE — NO FIT-TO-WINDOW.

Boot → `resetViewerToFit()` once.  When the first source image
arrives, `applyCanvasImage()` may call `refitViewerAspect()` if the
initial placeholder aspect differs from the image's, keeping the
centre.
"""

PANEL_JS = r"""
 import {
   board, view, setAreaDims, resetViewerToFit,
   clampPan,
   pushGeneratedToStack,
   previewGenerated, previewSource, deleteFromStack,
   getPreviewed, bufferIsGenerated,
 } from "./pg_state.js";
 import { VIEW_ZOOM_MIN } from "./pg_tuning.js";
 import {
   checkKritaStatus, fetchActiveState, sendToKrita,
 } from "./pg_client.js";
 import { fetchSelectedLayer } from "./pg_autosync.js";
 import { flashStatus } from "./pgkit/panel_core.js";
 import { _installScrubInput } from "./pgkit/panel_core.js";

 const PLAYGROUND_ID = "outpaint";
 const SLOT_ID       = "@thecanvas";

 let _lastStackKey = "";

 /* ============================================================
    Panel sync
    ============================================================ */

 export function syncPanelFields() {
   const e = {
     imageInfo:   document.getElementById("imageInfo"),
     areaW:       document.getElementById("areaW"),
     areaH:       document.getElementById("areaH"),
   };

   if (e.imageInfo) {
     const mode = bufferIsGenerated()
       ? "gen " + board.previewIndex
       : "src";
     const text = board.areaImageEl
       ? "area " + board.areaWidth + "\u00d7" + board.areaHeight
         + "   \u00b7   " + mode
         + " " + board.areaImageW + "\u00d7" + board.areaImageH
         + "   [" + String(board.areaImageHash).slice(0, 8) + "\u2026]"
       : "area " + board.areaWidth + "\u00d7" + board.areaHeight
         + "   \u00b7   no image";
     if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
   }

   if (e.areaW && document.activeElement !== e.areaW) {
     const want = String(board.areaWidth);
     if (e.areaW.value !== want) e.areaW.value = want;
   }
   if (e.areaH && document.activeElement !== e.areaH) {
     const want = String(board.areaHeight);
     if (e.areaH.value !== want) e.areaH.value = want;
   }
 }

 /* ============================================================
    Boot auto-fit (one-shot, only if area overflows window)
    ============================================================ */

 function _fitAreaIfOverflow() {
   const cw = window.innerWidth, ch = window.innerHeight;
   const pad = 40;
   const sx = (cw - 2 * pad) / board.areaWidth;
   const sy = (ch - 2 * pad) / board.areaHeight;
   const fit = Math.min(sx, sy);
   if (fit >= 1) return;   // area already fits at 1:1 — leave zoom alone
   view.zoom = Math.max(VIEW_ZOOM_MIN, fit);
   view.panX = 0;
   view.panY = 0;
   clampPan();
 }

 /* ============================================================
    Area size scrub inputs
    ============================================================ */

 function _installAreaInputs() {
   const wEl = document.getElementById("areaW");
   const hEl = document.getElementById("areaH");

   if (wEl) {
     wEl.value = String(board.areaWidth);
     _installScrubInput(wEl, {
       rate: 1,
       snapStep: 8,
       read: () => board.areaWidth,
       write: (raw, drag) => {
         const v = parseInt(String(raw), 10);
         if (!isFinite(v)) return;
         const clamped = Math.max(8, Math.min(16384, v));
         const actual = setAreaDims(clamped, board.areaHeight);
         if (document.activeElement !== wEl) wEl.value = String(actual.w);
         if (hEl && document.activeElement !== hEl) hEl.value = String(actual.h);
       },
     });
   }

   if (hEl) {
     hEl.value = String(board.areaHeight);
     _installScrubInput(hEl, {
       rate: 1,
       snapStep: 8,
       read: () => board.areaHeight,
       write: (raw, drag) => {
         const v = parseInt(String(raw), 10);
         if (!isFinite(v)) return;
         const clamped = Math.max(8, Math.min(16384, v));
         const actual = setAreaDims(board.areaWidth, clamped);
         if (wEl && document.activeElement !== wEl) wEl.value = String(actual.w);
         if (document.activeElement !== hEl) hEl.value = String(actual.h);
       },
     });
   }
 }

 /* ============================================================
    Generated-stack rendering
    ============================================================ */

 export function _renderGeneratedStack() {
   const strip   = document.getElementById("genStrip");
   const empty   = document.getElementById("genEmpty");
   const countEl = document.getElementById("genCount");
   if (!strip || !empty) return;

   const stack = board.generatedStack;
   const key = stack.map(e => e.id).join(",") + "|" + board.previewIndex;
   if (key === _lastStackKey) {
     _updateSendBtn();
     return;
   }
   _lastStackKey = key;

   if (countEl) countEl.textContent = String(stack.length);

   if (stack.length === 0) {
     empty.style.display = "";
     strip.style.display = "none";
     strip.innerHTML = "";
     _updateSendBtn();
     _updatePreviewInfo();
     return;
   }
   empty.style.display = "none";
   // display: grid — the strip is a wrapping auto-fill grid; new
   // entries spill into additional rows and overflow scrolls
   // vertically.  See pg_base.py, #genStrip.
   strip.style.display = "grid";

   // Rebuild strip.  The previous children are discarded; thumbnails
   // are tiny so this is cheap even for a couple dozen entries.
   strip.innerHTML = "";
   for (let i = 0; i < stack.length; i++) {
     const entry = stack[i];
     const div = document.createElement("div");
     div.className = "gen-thumb" + (i === board.previewIndex ? " active" : "");
     div.title = entry.width + "\u00d7" + entry.height
       + "   \u2022   click to preview";

     const img = document.createElement("img");
     img.src = entry.dataUrl;
     img.alt = "";
     img.draggable = false;
     div.appendChild(img);

     const del = document.createElement("button");
     del.className = "del";
     del.textContent = "\u00d7";
     del.title = "Delete from stack";
     del.addEventListener("click", (ev) => {
       ev.stopPropagation();
       _deleteAction(i);
     });
     div.appendChild(del);

     div.addEventListener("click", () => _previewAction(i));
     strip.appendChild(div);
   }

   _updateSendBtn();
   _updatePreviewInfo();
 }

 function _updatePreviewInfo() {
   const el = document.getElementById("previewInfo");
   if (!el) return;
   if (board.previewIndex >= 0
       && board.previewIndex < board.generatedStack.length) {
     const e = board.generatedStack[board.previewIndex];
     el.textContent = "Previewing " + e.width + "\u00d7" + e.height
       + "   \u2022   " + (board.previewIndex + 1)
       + "/" + board.generatedStack.length;
   } else if (board.generatedStack.length > 0) {
     el.textContent = "Showing @thecanvas (click a thumbnail to preview)";
   } else {
     el.textContent = "";
   }
 }

 function _updateSendBtn() {
   const btn = document.getElementById("sendToKritaBtn");
   const showSrc = document.getElementById("showSourceBtn");
   const entry = getPreviewed();
   if (btn) {
     btn.disabled = !entry;
     btn.textContent = entry
       ? "\u2192 Send " + entry.width + "\u00d7" + entry.height
         + " to Krita"
       : "\u2192 Send to Krita";
   }
   if (showSrc) {
     showSrc.style.display = (board.previewIndex >= 0) ? "" : "none";
   }
 }

 /* ============================================================
    Stack actions
    ============================================================ */

 function _previewAction(idx) {
   previewGenerated(idx);
   // previewGenerated fires pg:stackChanged, which re-renders the
   // strip; no explicit call needed here.
 }

 function _deleteAction(idx) {
   deleteFromStack(idx);
 }

 function _showSourceAction() {
   previewSource();
 }

 /* ============================================================
    Buttons
    ============================================================ */

 async function _refreshCanvasAction() {
   flashStatus("refreshing " + SLOT_ID + "\u2026", "");
   const state = await fetchActiveState();
   if (!state || state.daemon_reachable === false) {
     flashStatus(SLOT_ID + " refresh failed: daemon unreachable", "bad");
     return;
   }
   const playgrounds = (state.data && state.data.playgrounds) || [];
   const entry = playgrounds.find(p => p.id === PLAYGROUND_ID);
   const slots = (entry && entry.layer_slots) || [];
   const slot = slots.find(s => s.id === SLOT_ID);
   if (!slot) {
     flashStatus(SLOT_ID + " not registered", "warn");
     return;
   }
   const selector = slot.selector
     || { source: "active", layer_name: null };
   await fetchSelectedLayer(selector);
 }

 async function _kritaStatusAction() {
   const res = await checkKritaStatus();
   const el = document.getElementById("kritaStatus");
   if (!el) return;
   if (res.reachable) {
     el.textContent = "\u2713 reachable";
     el.className = "ok";
   } else {
     el.textContent = "\u2717 not reachable";
     el.className = "bad";
   }
 }

 async function _sendToKritaAction() {
   const entry = getPreviewed();
   if (!entry) {
     flashStatus(
       "no generated image selected \u2014 click a thumbnail first",
       "warn",
     );
     return;
   }
   if (!entry.b64) {
     flashStatus("selected entry has no image data", "bad");
     return;
   }

   if (typeof console !== "undefined" && console.log) {
     console.log(
       "[send_to_krita] hash=" + String(entry.hash).slice(0, 12)
       + " size=" + entry.width + "x" + entry.height
       + " b64_len=" + entry.b64.length
     );
   }

   flashStatus("sending to Krita\u2026", "");
   const res = await sendToKrita(
     entry.b64, entry.width, entry.height, undefined,
   );
   if (!res || !res.success) {
     flashStatus(
       "send to krita failed: "
       + ((res && res.message) || "unknown"),
       "bad",
     );
     return;
   }
   flashStatus(
     "created new document: "
     + (res.doc_name || "?")
     + " (" + (res.width || "?") + "\u00d7" + (res.height || "?") + ")",
     "ok",
   );
 }

 /* ============================================================
    Install
    ============================================================ */

 export function installPanelBindings() {
   const ids = {
     refresh:    "refreshCanvasBtn",
     kritaCheck: "kritaCheckBtn",
     send:       "sendToKritaBtn",
     showSource: "showSourceBtn",
   };
   const el = {};
   for (const k in ids) el[k] = document.getElementById(ids[k]);

   if (el.refresh)    el.refresh.addEventListener("click", _refreshCanvasAction);
   if (el.kritaCheck) el.kritaCheck.addEventListener("click", _kritaStatusAction);
   if (el.send)       el.send.addEventListener("click", _sendToKritaAction);
   if (el.showSource) el.showSource.addEventListener("click", _showSourceAction);

   // Stack re-render on any state change (push / preview / delete).
   window.addEventListener("pg:stackChanged", () => {
     _renderGeneratedStack();
   });

   _installAreaInputs();
   _renderGeneratedStack();
   _updateSendBtn();

   // One-time fit: establish sensible viewer bounds for the initial
   // area.  Never called again on area resize.
   resetViewerToFit();

   // One-shot boot fit for the *area* itself, only if it overflows
   // the window at 1:1.  Never re-runs, so dragging area corners
   // past window size can never trigger the "shrink the whole
   // canvas" boundary.  See module docstring, BOOT AUTO-FIT.
   _fitAreaIfOverflow();
 }

 export function layoutBootSync() {
   syncPanelFields();
   _kritaStatusAction();
 }

 export function onDrawComplete() {
   syncPanelFields();
 }
 """