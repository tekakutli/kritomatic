"""Layout playground's panel body and CSS."""
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
  #ui button#addTextBboxBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#addTextBboxBtn:hover:not(:disabled) {
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
    max-height: 260px;
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
    border-color:rgba(0,229,255,0.65);
    color:#b8ecff;
    background:rgba(0,229,255,0.06);
  }
  .bboxSwatch {
    width:9px; height:9px;
    border-radius:1px;
    flex:0 0 auto;
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
  .bboxTextInput {
    flex: 1 1 60px;
    min-width: 40px;
    background: transparent;
    border: 0;
    border-bottom: 1px solid #223040;
    color: #b8ecff;
    font-family: inherit;
    font-size: 10px;
    padding: 1px 3px;
    outline: none;
    letter-spacing: 0.02em;
  }
  .bboxTextInput:hover { border-bottom-color:#34445a; }
  .bboxTextInput:focus { border-bottom-color:#00e5ff; }
  .bboxTextInput::placeholder { color:#3d4756; font-style:italic; }
  .bboxMagiSel {
    flex: 1 1 60px;
    min-width: 60px;
    background: #0e1622;
    color: #ffd89b;
    border: 1px solid #223040;
    font-family: inherit;
    font-size: 10px;
    padding: 1px 2px;
    border-radius: 2px;
    outline: none;
    cursor: pointer;
  }
  .bboxMagiSel:hover:not(:disabled) { border-color: #34445a; color: #ffe6b8; }
  .bboxMagiSel:focus { border-color: #ffc85a; }
  .bboxMagiSel:disabled {
    color: #3d4756;
    border-color: #1e2836;
    cursor: not-allowed;
    font-style: italic;
  }
  .bboxEmbed {
    flex: 0 0 auto;
    font-size: 9px;
    font-variant-numeric: tabular-nums;
    color: #5a6774;
  }
  .bboxEmbed.yes { color: #00e5ff; }
  .bboxEmbed.no  { color: #3d4756; font-style: italic; }
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

  #promptText {
    width: 100%;
    background: #0d1219;
    color: #00e5ff;
    border: 1px solid #223040;
    border-radius: 2px;
    font-family: inherit;
    font-size: 11px;
    padding: 4px 6px;
    outline: none;
    resize: vertical;
    min-height: 60px;
    box-sizing: border-box;
  }
  #promptText:focus { border-color: #00e5ff; }
  #promptText::placeholder {
    color: #3d4756;
    font-style: italic;
  }
  #seedInput {
    flex: 1 1 auto;
    min-width: 0;
    background: transparent;
    border: 0;
    border-bottom: 1px solid #223040;
    color: #00e5ff;
    font-family: inherit;
    font-size: 11px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    padding: 2px 4px;
    outline: none;
  }
  #seedInput:hover { border-bottom-color: #34445a; }
  #seedInput:focus { border-bottom-color: #00e5ff; }
  #seedInput::placeholder {
    color: #3d4756;
    font-style: italic;
    font-weight: normal;
  }
  .lastSeedLine {
    font-size: 9px;
    color: #5a6774;
    margin: 4px 0 2px;
    font-variant-numeric: tabular-nums;
    user-select: text;
  }
  .lastSeedLine code {
    color: #00e5ff;
    background: transparent;
    padding: 0;
    font-family: inherit;
    user-select: text;
  }
  .lastSeedLine code[data-empty="1"] {
    color: #3d4756;
  }
  .lastSeedLine .copy-hint {
    color: #3d4756;
    font-style: italic;
  }
"""
PG_PANEL_BODY = r"""
    <details class="section" open>
      <summary>Generate</summary>
      <div class="section-body">
        <div class="field" style="flex-direction:column;align-items:stretch;">
          <label style="text-align:left;flex:0 0 auto;margin-bottom:4px;">
            Prompt
          </label>
          <textarea id="promptText" rows="3" spellcheck="false"
                    placeholder="manga panel, black and white, ..."
          ></textarea>
        </div>
        <div class="field" style="margin-top:6px;">
          <label>Seed</label>
          <input type="text" id="seedInput" value=""
                 placeholder="random" autocomplete="off" spellcheck="false">
        </div>
        <div class="row" style="margin-top:8px;">
          <button id="generateBtn" disabled>Generate</button>
        </div>
        <div id="generateStatus" style="font-size:10px;color:#5a6774;margin-top:6px;min-height:1.4em;">
          idle
        </div>
      </div>
    </details>
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
                  title="Re-fetch the layer selected at @thelayout.">
            ↻ Refresh @thelayout
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
      <summary>Generation Frame</summary>
      <div class="section-body">
        <div class="field">
          <label>Ratio</label>
          <select id="viewerRatio"></select>
        </div>
      </div>
    </details>
    <details class="section" open>
      <summary>Layout</summary>
      <div class="section-body">
        <div class="row">
          <button id="addTextBboxBtn"
                  title="Add a text region (speech bubble / caption). Text boxes are drawn as blue outlines."
                  disabled>+ Text region</button>
          <button id="addCharacterBboxBtn"
                  title="Add a character region. Character boxes are drawn as yellow outlines. Pair each one with a magi character to control identity."
                  disabled>+ Character</button>
        </div>
        <div class="row">
          <button id="seedDefaultBtn"
                  title="Populate with the default layout (caption top + character centre)."
                  disabled>Seed default</button>
          <button id="clearBboxesBtn"
                  title="Remove all text and character bboxes."
                  disabled>Clear all</button>
        </div>
        <div id="bboxList" class="bboxList"></div>
      </div>
    </details>
    <details class="section" open>
      <summary>State</summary>
      <div class="section-body">
        <div id="stateInfo">
          Served at <code>/state</code> for the
          <code>DrawatoonSetIP</code> node. Version:
          <code id="stateVersion">—</code>
        </div>
        <div class="lastSeedLine">
          Last used seed:
          <code id="lastSeedDisplay" data-empty="1">&mdash;</code>
          <span class="copy-hint">(click to copy)</span>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""