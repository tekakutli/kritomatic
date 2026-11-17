"""Inpaint playground's panel body and CSS.

Sections: Image, View, Masks, Context.  Export-to-Krita is not wired
here; mask editing and export live in `sam3_segment` or in Krita
itself.

The Inpaint button, Cancel button, prompt textarea, and interceptor
routing are all gone.  This playground no longer submits generation —
the docker's Run button does.  This playground's job is to push state
into the inpaint server's cache so the docker's run finds the right
canvas, mask, and context rectangle.  See `pg_sync.js`.

The `+ Pick image…`, `Clear`, and `+ Pick mask file…` buttons and the
drag-drop overlay are gone too.  The image is pulled from the daemon
by `pg_autosync.js`; `Refresh @thecanvas` bypasses the spec-change
gate to re-read when the pixels behind a constant spec changed in
Krita.  Masks come in from `+ Import masks from Krita` and are created
from the context rectangle via `+ As mask`.  There is no local-file
or dropped-file install path — every install is daemon-mediated.

The `#inpaintEnabled` checkbox ("Context rectangle") is a pure
"restrict the inpaint area" toggle: on means "the context is my drawn
rect", off means "the context is the whole @thecanvas".  Both states
are fully determined by the playground server's `/crop_frame`
response — no `@thecontext` layer is written or read; the effective
context rect is communicated as scalar fields on the `/sync` payload
and the server synthesizes `(0, 0, W, H)` when the checkbox is off.
See `inpaint_server.py`'s module docstring, CONTEXT RECT QUERY.

The `#statusLine` element shows sync state: `Synced 14:22:08 ·
rotation 15.0° · mask 3/5 · context on`.

REBIND DOC
==========
`⟲ Rebind doc` (next to Check Krita) is the manual escape hatch for
the pinned-document drift described in `inpaint_server.py`'s module
docstring, PINNED TARGET DOCUMENT.  If another playground (controlnets
Push-to-Krita, in particular) has activated its own doc and inpaint
came up during that window, the pin points at the wrong doc.  The
user clicks Rebind after manually switching Krita back to the doc
they want inpaint to follow; the pin re-captures from activeDocument().

OVERLAY CANVAS
==============
`#overlayCanvas` is created dynamically by `pg_state.py`.  Its CSS
lives here.  It sits on top of `#c` (image layer), has a transparent
background so the image shows through, and `pointer-events: none`
so all mouse input still lands on `#c` where the event handlers are
attached.

The `#overlayCanvas` selector has higher specificity than the shared
`canvas { … }` rule in `pgkit/styles.py`, so `background: transparent`
wins over the shared `background: #0a0e14`.  Without that override
the overlay would be opaque dark and hide the image layer entirely.

SYNC NOW BUTTON
===============
The "Sync now" button was removed.  Every real trigger for a push is
already automatic (debounce on every state change, `syncNow()` on
rotation), and the one case the button should have rescued — a
failed push leaving the client and server diverged — was not
recoverable by clicking it because the change-detection tracker
advanced optimistically before the fetch resolved.  Post-fix (see
`pg_sync.py`'s TRACKER ADVANCE ON SUCCESS ONLY), the next state
change retries the delta automatically, so a dedicated button added
no value.  The `action.syncNow` pgkit action is still registered for
programmatic callers; only the in-panel button is gone."""

