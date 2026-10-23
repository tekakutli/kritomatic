"""SAM3 playground's panel body and CSS."""

PG_CSS = r"""
  #ui button#runBtn {
    border-color: rgba(0, 229, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#runBtn:hover {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(0, 229, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#runBtn.busy {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#cancelBtn {
    border-color: rgba(255, 105, 125, 0.55);
    color: #ffb0bc;
  }
  #ui button#cancelBtn:hover {
    border-color: rgba(255, 130, 150, 1.00);
    background: rgba(220, 90, 110, 0.10);
    color: #ffd0d8;
  }
  #ui button#pickImageBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
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
  .maskRow.selected {
    border-color:rgba(0, 229, 255, 0.72);
    color:#b8ecff;
    background:rgba(0, 229, 255, 0.06);
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
"""

PG_PANEL_BODY = r"""
    <details class="section" open>
      <summary>Image</summary>
      <div class="section-body">
        <div class="row">
          <button id="pickImageBtn" title="Pick an image file from the server machine">+ Pick image…</button>
          <button id="clearImageBtn" title="Clear the current image">Clear</button>
        </div>
        <div id="imageInfo">No image loaded — drop one on the canvas</div>
      </div>
    </details>

    <details class="section" open>
      <summary>Prompt</summary>
      <div class="section-body">
        <div class="field" style="align-items:flex-start;flex-direction:column;">
          <label style="text-align:left;flex:0 0 auto;margin-bottom:4px;">Text</label>
          <textarea id="promptText" rows="2" spellcheck="false"
                    placeholder="woman, hair, eyes, legs"
                    style="width:100%;background:#0d1219;color:#00e5ff;
                           border:1px solid #223040;border-radius:2px;
                           font-family:inherit;font-size:12px;
                           padding:4px 6px;outline:none;resize:vertical;"
          ></textarea>
        </div>
        <div class="field">
          <label>Points</label>
          <span id="pointsInfo" style="font-size:10px;color:#7b8794;">
            0 positive · 0 negative
          </span>
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
                 title="When on, SAM3 returns one mask per detected object.
                        When off, a single mask is returned.">
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Run</summary>
      <div class="section-body">
        <div class="row">
          <button id="runBtn">Run</button>
          <button id="cancelBtn">Cancel</button>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>

    <details class="section" open>
      <summary>Masks</summary>
      <div class="section-body">
        <div class="field" style="margin-bottom:2px;">
          <label>Results</label>
          <span id="maskCount" style="font-size:10px;color:#5a6774;">0</span>
        </div>
        <div id="maskList" class="maskList"></div>
      </div>
    </details>
"""
