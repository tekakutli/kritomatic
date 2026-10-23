"""SAM3 playground's panel body and CSS."""

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
  #ui button#inpaintBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  #ui button#runBtn {
    border-color: rgba(0, 229, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#runBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(0, 229, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#runBtn.busy {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#segmentAllBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#segmentAllBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#segmentAllBtn.busy {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
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
  #ui button#undoBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#undoBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#redoBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#redoBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#pickImageBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#exportBtn,
  #ui button#exportCombinedBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecff;
  }
  #ui button#exportBtn:hover:not(:disabled),
  #ui button#exportCombinedBtn:hover:not(:disabled) {
    border-color: rgba(160, 240, 180, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
  }

  /* Export-option checkboxes next to the two Export buttons.
     Small pill-shaped labels so they read as inline toggles rather
     than separate form fields. Checked state uses the same accent
     color as other interactive controls so the "on" state is
     visually consistent with the rest of the panel. */
  .exportOpt {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 10px;
    color: #a8b5c4;
    cursor: pointer;
    padding: 4px 6px;
    border: 1px solid #223040;
    border-radius: 2px;
    background: transparent;
    letter-spacing: 0.02em;
    user-select: none;
  }
  .exportOpt:hover {
    border-color: #34445a;
    color: #dce6f2;
  }
  .exportOpt input[type=checkbox] {
    margin: 0;
    width: 12px;
    height: 12px;
    accent-color: #00e5ff;
  }
  .exportOpt input[type=checkbox]:disabled {
    opacity: 0.4;
  }
  .exportOpt:has(input[type=checkbox]:disabled) {
    opacity: 0.55;
    cursor: not-allowed;
  }

  /* Export to Krita */
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
  #ui button#kritaSelectionBtn,
  #ui button#kritaTransparencyBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecff;
  }
  #ui button#kritaSelectionBtn:hover:not(:disabled),
  #ui button#kritaTransparencyBtn:hover:not(:disabled) {
    border-color: rgba(160, 240, 180, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
  }
  #ui button#kritaExtractNewLayerBtn,
  #ui button#kritaExtractDuplicateBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#kritaExtractNewLayerBtn:hover:not(:disabled),
  #ui button#kritaExtractDuplicateBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#kritaImportBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecff;
  }
  #ui button#kritaImportBtn:hover:not(:disabled) {
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
  #kritaStatus {
    font-size: 10px;
    color: #5a6774;
    margin-left: 8px;
  }
  #kritaStatus.ok   { color: #00e5ff; }
  #kritaStatus.bad  { color: #d48590; }
  #kritaStatus.warn { color: #ffe600; }

  #maskPromptRow textarea {
    width:100%;background:#0d1219;color:#00e5ff;
    border:1px solid #223040;border-radius:2px;
    font-family:inherit;font-size:12px;
    padding:4px 6px;outline:none;resize:vertical;
  }

  /* Sub-header inside a section — small uppercase label that groups
     related controls. Used to split "Edge cleanup", "Selection",
     "Transparency mask", and "Extract" inside the Export to Krita
     block. */
  .section-subhead {
    font-size: 9px;
    color: #5a6774;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-weight: 700;
    margin: 10px 0 4px;
    padding: 4px 0 2px;
    border-top: 1px solid #1e2836;
  }
  .section-subhead:first-child {
    margin-top: 4px;
    border-top: none;
    padding-top: 0;
  }

  .modelRow, .toolRow {
    display: flex; gap: 12px; align-items: center;
    margin: 4px 0 2px;
    font-size: 11px;
    flex-wrap: wrap;
  }
  .modelRow label, .toolRow label {
    display: flex; align-items: center; gap: 4px;
    cursor: pointer; color: #a8b5c4;
  }
  .modelRow input[type=radio], .toolRow input[type=radio] {
    accent-color: #00e5ff;
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
          <button id="kritaImportBtn"
                  title="Import the active document's composite from Krita as the playground image">
            + Import image from Krita
          </button>
        </div>
        <div class="row">
          <button id="kritaImportMasksBtn"
                  title="Import the selected mask or paint layer(s) from Krita into the playground's mask stack. If multiple layers are selected in Krita, all are imported. Paint layers contribute their alpha channel; mask layers contribute their grayscale pixels.">
            + Import masks from Krita
          </button>
        </div>
        <div id="imageInfo">No image loaded — drop one on the canvas</div>
      </div>
    </details>

    <details class="section" open>
      <summary>Prompt</summary>
      <div class="section-body">
        <div class="field" style="align-items:flex-start;flex-direction:column;">
          <label style="text-align:left;flex:0 0 auto;margin-bottom:4px;">
            Global prompt <span style="color:#3d4756;">(Segment All)</span>
          </label>
          <textarea id="promptText" rows="2" spellcheck="false"
                    placeholder="woman, hair, eyes, legs"
                    style="width:100%;background:#0d1219;color:#00e5ff;
                           border:1px solid #223040;border-radius:2px;
                           font-family:inherit;font-size:12px;
                           padding:4px 6px;outline:none;resize:vertical;"
          ></textarea>
        </div>
        <div class="field" id="maskPromptRow" style="display:none;align-items:flex-start;flex-direction:column;margin-top:6px;">
          <label style="text-align:left;flex:0 0 auto;margin-bottom:4px;">
            Mask prompt <span style="color:#3d4756;">(SAM3 refine only)</span>
          </label>
          <textarea id="maskPromptText" rows="2" spellcheck="false"
                    placeholder="prompt used only when refining this mask"
          ></textarea>
        </div>
        <div class="field" style="margin-top:6px;">
          <label id="pointsLabel">Points</label>
          <span id="pointsInfo" style="font-size:10px;color:#7b8794;">0 positive · 0 negative</span>
          <button id="clearPointsBtn" style="flex:0 0 auto;margin-left:auto;">Clear</button>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Settings</summary>
      <div class="section-body">
        <div class="field">
          <label>Threshold</label>
          <input type="text" id="thresholdVal" class="scrubInput"
                 value="0.50" autocomplete="off" spellcheck="false">
        </div>
        <div class="field">
          <label>Refine</label>
          <input type="text" id="refineVal" class="scrubInput"
                 value="2" autocomplete="off" spellcheck="false">
        </div>
        <div class="field">
          <label>Individual</label>
          <input type="checkbox" id="individualMasks" checked
                 title="One mask per detected object (Segment All only).">
        </div>
        <div class="field">
          <label>Auto-run</label>
          <input type="checkbox" id="autoRun"
                 title="Auto-refine 400 ms after any point change on the selected mask.">
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Refine region</summary>
      <div class="section-body">
        <div class="field">
          <label>Paint</label>
          <input type="checkbox" id="regionMode"
                 title="When on, edit the region on the canvas. Refine only rewrites pixels inside it.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">edit region</span>
        </div>
        <div class="toolRow" style="margin-top:4px;">
          <span style="color:#5a6774;font-size:9px;letter-spacing:0.14em;text-transform:uppercase;font-weight:700;">Tool</span>
          <label><input type="radio" name="regionTool" id="regionToolBrush" checked> Brush</label>
          <label><input type="radio" name="regionTool" id="regionToolLasso"> Lasso</label>
          <label><input type="radio" name="regionTool" id="regionToolFreehand"> Freehand</label>
        </div>
        <div class="field">
          <label>Subtract</label>
          <input type="checkbox" id="regionSubtract"
                 title="When on, the active tool subtracts from the region instead of adding. Right-drag still erases.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">erase mode</span>
        </div>
        <div class="field" id="regionSizeRow">
          <label>Size</label>
          <input type="text" id="regionSize" class="scrubInput"
                 value="30" autocomplete="off" spellcheck="false">
        </div>
        <div class="row" style="margin-top:4px;">
          <button id="clearRegionBtn" style="flex:0 0 auto;">Clear region</button>
          <span id="regionStatus" style="flex:1 1 auto;font-size:10px;color:#5a6774;margin-left:8px;">no region</span>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Refine</summary>
      <div class="section-body">
        <div class="modelRow">
          <span style="color:#5a6774;font-size:9px;letter-spacing:0.14em;text-transform:uppercase;font-weight:700;">Model</span>
          <label title="SAM2 refines the existing mask incrementally: each click nudges the current mask toward the prompt. Preserves everything the mask already covers.">
            <input type="radio" name="refineModel" id="modelSam2" checked> SAM2 (true refine)
          </label>
          <label title="SAM3 re-detects from points + prompt + bbox. Each click produces a fresh detection, which can move the whole shape rather than nudging it. Useful for re-authoring a bad mask; not incremental.">
            <input type="radio" name="refineModel" id="modelSam3"> SAM3 (re-detect)
          </label>
        </div>
        <div class="runHint" style="margin-top:2px;">
          SAM2 preserves the mask; SAM3 re-detects it. Use SAM3 to
          author a fresh mask, SAM2 to nudge an existing one.
        </div>
        <div class="row">
          <button id="runBtn">Refine</button>
          <button id="segmentAllBtn">Segment All</button>
        </div>
        <div class="row">
          <button id="cancelBtn">Cancel</button>
          <button id="undoBtn">Undo</button>
          <button id="redoBtn">Redo</button>
        </div>
        <div id="statusLine">idle</div>
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
        <div class="row" style="margin-top:6px; flex-wrap:wrap; gap:6px;">
          <button id="exportBtn"
                  title="Export the selected mask(s). With 'To Krita' checked, each becomes a white-on-transparent paint layer in Krita; unchecked, each becomes a PNG file.">
            Export selected
          </button>
          <button id="exportCombinedBtn"
                  title="Export the union of the selected masks. With 'To Krita' checked, becomes a single white-on-transparent paint layer; unchecked, becomes a single PNG file.">
            Export combined
          </button>
        </div>
        <div class="row" style="margin-top:4px; flex-wrap:wrap; gap:6px;">
          <label class="exportOpt"
                 title="Create paint layer(s) in Krita instead of saving PNG files. Each mask becomes a white-on-transparent paint layer.">
            <input type="checkbox" id="exportToKrita" checked>
            To Krita
          </label>
          <label class="exportOpt"
                 title="Place the exported paint layer(s) directly above the currently selected layer in Krita. Uncheck to place at the top of the stack instead. Uses the 'Target layer' field below if filled, otherwise the active layer.">
            <input type="checkbox" id="exportAtCurrentLayer" checked>
            At current layer
          </label>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Export to Krita</summary>
      <div class="section-body">
        <div class="row">
          <button id="kritaCheckBtn">Check Krita</button>
          <span id="kritaStatus">not checked</span>
        </div>
        <div class="field" style="margin-top:4px;">
          <label>Target layer</label>
          <input type="text" id="kritaTargetLayer" autocomplete="off"
                 spellcheck="false" placeholder="(blank = active layer)"
                 style="flex:1 1 auto;min-width:0;background:transparent;
                        border:0;border-bottom:1px solid #223040;
                        color:#00e5ff;font-family:inherit;font-size:12px;
                        font-weight:700;padding:2px 4px;outline:none;">
        </div>

        <div class="section-subhead">Edge cleanup</div>
        <div class="field">
          <label>Feather</label>
          <input type="text" id="kritaFeather" class="scrubInput"
                 value="0" autocomplete="off" spellcheck="false"
                 title="Box-blur the mask edge by N pixels; softens the boundary between kept and dropped pixels.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">px</span>
        </div>
        <div class="field">
          <label>Expand</label>
          <input type="text" id="kritaExpand" class="scrubInput"
                 value="0" autocomplete="off" spellcheck="false"
                 title="Dilate the mask outward by N pixels. Applied before contract.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">px</span>
        </div>
        <div class="field">
          <label>Contract</label>
          <input type="text" id="kritaContract" class="scrubInput"
                 value="0" autocomplete="off" spellcheck="false"
                 title="Erode the mask inward by N pixels. Applied after expand.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">px</span>
        </div>
        <div class="field">
          <label>Smooth</label>
          <input type="text" id="kritaSmooth" class="scrubInput"
                 value="0" autocomplete="off" spellcheck="false"
                 title="Morphological open+close iterations. Removes small islands and fills small holes without moving the overall edge.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">iter</span>
        </div>
        <div class="field">
          <label>Invert</label>
          <input type="checkbox" id="kritaInvert"
                 title="Invert the mask before applying. Use this to keep the region you masked *out*.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">invert mask</span>
        </div>

        <div class="section-subhead">Selection</div>
        <div class="field">
          <label>Sel mode</label>
          <select id="kritaSelMode" title="How the mask combines with the document's current selection.">
            <option value="replace">replace</option>
            <option value="add">add</option>
            <option value="subtract">subtract</option>
            <option value="intersect">intersect</option>
          </select>
        </div>
        <div class="row">
          <button id="kritaSelectionBtn"
                  title="Set the document selection from the selected masks (union)">
            Set as selection
          </button>
        </div>

        <div class="section-subhead">Transparency mask</div>
        <div class="row">
          <button id="kritaTransparencyBtn"
                  title="Add a transparency mask to the target layer using the selected masks (union)">
            Add transparency mask
          </button>
        </div>

        <div class="section-subhead">Extract</div>
        <div class="field">
          <label>Blend</label>
          <select id="kritaBlendMode" title="Blend mode to apply to the extracted layer. Leave on (keep) to inherit the source's blend mode.">
            <option value="">(keep)</option>
            <option value="normal">normal</option>
            <option value="multiply">multiply</option>
            <option value="screen">screen</option>
            <option value="overlay">overlay</option>
            <option value="add">add</option>
            <option value="difference">difference</option>
            <option value="color">color</option>
            <option value="saturation">saturation</option>
            <option value="hue">hue</option>
            <option value="luminosity">luminosity</option>
          </select>
        </div>
        <div class="field">
          <label>Opacity</label>
          <input type="checkbox" id="kritaOpacityEnabled"
                 title="When checked, override the extracted layer's opacity. When unchecked, the extracted layer inherits the source's opacity.">
          <input type="text" id="kritaOpacity" class="scrubInput"
                 value="100" autocomplete="off" spellcheck="false"
                 title="0-100 percent. Applied only when the checkbox to the left is on.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">%</span>
        </div>
        <div class="field">
          <label>Group</label>
          <input type="text" id="kritaGroup" autocomplete="off"
                 spellcheck="false" placeholder="(blank = no group)"
                 style="flex:1 1 auto;min-width:0;background:transparent;
                        border:0;border-bottom:1px solid #223040;
                        color:#00e5ff;font-family:inherit;font-size:12px;
                        font-weight:700;padding:2px 4px;outline:none;"
                 title="If set, move the extracted layer into this group. The group must already exist.">
        </div>
        <div class="row">
          <button id="kritaExtractNewLayerBtn"
                  title="Extract the masked region of the target layer into a fresh paint layer, preserving the source layer.">
            Extract as new layer
          </button>
        </div>
        <div class="row">
          <button id="kritaExtractDuplicateBtn"
                  title="Duplicate the target layer in place, and attach a transparency mask for the masked region.">
            Duplicate with mask
          </button>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Inpainting</summary>
      <div class="section-body">
        <div class="field">
          <label>Enable</label>
          <input type="checkbox" id="inpaintEnabled"
                 title="Show inpainting controls and the context rectangle on the canvas.">
          <span style="font-size:9px;color:#3d4756;margin-left:6px;">inpainting</span>
        </div>
        <div id="inpaintControls" style="display:none;margin-top:6px;">
          <div class="field">
            <label>Name</label>
            <input type="text" id="inpaintName" autocomplete="off"
                   spellcheck="false" placeholder="(optional)"
                   style="flex:1 1 auto;min-width:0;background:transparent;
                          border:0;border-bottom:1px solid #223040;
                          color:#00e5ff;font-family:inherit;font-size:12px;
                          font-weight:700;padding:2px 4px;outline:none;">
          </div>
          <div class="field" style="align-items:flex-start;flex-direction:column;margin-top:6px;">
            <label style="text-align:left;flex:0 0 auto;margin-bottom:4px;">
              Inpaint prompt
            </label>
            <textarea id="inpaintPrompt" rows="2" spellcheck="false"
                      placeholder="what should appear in the context region"
                      style="width:100%;background:#0d1219;color:#00e5ff;
                             border:1px solid #223040;border-radius:2px;
                             font-family:inherit;font-size:12px;
                             padding:4px 6px;outline:none;resize:vertical;"
            ></textarea>
          </div>
          <div class="row" style="margin-top:6px;">
            <button id="inpaintBtn"
                    title="Run inpainting inside the context rectangle. (Placeholder — not yet wired to a backend.)">
              Inpaint
            </button>
          </div>
          <div class="runHint" style="margin-top:4px;">
            Drag the context rectangle's corners or edges in the canvas
            to resize. Hold Shift to resize symmetrically around the
            center. Drag the interior to move it.
          </div>
        </div>
      </div>
    </details>
"""
