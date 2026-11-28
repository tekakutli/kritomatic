"""
Controlnets state — canvas, view, board, applyCanvasImage.

Delegates to pgruntime's `makeStateBase`.  The base's `board` has the
universal image fields (`imageHash`, `imageEl`, `imageWidth`,
`imageHeight`, `imageDataUrl`); the playground extends it with
`controlnets`, `viewingControlnetTag`, `applyNodes`, `preprocessRunning`,
`lastSync`, `syncError`.

The `onHashChanged` hook clears the preprocessed-image list whenever
the source image changes — the same behaviour controlnets has always
had in `applyCanvasImage`.

No `rotation`, no `overlay` — controlnets has neither concept.
"""

STATE_JS = r"""
import { makeStateBase } from "./pgruntime/js/state_base.js";

const _base = makeStateBase(document.getElementById("c"), {
  applyHooks: {
    onHashChanged: (board, hash, oldHash) => {
      board.controlnets = [];
      board.viewingControlnetTag = null;
    },
  },
});

export const {
  canvas, ctx, view, board,
  resetView, clampPan, zoomAt,
  fittedImageRect, imageToScreen, screenToImage,
  applyCanvasImage,
  _setDraw, _scheduleDraw,
  _setDrawView, _scheduleDrawView,
  resize,
} = _base;

Object.assign(board, {
  controlnets:          [],
  viewingControlnetTag: null,
  preprocessRunning:    false,
  applyNodes:           [],
  lastSync:             null,
  syncError:            null,
});
"""
