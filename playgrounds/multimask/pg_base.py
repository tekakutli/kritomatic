"""Multimask's panel CSS and body."""

PG_CSS = r"""
  #ui button#importMasksBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#importMasksBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#blankAreaBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecc8;
  }
  #ui button#blankAreaBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 160, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
  }
  #ui button#clearAreasBtn {
    border-color: rgba(255, 105, 125, 0.55);
    color: #ffb0bc;
  }
  #ui button#clearAreasBtn:hover:not(:disabled) {
    border-color: rgba(255, 130, 150, 1.00);
    background: rgba(220, 90, 110, 0.10);
    color: #ffd0d8;
  }
  #ui button#importImageBtn,
  #ui button#pickImageBtn,
  #ui button#clearImageBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#importImageBtn:hover:not(:disabled),
  #ui button#pickImageBtn:hover:not(:disabled),
  #ui button#clearImageBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#exportGraphBtn {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#exportGraphBtn:hover:not(:disabled) {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
  }
  #ui button#fetchHistoryBtn {
    border-color: rgba(140, 225, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#fetchHistoryBtn:hover:not(:disabled) {
    border-color: rgba(160, 230, 255, 1.00);
    background: rgba(140, 225, 255, 0.10);
    color: #d8f4ff;
  }

  #imageInfo {
    font-size:10px; color:#5a6774;
    margin:4px 0;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }

  #kritaStatus {
    font-size: 10px;
    color: #5a6774;
    margin-left: 8px;
  }
  #kritaStatus.ok   { color: #00e5ff; }
  #kritaStatus.bad  { color: #d48590; }
  #kritaStatus.warn { color: #ffe600; }

  .areaList {
    display:flex; flex-direction:column; gap:6px;
    margin:6px 0 0;
    max-height: 360px;
    overflow-y: auto;
  }
  .areaList::-webkit-scrollbar { width:6px; }
  .areaList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .areaList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }

  .areaCard {
    border:1px solid #223040;
    border-radius:3px;
    padding:5px 6px;
    background: rgba(220,230,242,0.015);
    display:flex; flex-direction:column; gap:5px;
  }
  .areaCard:hover { border-color:#34445a; }

  .areaHeader {
    display:flex; align-items:center; gap:6px;
  }
  .areaSwatch {
    width:12px; height:12px; border-radius:2px;
    flex:0 0 auto;
    border:1px solid rgba(255,255,255,0.10);
  }
  .areaName {
    flex:1 1 auto; min-width:0;
    background:transparent;
    border:0;
    border-bottom:1px solid transparent;
    color:#dce6f2;
    font-family:inherit;
    font-size:11px;
    font-weight:700;
    padding:1px 2px;
    outline:none;
  }
  .areaName:hover { border-bottom-color:#223040; }
  .areaName:focus { border-bottom-color:#00e5ff; }
  .areaDims {
    flex:0 0 auto;
    font-size:9px; color:#5a6774;
    font-variant-numeric: tabular-nums;
  }
  .areaDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
  }
  .areaDel:hover {
    background:rgba(212,133,144,0.10) !important;
    border-color:#7a3e4a !important;
    color:#e8a0a8 !important;
  }

  .areaPrompt {
    width:100%; box-sizing:border-box;
    background:#0d1219;
    color:#00e5ff;
    border:1px solid #223040;
    border-radius:2px;
    font-family:inherit; font-size:11px;
    padding:4px 6px;
    outline:none;
    resize:vertical;
    min-height: 34px;
  }
  .areaPrompt:focus { border-color:#00e5ff; }

  .areaFlags {
    display:flex; align-items:center; gap:6px;
    font-size:10px; color:#7b8794;
    cursor:pointer; user-select:none;
  }
  .areaFlags input[type=checkbox] {
    accent-color:#00e5ff;
    cursor:pointer; margin:0;
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

  #statusLine {
    font-size:10px; color:#7b8794;
    margin-top:6px;
    font-variant-numeric: tabular-nums;
    overflow-wrap:anywhere; line-height:1.5;
  }
  #statusLine.ok   { color:#00e5ff; }
  #statusLine.bad  { color:#d48590; }
  #statusLine.warn { color:#ffe600; }

  #stateInfo {
    font-size:10px; color:#5a6774; line-height:1.5;
    margin-bottom:6px;
  }
  #stateInfo code { color:#00e5ff; }

  #lastRunInfo {
    font-size:9px; color:#5a6774;
    margin-top:6px; line-height:1.5;
    overflow-wrap:anywhere;
    white-space: pre-line;
  }
  #lastRunInfo.ok  { color: #7b8794; }
  #lastRunInfo.bad { color: #d48590; }
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
          <button id="importImageBtn" title="Import the active document's composite from Krita">
            + From Krita
          </button>
        </div>
        <div id="imageInfo">No image loaded</div>
        <div class="row" style="margin-top:4px;">
          <button id="kritaCheckBtn">Check Krita</button>
          <span id="kritaStatus">not checked</span>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Areas <span id="areaCount" style="color:#00e5ff;margin-left:4px;">0</span></summary>
      <div class="section-body">
        <div class="row">
          <button id="importMasksBtn"
                  title="Import each selected mask or paint layer from Krita as an area. Adds to the existing list.">
            + From Krita
          </button>
          <button id="blankAreaBtn"
                  title="Add a fully-white mask at image dimensions">
            + Blank
          </button>
        </div>
        <div class="row">
          <button id="clearAreasBtn"
                  title="Remove every area (mask + prompt) from the list. The image is left untouched.">
            Clear all areas
          </button>
        </div>
        <div id="areaList" class="areaList"></div>
      </div>
    </details>

    <details class="section">
      <summary>Defaults</summary>
      <div class="section-body">
        <div class="field">
          <label>Combine</label>
          <select id="combineModeSelect">
            <option value="combine">combine</option>
            <option value="concat">concat</option>
          </select>
        </div>
        <div class="field">
          <label>Strength</label>
          <input type="number" id="defaultStrengthInput"
                 min="0" max="1" step="0.05" value="0.9"
                 style="flex:1 1 auto;background:#0d1219;color:#00e5ff;
                        border:1px solid #223040;border-radius:2px;
                        font-family:inherit;font-size:11px;
                        padding:3px 6px;outline:none;">
        </div>
        <div class="field">
          <label>Bbox crop</label>
          <span style="display:flex;gap:6px;align-items:center;flex:1 1 auto;justify-content:flex-end;">
            <span style="font-size:9px;color:#5a6774;">align with workflow crop</span>
            <input type="checkbox" id="bboxCheckbox"
                   title="Crop each area's mask to bbox(context_mask ∪ selection_mask) + crop_padding. Enable this when the workflow crops the canvas (e.g. InpaintCropImproved) so area masks align with the sampler's latent.">
          </span>
        </div>
      </div>
    </details>

    <details class="section">
      <summary>State</summary>
      <div class="section-body">
        <div id="stateInfo">
          Served at <code>/state</code> for the
          <code>MultiAreaConditioning</code> node.
          Version: <code id="stateVersion">—</code>
        </div>
        <div class="row">
          <button id="exportGraphBtn"
                  title="Download the API-format JSON of the graph the node would build from the current state.">
            Export graph JSON
          </button>
        </div>
        <div class="row">
          <button id="fetchHistoryBtn"
                  title="Fetch the last ComfyUI run from the native /history endpoint and download it as JSON. Works only when ComfyUI is reachable.">
            Fetch last run
          </button>
        </div>
        <div class="row">
          <button id="saveStateBtn">Save state</button>
          <button id="loadStateBtn">Load state</button>
        </div>
        <div id="lastRunInfo"></div>
        <div style="font-size:9px;color:#3d4756;margin-top:6px;line-height:1.5;">
          Export shows what the node <em>would</em> build from the
          current state. Fetch downloads the <em>submitted</em> graph
          of the last ComfyUI run — expansion nodes created at
          execution time do not appear there.
        </div>
      </div>
    </details>

    <div id="statusLine">idle</div>
"""
