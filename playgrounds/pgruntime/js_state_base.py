"""
pgruntime.js_state_base — `makeStateBase(canvasEl, opts)`.

Returns a state bundle providing every playground's core canvas/view
boilerplate.  Extensions are opt-in via `opts`:

    overlay:  true    — creates the transparent overlay canvas,
                        adds `_scheduleDrawView` / `_setDrawView`
                        and a `_layerSigs` cache keyed by layer name
    rotation: true    — view gains a `rotation` field; imageToScreen
                        and screenToImage apply/undo the rotation
                        around the image's centre
    applyHooks: {...} — hooks into applyCanvasImage (onHashChanged,
                        onDimsChanged, onLoad, onError)

The returned `board` object is where the playground stores its
own per-playground fields.  `makeStateBase` initialises the
universal image fields; the caller extends via `Object.assign(board,
{ ... })`.  Reassigning `board` breaks the state bundle's internal
references; do not do it.
"""

STATE_BASE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./constants.js";

/* ==========================================================================
   FACTORY
   ========================================================================== */

export function makeStateBase(canvasEl, opts = {}) {
  const useOverlay  = !!opts.overlay;
  const useRotation = !!opts.rotation;
  const hooks       = opts.applyHooks || {};

  const ctx = canvasEl.getContext("2d");

  let _dpr = window.devicePixelRatio || 1;

  /* ---- View ---- */

  const view = useRotation
    ? { zoom: 1, panX: 0, panY: 0, rotation: 0 }
    : { zoom: 1, panX: 0, panY: 0 };

  /* ---- Overlay canvas ---- */

  let overlayCanvas = null;
  let overlayCtx    = null;
  if (useOverlay) {
    overlayCanvas = document.createElement("canvas");
    overlayCanvas.id = "overlayCanvas";
    document.body.appendChild(overlayCanvas);
    overlayCtx = overlayCanvas.getContext("2d");
  }

  /* ---- Board — universal image fields only ---- */

  const board = {
    imageHash:    null,
    imageWidth:   0,
    imageHeight:  0,
    imageDataUrl: null,
    imageEl:      null,
  };

  /* ---- Per-layer sig cache (used by sig-based renderers) ---- */

  const _layerSigs = { image: null, overlay: null };

  /* ---- Masks version counter ---- */

  let _masksVersion = 0;
  function _bumpMasksVersion() { _masksVersion++; }
  function _getMasksVersion()  { return _masksVersion; }

  /* ---- View ops ---- */

  function resetView() {
    view.zoom = 1;
    view.panX = 0;
    view.panY = 0;
    if (useRotation) view.rotation = 0;
  }

  function clampPan() {
    if (!board.imageWidth || !board.imageHeight) return;
    const cw = window.innerWidth, ch = window.innerHeight;
    const pad = 40;
    const sx = (cw - 2 * pad) / board.imageWidth;
    const sy = (ch - 2 * pad) / board.imageHeight;
    const baseS = Math.min(sx, sy);
    const s = baseS * view.zoom;

    const rotRad = useRotation ? (view.rotation * Math.PI / 180) : 0;
    const cos = Math.abs(Math.cos(rotRad));
    const sin = Math.abs(Math.sin(rotRad));
    const bw = (board.imageWidth * cos + board.imageHeight * sin) * s;
    const bh = (board.imageWidth * sin + board.imageHeight * cos) * s;

    const baseX = (cw - bw) / 2;
    const baseY = (ch - bh) / 2;
    const m = VIEW_PAN_MARGIN;

    view.panX = Math.max(m - baseX - bw, Math.min(cw - m - baseX, view.panX));
    view.panY = Math.max(m - baseY - bh, Math.min(ch - m - baseY, view.panY));
  }

  function zoomAt(sx, sy, newZoom) {
    const [ix0, iy0] = screenToImage(sx, sy);
    view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));
    const [sx1, sy1] = imageToScreen(ix0, iy0);
    view.panX += sx - sx1;
    view.panY += sy - sy1;
    clampPan();
  }

  /* ---- Fit-rect memoisation ---- */

  let _firKey = null, _firVal = null;

  function fittedImageRect() {
    const cw = window.innerWidth, ch = window.innerHeight;
    if (!board.imageWidth || !board.imageHeight) return null;

    const key = cw + "," + ch + ","
              + board.imageWidth + "," + board.imageHeight + ","
              + view.zoom + "," + view.panX + "," + view.panY;
    if (key === _firKey) return _firVal;

    const pad = 40;
    const sx = (cw - 2 * pad) / board.imageWidth;
    const sy = (ch - 2 * pad) / board.imageHeight;
    const baseS = Math.min(sx, sy);
    const s = baseS * view.zoom;
    const w = board.imageWidth * s;
    const h = board.imageHeight * s;
    const x0 = (cw - w) / 2 + view.panX;
    const y0 = (ch - h) / 2 + view.panY;

    _firKey = key;
    _firVal = { x0, y0, w, h, s };
    return _firVal;
  }

  function imageToScreen(x, y) {
    const r = fittedImageRect();
    if (!r) return [x, y];
    if (!useRotation || !view.rotation) {
      return [r.x0 + x * r.s, r.y0 + y * r.s];
    }
    const cx = r.x0 + r.w / 2, cy = r.y0 + r.h / 2;
    const dx = (x - board.imageWidth / 2) * r.s;
    const dy = (y - board.imageHeight / 2) * r.s;
    const rad = view.rotation * Math.PI / 180;
    const cos = Math.cos(rad), sin = Math.sin(rad);
    return [cx + dx * cos - dy * sin, cy + dx * sin + dy * cos];
  }

  function screenToImage(sx, sy) {
    const r = fittedImageRect();
    if (!r) return [0, 0];
    if (!useRotation || !view.rotation) {
      return [(sx - r.x0) / r.s, (sy - r.y0) / r.s];
    }
    const cx = r.x0 + r.w / 2, cy = r.y0 + r.h / 2;
    const dx = sx - cx, dy = sy - cy;
    const rad = view.rotation * Math.PI / 180;
    const cos = Math.cos(rad), sin = Math.sin(rad);
    const rx =  dx * cos + dy * sin;
    const ry = -dx * sin + dy * cos;
    return [board.imageWidth / 2 + rx / r.s,
            board.imageHeight / 2 + ry / r.s];
  }

  function screenToImageUnrotated(sx, sy) {
    const r = fittedImageRect();
    if (!r) return [0, 0];
    return [(sx - r.x0) / r.s, (sy - r.y0) / r.s];
  }

  /* ---- Schedulers ---- */

  let _drawPending = false;
  let _drawFull    = false;
  let _drawFn      = null;
  let _drawViewFn  = null;

  function _setDraw(fn)     { _drawFn = fn; }
  function _setDrawView(fn) { _drawViewFn = fn; }

  function _fire() {
    const full = _drawFull;
    _drawPending = false;
    _drawFull = false;
    if (full) { if (_drawFn) _drawFn(); }
    else      { if (_drawViewFn) _drawViewFn(); }
  }

  function _scheduleDraw() {
    _drawFull = true;
    if (_drawPending) return;
    _drawPending = true;
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(_fire);
    else setTimeout(_fire, 16);
  }

  function _scheduleDrawView() {
    if (_drawPending) return;
    _drawFull = false;
    _drawPending = true;
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(_fire);
    else setTimeout(_fire, 16);
  }

  /* ---- Apply canvas image ---- */

  function applyCanvasImage(hash, dataUrl, width, height) {
    const oldHash = board.imageHash;
    const hashChanged = (oldHash !== hash);

    if (hashChanged && hooks.onHashChanged) {
      try { hooks.onHashChanged(board, hash, oldHash); }
      catch (e) { console.error("[state_base] onHashChanged:", e); }
    }

    board.imageHash = hash;
    board.imageDataUrl = dataUrl;

    const img = new Image();
    img.onload = () => {
      const oldW = board.imageWidth, oldH = board.imageHeight;
      const newW = img.naturalWidth, newH = img.naturalHeight;
      const dimsChanged = (oldW !== newW) || (oldH !== newH);

      board.imageEl     = img;
      board.imageWidth  = newW;
      board.imageHeight = newH;

      if (dimsChanged && hooks.onDimsChanged) {
        try { hooks.onDimsChanged(board, oldW, oldH, newW, newH); }
        catch (e) { console.error("[state_base] onDimsChanged:", e); }
      }
      if (hooks.onLoad) {
        try { hooks.onLoad(board, img, hashChanged); }
        catch (e) { console.error("[state_base] onLoad:", e); }
      }
      _scheduleDraw();
    };
    img.onerror = () => {
      board.imageEl = null;
      if (hooks.onError) {
        try { hooks.onError(board); } catch (e) {}
      }
      _scheduleDraw();
    };
    img.src = dataUrl;
    _scheduleDraw();
  }

  /* ---- Resize ---- */

  function resize() {
    _dpr = window.devicePixelRatio || 1;
    const cw = window.innerWidth, ch = window.innerHeight;

    canvasEl.width  = Math.round(cw * _dpr);
    canvasEl.height = Math.round(ch * _dpr);
    canvasEl.style.width  = cw + "px";
    canvasEl.style.height = ch + "px";
    ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

    if (useOverlay && overlayCanvas) {
      overlayCanvas.width  = Math.round(cw * _dpr);
      overlayCanvas.height = Math.round(ch * _dpr);
      overlayCanvas.style.width  = cw + "px";
      overlayCanvas.style.height = ch + "px";
      overlayCtx.setTransform(_dpr, 0, 0, _dpr, 0, 0);
    }

    _layerSigs.image = null;
    _layerSigs.overlay = null;

    clampPan();
    if (_drawFn) _drawFn();
  }

  window.addEventListener("resize", resize);

  /* ---- Return bundle ---- */

  return {
    canvas:         canvasEl,
    ctx,
    overlayCanvas,
    overlayCtx,
    view,
    board,
    resetView,
    clampPan,
    zoomAt,
    fittedImageRect,
    imageToScreen,
    screenToImage,
    screenToImageUnrotated,
    applyCanvasImage,
    _setDraw,
    _scheduleDraw,
    _setDrawView,
    _scheduleDrawView,
    _bumpMasksVersion,
    _getMasksVersion,
    _layerSigs,
    resize,
  };
}
"""