PG_CSS = r"""
  #ui button#refreshCanvasBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#refreshCanvasBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#kritaCheckBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecff;
    font-size: 9px;
    padding: 2px 8px;
    width: auto;
  }
  #ui button#kritaCheckBtn:hover:not(:disabled) {
    border-color: rgba(160, 240, 180, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
  }
  #ui button#rebindDocBtn {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
    font-size: 9px;
    padding: 2px 8px;
    width: auto;
  }
  #ui button#rebindDocBtn:hover:not(:disabled) {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
  }
  #ui button#kritaImportMasksBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#kritaImportMasksBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#maskFromRectBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
    font-size: 9px;
    padding: 2px 8px;
    width: auto;
  }
  #ui button#maskFromRectBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#maskFromRectBtn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }
  #kritaStatus {
    font-size: 10px;
    color: #5a6774;
    margin-left: 8px;
  }
  #kritaStatus.ok   { color: #00e5ff; }
  #kritaStatus.bad  { color: #d48590; }
  #kritaStatus.warn { color: #ffe600; }

  .maskList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:180px; overflow-y:auto;
  }
  .maskList::-webkit-scrollbar { width:6px; }
  .maskList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .maskList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .maskRow {
    display:flex; align-items:center; gap:7px;
    padding:3px 6px;
    border:1px solid #223040;
    border-radius:2px;
    cursor:pointer;
    font-size:10px;
    color:#a8b5c4;
    letter-spacing:0.04em;
  }
  .maskRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .maskSwatch {
    width:18px; height:18px;
    border-radius:2px;
    flex:0 0 auto;
    background:#0e1622;
    border:1px solid #34445a;
    image-rendering:pixelated;
  }
  .maskLabel {
    flex:1 1 auto; font-weight:700;
    font-variant-numeric: tabular-nums;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .maskMeta {
    flex:0 0 auto;
    color:#5a6774;
    font-size:9px;
  }
  .maskDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
  }

  #imageInfo {
    font-size:10px; color:#5a6774;
    margin:4px 0;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  #statusLine {
    font-size:10px; color:#7b8794;
    margin-top:6px;
    font-variant-numeric: tabular-nums;
    overflow-wrap:anywhere; line-height:1.5;
  }
  #statusLine.ok   { color:#00e5ff; }
  #statusLine.bad  { color:#d48590; }
  #statusLine.warn { color:#ffe600; }

  .runHint {
    font-size:9px; color:#3d4756; font-style:italic;
    margin: 4px 0 0;
    line-height: 1.45;
  }

  /* ---- overlay layer (UI: context rect + chrome text) ----
     Created dynamically by pg_state.py.  Sits on top of #c, is
     transparent so the image shows through, and passes all pointer
     events through to #c.  Specificity (100) beats the shared
     `canvas { … }` rule (1), so `background: transparent` wins over
     the shared `background: #0a0e14`. */
  #overlayCanvas {
    position: absolute;
    top: 0;
    left: 0;
    background: transparent;
    pointer-events: none;
  }
"""

PG_PANEL_BODY = r"""
    <details class="section" open>
      <summary>Image</summary>
      <div class="section-body">
        <div class="row">
          <button id="kritaImportMasksBtn"
                  title="Import the selected mask or paint layer(s) from Krita into the playground's mask stack.">
            + Import masks from Krita
          </button>
        </div>
        <div class="row">
          <button id="refreshCanvasBtn"
                  title="Re-fetch the current @thecanvas from Krita. Use when the canvas changed in Krita but autosync did not pick it up — the poller fires only when the @thecanvas *spec* changes, not when the pixels behind a constant spec change.">
            ↻ Refresh @thecanvas
          </button>
        </div>
        <div id="imageInfo">No image loaded — load a workflow in the docker to pull @thecanvas</div>
        <div class="row" style="margin-top:4px;">
          <button id="kritaCheckBtn">Check Krita</button>
          <button id="rebindDocBtn"
                  title="Re-pin inpaint to whatever document is currently active in Krita.  Use this if another playground or command stole activeDocument() while inpaint was open.">
            ⟲ Rebind doc
          </button>
          <span id="kritaStatus">not checked</span>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>View</summary>
      <div class="section-body">
        <div class="field">
          <label>Rotation</label>
          <input type="text" id="viewRotation" class="scrubInput"
                 value="0" autocomplete="off" spellcheck="false"
                 title="Rotation in degrees. Drag sideways to scrub; hold Shift to snap to 15°. Click to type an exact value.">
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Masks</summary>
      <div class="section-body">
        <div class="field" style="margin-bottom:2px;">
          <label>Selected</label>
          <span id="maskCount" style="font-size:10px;color:#5a6774;">0 / 0</span>
        </div>
        <div id="maskList" class="maskList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Context</summary>
      <div class="section-body">
        <div class="field">
          <label>Context rectangle</label>
          <span style="display:flex;gap:6px;align-items:center;">
            <input type="checkbox" id="inpaintEnabled"
                   title="When checked, the context rectangle defines the inpaint area. When unchecked, the whole @thecanvas is used as the context — identical to having drawn a context rectangle covering the entire canvas.">
            <button id="maskFromRectBtn"
                    title="Create a new mask from the current context rectangle."
                    disabled>+ As mask</button>
          </span>
        </div>
        <div id="inpaintRectHint" class="runHint"
             style="margin-top:4px;display:none;">
          Drag anywhere on the context rectangle's border or interior to move it.
          Drag a handle (four corners, four edge midpoints) to resize.
          Hold Shift while dragging a handle to resize symmetrically around the center.
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""