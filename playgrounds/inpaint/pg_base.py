"""Inpaint playground's panel body and CSS.

Sections: Image, View, Masks, Context.  Export-to-Krita is not wired
here; mask editing and export live in `sam3_segment` or in Krita
itself.

The Inpaint button, Cancel button, prompt textarea, and interceptor
routing are all gone.  This playground no longer submits generation —
the docker's Run button does.  This playground's job is to push state
into Krita scratch layers so the docker's run finds the right canvas,
mask, and context rectangle.  See `pg_sync.js`.

The `#inpaintEnabled` checkbox ("Context rectangle") stays: it decides
whether `thecontext` is pushed with a white rectangle or left alone.
When off, the daemon falls back to the `@thecontext` layer's own tight
bbox (if any) or the whole canvas.

The `#statusLine` element is repurposed as the sync status: `Synced
14:22:08 · rotation 15.0° · mask 3/5 · context on`.

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
the overlay would be opaque dark and hide the image layer entirely."""

PG_CSS = r"""
  #ui button#pickImageBtn,
  #ui button#pickMaskBtn,
  #ui button#refreshCanvasBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#pickImageBtn:hover:not(:disabled),
  #ui button#pickMaskBtn:hover:not(:disabled),
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
  #ui button#syncNowBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
    font-size: 9px;
    padding: 2px 8px;
    width: auto;
  }
  #ui button#syncNowBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
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

  #dropOverlay {
    position:fixed; inset:0; z-index:100;
    display:none;
    background:rgba(0,229,255,0.06);
    border:2px dashed rgba(0,229,255,0.65);
    box-sizing:border-box;
    pointer-events:none;
    align-items:center; justify-content:center;
    font-family:'JetBrains Mono','Fira Code','Consolas','SF Mono',monospace;
    font-size:14px; font-weight:700;
    color:#b8ecff;
    letter-spacing:0.16em; text-transform:uppercase;
    text-shadow:0 0 12px rgba(0,229,255,0.55);
  }
  #dropOverlay.visible { display:flex; }

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
          <button id="pickImageBtn" title="Pick an image file">+ Pick image…</button>
          <button id="clearImageBtn" title="Clear the current image">Clear</button>
        </div>
        <div class="row">
          <button id="kritaImportMasksBtn"
                  title="Import the selected mask or paint layer(s) from Krita into the playground's mask stack.">
            + Import masks from Krita
          </button>
        </div>
        <div class="row">
          <button id="pickMaskBtn"
                  title="Pick a PNG from disk as a mask.">
            + Pick mask file…
          </button>
        </div>
        <div class="row">
          <button id="refreshCanvasBtn"
                  title="Re-fetch the current @thecanvas from Krita.">
            ↻ Refresh @thecanvas
          </button>
        </div>
        <div id="imageInfo">No image loaded — drop one on the canvas</div>
        <div class="row" style="margin-top:4px;">
          <button id="kritaCheckBtn">Check Krita</button>
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
                   title="When checked, pushes a white rectangle at the current context rect into @thecontext. When unchecked, @thecontext is left alone — the daemon falls back to that layer's own bbox or the whole canvas.">
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
        <div class="row" style="margin-top:8px;">
          <button id="syncNowBtn"
                  title="Re-push the current state into Krita scratch layers.">
            Sync now
          </button>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""