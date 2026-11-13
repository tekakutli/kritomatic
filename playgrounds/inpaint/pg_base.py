"""Inpaint playground's panel body and CSS.

Three sections: Image, View, Masks, Inpainting.  Export-to-Krita is
not wired here; mask editing and export live in `sam3_segment` or in
Krita itself.

The context rectangle defaults to disabled: on page load the checkbox
is unchecked, the rectangle is not drawn, and the inpaint workflow
uses the full canvas as context.  Checking the box restricts
inpainting to the rectangle drawn on the canvas.

The Inpaint prompt textarea is an *override* on the daemon's
shared-state prompt (the docker / web panel's
`comfy_apply_config` params).  Left blank, the workflow uses whatever
the docker or web panel has set, with the workflow's own
`PrimitiveString` default as the last resort.  See
`inpaint_server.py`'s PROMPT RESOLUTION section."""

PG_CSS = r"""
  #ui button#inpaintBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#inpaintBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#inpaintBtn.busy {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#inpaintBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  #ui button#cancelBtn {
    border-color: rgba(255, 105, 125, 0.55);
    color: #ffb0bc;
  }
  #ui button#cancelBtn:hover:not(:disabled) {
    border-color: rgba(255, 130, 150, 1.00);
    background: rgba(220, 90, 110, 0.10);
    color: #ffd0d8;
  }
  #ui button#pickImageBtn,
  #ui button#pickMaskBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#pickImageBtn:hover:not(:disabled),
  #ui button#pickMaskBtn:hover:not(:disabled) {
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
  #kritaStatus {
    font-size: 10px;
    color: #5a6774;
    margin-left: 8px;
  }
  #kritaStatus.ok   { color: #00e5ff; }
  #kritaStatus.bad  { color: #d48590; }
  #kritaStatus.warn { color: #ffe600; }

  #inpaintPrompt {
    width:100%;background:#0d1219;color:#00e5ff;
    border:1px solid #223040;border-radius:2px;
    font-family:inherit;font-size:12px;
    padding:4px 6px;outline:none;resize:vertical;
  }
  #inpaintPrompt:disabled {
    opacity: 0.5;
  }

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
                  title="Import the selected mask or paint layer(s) from Krita into the playground's mask stack. If multiple layers are selected in Krita, all are imported. Paint layers contribute their alpha channel; mask layers contribute their grayscale pixels.">
            + Import masks from Krita
          </button>
        </div>
        <div class="row">
          <button id="pickMaskBtn"
                  title="Pick a PNG from disk as a mask. White = selected, black = not selected. Resized to the canvas if needed.">
            + Pick mask file…
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
                     title="Rotation in degrees. Drag sideways to scrub; hold Shift to snap to 15&#176;. Click to type an exact value.">
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
      <summary>Inpainting</summary>
      <div class="section-body">
        <div class="field">
          <label>Context rectangle</label>
          <span style="display:flex;gap:6px;align-items:center;">
            <input type="checkbox" id="inpaintEnabled"
                   title="When checked, inpainting is restricted to the rectangle drawn on the canvas. When unchecked, the whole canvas is the context and the rectangle is hidden.">
            <button id="maskFromRectBtn"
                    title="Create a new mask from the current context rectangle (white = inside, black = outside). The new mask is placed at the top of the mask stack and selected."
                    disabled>+ As mask</button>
          </span>
        </div>
        <div id="inpaintRectHint" class="runHint"
             style="margin-top:4px;display:none;">
          Drag anywhere on the context rectangle's border or interior
          to move it. Drag a handle (four corners, four edge midpoints)
          to resize. Hold Shift while dragging a handle to resize
          symmetrically around the center.
        </div>
        <div class="field" style="align-items:flex-start;flex-direction:column;margin-top:6px;">
          <label style="text-align:left;flex:0 0 auto;margin-bottom:4px;">
            Inpaint prompt
          </label>
          <textarea id="inpaintPrompt" rows="2" spellcheck="false"
                    placeholder="what should appear (blank = use docker / web panel prompt)"
          ></textarea>
        </div>
        <div class="field" style="align-items:flex-start;flex-direction:column;margin-top:8px;">
          <label style="display:flex;gap:6px;align-items:center;cursor:pointer;flex:0 0 auto;">
            <input type="checkbox" id="interceptEnabled"
                   title="Send the payload to an interceptor playground instead of running inpaint locally.">
            Route via interceptor
          </label>
        </div>
        <div class="field" id="interceptUrlRow"
             style="display:none;align-items:flex-start;flex-direction:column;margin-top:4px;">
          <input type="text" id="interceptUrl"
                 value="http://127.0.0.1:8775/intercept/intake"
                 style="width:100%;background:#0d1219;color:#00e5ff;
                        border:1px solid #223040;border-radius:2px;
                        font-family:inherit;font-size:11px;
                        padding:4px 6px;outline:none;"
                 autocomplete="off" spellcheck="false">
        </div>
        <div class="row" style="margin-top:8px;">
          <button id="inpaintBtn"
                  title="Run the inpaint workflow with the current canvas, selected mask, and context.">
            Inpaint
          </button>
          <button id="cancelBtn">Cancel</button>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""