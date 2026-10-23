"""SAM3 playground's panel body and CSS."""

PG_CSS = r"""
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

  #maskPromptRow textarea {
    width:100%;background:#0d1219;color:#00e5ff;
    border:1px solid #223040;border-radius:2px;
    font-family:inherit;font-size:12px;
    padding:4px 6px;outline:none;resize:vertical;
  }

  .modelRow {
    display: flex; gap: 12px; align-items: center;
    margin: 4px 0 2px;
    font-size: 11px;
  }
  .modelRow label {
    display: flex; align-items: center; gap: 4px;
    cursor: pointer; color: #a8b5c4;
  }
  .modelRow input[type=radio] { accent-color: #00e5ff; }

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
      <summary>Refine</summary>
      <div class="section-body">
        <div class="modelRow">
          <span style="color:#5a6774;font-size:9px;letter-spacing:0.14em;text-transform:uppercase;font-weight:700;">Model</span>
          <label><input type="radio" name="refineModel" id="modelSam2" checked> SAM2 (true refine)</label>
          <label><input type="radio" name="refineModel" id="modelSam3"> SAM3 (re-detect)</label>
        </div>
        <div class="row">
          <button id="runBtn">Run</button>
          <button id="segmentAllBtn">Segment All</button>
        </div>
        <div class="row">
          <button id="cancelBtn">Cancel</button>
          <button id="undoBtn">Undo</button>
        </div>
        <div id="statusLine">idle</div>
        <div class="runHint">
          Canvas: click adds positive point, Shift+click adds negative,
          Alt+click selects a mask.<br>
          No mask selected → staging points feed Segment All.<br>
          Mask selected → points go to that mask's refinement.<br>
          Ctrl+Z undoes. M toggles refine model.
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
        <div class="row" style="margin-top:6px;">
          <button id="exportBtn"
                  title="Download each selected mask as a separate PNG">
            Export selected
          </button>
          <button id="exportCombinedBtn"
                  title="Download a single PNG containing the union of selected masks">
            Export combined
          </button>
        </div>
      </div>
    </details>
"""
