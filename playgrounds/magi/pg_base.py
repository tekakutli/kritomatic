"""Magi playground's panel body and CSS.

Sections: Image, Characters, Analysis, State.

No viewer, no prompt, no seed, no generate.  The playground's only
job is: display a reference image, let the user draw character
bounding boxes over it, post to `/analyze_characters` to obtain
768-d embeddings, and expose the result at `/state`."""

PG_CSS = r"""
  #ui button#pickImageBtn,
  #ui button#importImageBtn,
  #ui button#refreshCanvasBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#pickImageBtn:hover:not(:disabled),
  #ui button#importImageBtn:hover:not(:disabled),
  #ui button#refreshCanvasBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#addCharacterBboxBtn {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#addCharacterBboxBtn:hover:not(:disabled) {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
  }
  #ui button#seedDefaultBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecc8;
  }
  #ui button#seedDefaultBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 160, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
  }
  #ui button#clearBboxesBtn {
    border-color: rgba(255, 105, 125, 0.55);
    color: #ffb0bc;
  }
  #ui button#clearBboxesBtn:hover:not(:disabled) {
    border-color: rgba(255, 130, 150, 1.00);
    background: rgba(220, 90, 110, 0.10);
    color: #ffd0d8;
  }
  #ui button#analyzeAllBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#analyzeAllBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
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
  .bboxList {
    display:flex; flex-direction:column; gap:3px;
    margin:6px 0 0;
    max-height: 280px;
    overflow-y: auto;
  }
  .bboxList::-webkit-scrollbar { width:6px; }
  .bboxList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .bboxList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .bboxRow {
    display:flex; align-items:center; gap:7px;
    padding:3px 6px;
    border:1px solid #223040;
    border-radius:2px;
    cursor:pointer;
    font-size:10px;
    color:#a8b5c4;
    letter-spacing:0.04em;
    flex: 0 0 auto;
  }
  .bboxRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .bboxRow.selected {
    border-color:rgba(255,200,90,0.65);
    color:#ffd89b;
    background:rgba(255,200,90,0.06);
  }
  .bboxSwatch {
    width:9px; height:9px;
    border-radius:1px;
    flex:0 0 auto;
    background: rgb(255, 200, 90);
  }
  .bboxSwatch.dim {
    background: #3d4756;
  }
  .bboxLabel {
    flex:0 0 auto; font-weight:700;
    font-variant-numeric: tabular-nums;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .bboxDims {
    flex:0 0 auto;
    font-size:9px; color:#5a6774;
    font-variant-numeric: tabular-nums;
  }
  .bboxEmbed {
    flex:1 1 auto;
    text-align:right;
    font-size:9px;
    font-variant-numeric: tabular-nums;
  }
  .bboxEmbed.yes { color:#00e5ff; }
  .bboxEmbed.no  { color:#3d4756; font-style:italic; }
  .bboxDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
    flex: 0 0 auto;
  }
  .bboxDel:hover {
    background:rgba(212,133,144,0.10) !important;
    border-color:#7a3e4a !important;
    color:#e8a0a8 !important;
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
        <div class="row">
          <button id="refreshCanvasBtn"
                  title="Re-fetch the layer selected at @thecharacter.">
            ↻ Refresh @thecharacter
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
      <summary>Characters</summary>
      <div class="section-body">
        <div class="row">
          <button id="addCharacterBboxBtn"
                  title="Add a character region. Character boxes are drawn as yellow outlines."
                  disabled>+ Character</button>
        </div>
        <div class="row">
          <button id="seedDefaultBtn"
                  title="Populate with a default character box (centre)."
                  disabled>Seed default</button>
          <button id="clearBboxesBtn"
                  title="Remove all character bboxes."
                  disabled>Clear all</button>
        </div>
        <div id="bboxList" class="bboxList"></div>
      </div>
    </details>
    <details class="section" open>
      <summary>Analysis</summary>
      <div class="section-body">
        <div class="row">
          <button id="analyzeAllBtn"
                  title="Send the image and every character bbox to MAGI, then store the 768-d embeddings in state."
                  disabled>Analyze all</button>
        </div>
        <div id="analysisHint" style="font-size:9px;color:#3d4756;font-style:italic;margin-top:4px;line-height:1.45;">
          MAGI integration not yet wired. This button will call the
          MAGI analysis endpoint once the ComfyUI MAGI port lands.
        </div>
      </div>
    </details>
    <details class="section" open>
      <summary>State</summary>
      <div class="section-body">
        <div id="stateInfo">
          Served at <code>/state</code> for
          <code>DrawatoonSetIP</code>. Version:
          <code id="stateVersion">—</code>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""