"""Outpaint playground's panel body and CSS.

Sections: Image (area dims + refresh + krita check), Viewer Frame
(ratio dropdown), State (status line).

AREA DIM INPUTS
===============
`#areaW` and `#areaH` are scrub inputs (see
`pg_panel.py`'s AREA DIM SCRUB INPUTS).  They are `type="text"`
with `class="scrubInput"` so `_installScrubInput` from
`pgkit/panel_core.js` can attach drag-to-scrub behaviour.  Click
to type an exact value, drag sideways to scrub.  Shift-drag snaps
to 8-px multiples.

The old `type="number"` inputs (with `min="1" max="8192"`) are
gone.  `setAreaDims()` in `pg_state.py` already enforces the real
bounds — area must be at least the viewer size and at least
`VIEWER_MIN_SIZE_PX` — and the scrub input's `write` callback
clamps to a sane upper bound (16384) so an over-scrub can't
produce an absurd area.
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
    margin:6px 0;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  #kritaStatus {
    font-size: 10px;
    color: #5a6774;
    margin-left: 8px;
  }
  #kritaStatus.ok   { color: #00e5ff; }
  #kritaStatus.bad  { color: #d48590; }
  #areaW, #areaH {
    width: 100%;
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
    box-sizing: border-box;
  }
  #areaW:hover, #areaH:hover { border-bottom-color: #34445a; }
  #areaW:focus, #areaH:focus { border-bottom-color: #00e5ff; }
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
        <div class="field">
          <label>Area width</label>
          <input type="text" id="areaW" class="scrubInput"
                 value="1024" autocomplete="off" spellcheck="false"
                 title="Area width in pixels. Drag sideways to scrub; hold Shift to snap to 8 px. Click to type an exact value.">
        </div>
        <div class="field">
          <label>Area height</label>
          <input type="text" id="areaH" class="scrubInput"
                 value="1024" autocomplete="off" spellcheck="false"
                 title="Area height in pixels. Drag sideways to scrub; hold Shift to snap to 8 px. Click to type an exact value.">
        </div>
        <div class="row" style="margin-top:8px;">
          <button id="refreshCanvasBtn"
                  title="Re-fetch the layer selected at @theoutpainted. Use when the source changed in Krita but the autosync poller didn't pick it up — the poller only fires when the @theoutpainted spec changes (active ↔ layer X ↔ layer Y), not when the pixels behind a constant spec change.">
            ↻ Refresh @theoutpainted
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
      <summary>Viewer Frame</summary>
      <div class="section-body">
        <div class="field">
          <label>Ratio</label>
          <select id="viewerRatio"></select>
        </div>
      </div>
    </details>
    <details class="section" open>
      <summary>State</summary>
      <div class="section-body">
        <div id="stateInfo">
          Served at <code>/state</code>. Version:
          <code id="stateVersion">—</code>
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""