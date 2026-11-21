"""Panel: area size scrub inputs, ratio dropdown, refresh, krita check.

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
    contained inside the area — the invariant the panel previously
    relied on `min="1"` attributes and post-hoc clamping to
    maintain.  Whatever the clamp chain produces is written back
    into the other input (only if that input is not focused), so
    the two widgets stay in sync when `setAreaDims` bumps one
    dimension to accommodate the viewer's other constraint.

The old `change`-event handlers are gone.  `write` is now the single
entry point for area-dim mutations, whether triggered by typing or
by dragging.

BOOT AUTO-FIT
=============
`_fitAreaIfOverflow()` runs once at boot.  If the initial area is
larger than the window, it sets `view.zoom` so the whole area fits —
this is the one place where the scale is derived from window size,
so the boundary that previously re-appeared on every drag is now
strictly boot-only.  Growth during drag does not re-trigger it; the
user's wheel zoom owns the scale for the rest of the session.  See
`pg_state.py`'s module docstring, RENDER SCALE — NO FIT-TO-WINDOW.

Ratio change → `refitViewerAspect()` keeps the current centre.

Boot → `resetViewerToFit()` once.
"""

PANEL_JS = r"""
 import {
   board, view, setAreaDims, resetViewerToFit, refitViewerAspect,
   clampPan,
 } from "./pg_state.js";
 import { VIEW_ZOOM_MIN } from "./pg_tuning.js";
 import { checkKritaStatus, fetchActiveState } from "./pg_client.js";
 import { fetchSelectedLayer } from "./pg_autosync.js";
 import { flashStatus } from "./pgkit/panel_core.js";
 import { _installScrubInput } from "./pgkit/panel_core.js";

 const PLAYGROUND_ID = "outpaint";
 const SLOT_ID       = "@theoutpainted";

 const RATIO_KEYS = [
   "0.25", "0.26", "0.27", "0.28",
   "0.32", "0.33", "0.35", "0.40",
   "0.42", "0.48", "0.50", "0.52",
   "0.57", "0.60", "0.68", "0.72",
   "0.78", "0.82", "0.88", "0.94",
   "1.00", "1.07", "1.13", "1.21",
   "1.29", "1.38", "1.46", "1.67",
   "1.75", "2.00", "2.09", "2.40",
   "2.50", "2.89", "3.00", "3.11",
   "3.62", "3.75", "3.88", "4.00",
 ];

 /* ============================================================
    Panel sync
    ============================================================ */

 export function syncPanelFields() {
   const e = {
     imageInfo:   document.getElementById("imageInfo"),
     viewerRatio: document.getElementById("viewerRatio"),
     areaW:       document.getElementById("areaW"),
     areaH:       document.getElementById("areaH"),
   };

   if (e.imageInfo) {
     const text = board.areaImageEl
       ? "area " + board.areaWidth + "\u00d7" + board.areaHeight
         + "   \u00b7   image " + board.areaImageW + "\u00d7" + board.areaImageH
         + "   [" + String(board.areaImageHash).slice(0, 8) + "\u2026]"
       : "area " + board.areaWidth + "\u00d7" + board.areaHeight
         + "   \u00b7   no image";
     if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
   }

   if (e.viewerRatio && document.activeElement !== e.viewerRatio) {
     if (e.viewerRatio.value !== board.viewer.ratio) {
       e.viewerRatio.value = board.viewer.ratio;
     }
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
    Ratio dropdown
    ============================================================ */

 function _installViewerDropdowns() {
   const ratioSel = document.getElementById("viewerRatio");
   if (!ratioSel) return;
   if (ratioSel.options.length === 0) {
     for (const k of RATIO_KEYS) {
       const opt = document.createElement("option");
       opt.value = k;
       opt.textContent = k;
       ratioSel.appendChild(opt);
     }
   }
   ratioSel.value = board.viewer.ratio;
   ratioSel.addEventListener("change", () => {
     board.viewer.ratio = ratioSel.value;
     refitViewerAspect();   // preserve centre, don't reset
   });
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

 /* ============================================================
    Install
    ============================================================ */

 export function installPanelBindings() {
   const ids = {
     refresh:    "refreshCanvasBtn",
     kritaCheck: "kritaCheckBtn",
   };
   const el = {};
   for (const k in ids) el[k] = document.getElementById(ids[k]);

   if (el.refresh)    el.refresh.addEventListener("click", _refreshCanvasAction);
   if (el.kritaCheck) el.kritaCheck.addEventListener("click", _kritaStatusAction);

   _installViewerDropdowns();
   _installAreaInputs();

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