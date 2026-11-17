"""Controlnets playground's panel CSS and body.

Sections: Image, Controlnets, State.

Image loading is `@thecontrolnet`-only — no local file pick, no
clear, no Krita composite import.  The selector picked in the
docker's layer-picker row is the sole source of truth, so what's on
the canvas is always what the future node will fetch.

The Controlnets section is populated by the `Preprocess images`
button: it runs every preprocessor wired into a `ControlNetApply*`
node in `data/controlnets/controlnets_workflow.json` against
`@thecontrolnet` and lists each preprocessor's title as a clickable
tag.  Clicking a tag shows that preprocessed image on the canvas
(letterboxed into the same fit rect); clicking `Back to
@thecontrolnet` restores the source image.

Each ready tag also carries an enable checkbox.  Unchecked
controlnets are omitted from `/state`, so `ControlNetsConditioning`
in ComfyUI skips their chain step for the next generation.  The row
is dimmed and the title struck through to signal "present but not
being applied".  Errors / running / pending rows have no checkbox
and cannot be toggled.

The `Push to Krita` button copies every done controlnet into a
scratch Krita document (`kritomatic_controlnets`), one layer per
image, layer names == preprocessor titles.  `Refresh from Krita`
pulls those layers back and updates the playground's cached images.
"""

PG_CSS = r"""
  #ui button#refreshCanvasBtn,
  #ui button#preprocessBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#refreshCanvasBtn:hover:not(:disabled),
  #ui button#preprocessBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#preprocessBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  #ui button#pushToKritaBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecc8;
  }
  #ui button#pushToKritaBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 160, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
  }
  #ui button#pushToKritaBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  #ui button#refreshFromKritaBtn {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#refreshFromKritaBtn:hover:not(:disabled) {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
  }
  #ui button#refreshFromKritaBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  #ui button#backToControlnetBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecc8;
  }
  #ui button#backToControlnetBtn:hover:not(:disabled) {
    border-color: rgba(140, 225, 160, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
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

  .controlnetList {
    display:flex; flex-direction:column; gap:3px;
    margin-top:6px;
    max-height: 240px;
    overflow-y: auto;
  }
  .controlnetList::-webkit-scrollbar { width:6px; }
  .controlnetList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .controlnetList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .controlnetRow {
    display:flex; align-items:center; gap:8px;
    padding:3px 6px;
    border:1px solid #223040;
    border-radius:2px;
    font-size:10px;
    color:#a8b5c4;
    letter-spacing:0.04em;
    flex: 0 0 auto;
  }
  .controlnetRow[data-clickable="1"] {
    cursor:pointer;
  }
  .controlnetRow[data-clickable="1"]:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .controlnetRow.selected {
    border-color:rgba(0,229,255,0.65);
    color:#b8ecff;
    background:rgba(0,229,255,0.06);
  }
  .controlnetRow input[type=checkbox] {
    flex: 0 0 auto;
    accent-color: #00e5ff;
    cursor: pointer;
    margin: 0;
    width: 13px;
    height: 13px;
  }
  .controlnetRow.disabled { opacity: 0.45; }
  .controlnetRow.disabled .controlnetTitle {
    text-decoration: line-through;
    text-decoration-color: rgba(212,133,144,0.55);
  }
  .controlnetTitle {
    flex: 1 1 auto;
    font-weight:700;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .controlnetStatus {
    flex: 0 0 auto;
    font-size:9px;
    letter-spacing:0.10em;
    text-transform:uppercase;
    font-weight:700;
  }
  .controlnetStatus.pending { color: #ffe600; }
  .controlnetStatus.running { color: #ffc966; }
  .controlnetStatus.done    { color: #00e5ff; }
  .controlnetStatus.error   { color: #d48590; }
"""


PG_PANEL_BODY = r"""
    <details class="section" open>
      <summary>Image</summary>
      <div class="section-body">
        <div class="row">
          <button id="refreshCanvasBtn"
                  title="Re-fetch the layer selected at @thecontrolnet.">
            ↻ Refresh @thecontrolnet
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
      <summary>Controlnets</summary>
      <div class="section-body">
        <div class="row">
          <button id="preprocessBtn" disabled
                  title="Run every preprocessor wired into a ControlNetApply node in data/controlnets/controlnets_workflow.json, feeding @thecontrolnet in as the source image.">
            Preprocess images
          </button>
        </div>
        <div class="row">
          <button id="pushToKritaBtn" disabled
                  title="Copy every ready controlnet into a scratch Krita document, one layer per image. Layer names match the preprocessor titles.">
            ↑ Push to Krita
          </button>
          <button id="refreshFromKritaBtn" disabled
                  title="Read back the layers from the scratch Krita document and update the cached controlnet images.">
            ↓ Refresh from Krita
          </button>
        </div>
        <div class="row" id="backToControlnetRow" style="display:none;">
          <button id="backToControlnetBtn">
            ← Back to @thecontrolnet
          </button>
        </div>
        <div id="controlnetList" class="controlnetList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>State</summary>
      <div class="section-body">
        <div id="stateInfo">
          Served at <code>/state</code> for the future controlnets node.
          Version: <code id="stateVersion">—</code>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""