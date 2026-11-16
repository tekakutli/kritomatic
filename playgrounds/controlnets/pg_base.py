"""Controlnets playground's panel CSS and body.

Sections: Image, Controlnets (placeholder), State.

Image loading is `@thecontrolnet`-only — no local file pick, no
clear, no Krita composite import.  The selector picked in the
docker's layer-picker row is the sole source of truth, so what's on
the canvas is always what the future node will fetch.
"""

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
  .stubNotice {
    font-size:10px; color:#5a6774;
    font-style:italic;
    padding:6px 8px;
    border:1px dashed #1e2836;
    border-radius:2px;
    line-height:1.5;
  }
  .stubNotice code { color:#00e5ff; font-style:normal; }
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
        <div class="stubNotice">
          No controlnets yet — this is the skeleton. Future work will
          populate <code>controlnets[]</code> with entries shaped like
          <code>{id, name, strength, image: {hash, width, height}}</code>
          and a future <code>MultiControlNetConditioning</code> node will
          pull each one's image via a future <code>/controlnet</code>
          route.
        </div>
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