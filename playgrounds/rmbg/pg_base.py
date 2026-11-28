"""rmbg playground's panel body and CSS.

Sections: Image, View, Masks, Context.

No rotation scrub field.  No sync-now button (see `pg_sync.py`'s
TRACKER ADVANCE ON SUCCESS ONLY — the next state change retries
a failed delta automatically).  No "Pick image" / "Clear" / drop
overlay — the image is pulled exclusively from the daemon via
autosync or `Refresh @thecanvas`.

The `#contextEnabled` checkbox ("Context rectangle") is a pure
"restrict the operating area" toggle: on means "the context is
my drawn rect, clamped to the alpha-content region"; off means
"the whole alpha-content region".  Both states are communicated
to the server via the `/sync` payload; the server synthesizes
the effective frame from `boundary ∩ context_rect` (or
`boundary` alone when the checkbox is off).

The `#imageInfo` line shows `content W×H (of W×H)` when the
boundary is tighter than the full image, `W×H` otherwise.

OVERLAY LAYER
=============
Single canvas — no separate overlay.  Without rotation, the
two-canvas split from inpaint adds complexity without a payoff."""

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
  #ui button#rebindDocBtn {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
    font-size: 9px;
    padding: 2px 8px;
    width: auto;
  }
  #ui button#rebindDocBtn:hover:not(:disabled) {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
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
  #ui button#resetViewBtn {
    border-color: rgba(120, 220, 255, 0.55);
    color: #b8ecff;
    font-size: 9px;
    padding: 2px 8px;
    width: auto;
  }
  #kritaStatus {
    font-size: 10px;
    color: #5a6774;
    margin-left: 8px;
  }
  #kritaStatus.ok   { color: #00e5ff; }
  #kritaStatus.bad  { color: #d48590; }
  #kritaStatus.warn { color: #ffe600; }

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
          <button id="kritaImportMasksBtn"
                  title="Import the selected mask or paint layer(s) from Krita into the playground's mask stack (cropped to content bounds).">
            + Import masks from Krita
          </button>
        </div>
        <div class="row">
          <button id="refreshCanvasBtn"
                  title="Re-fetch the current @thecanvas from Krita. Use when the canvas changed in Krita but autosync did not pick it up — the poller fires only when the @thecanvas *spec* changes, not when the pixels behind a constant spec change.">
            ↻ Refresh @thecanvas
          </button>
        </div>
        <div id="imageInfo">No image loaded — load a workflow in the docker to pull @thecanvas</div>
        <div class="row" style="margin-top:4px;">
          <button id="kritaCheckBtn">Check Krita</button>
          <button id="rebindDocBtn"
                  title="Re-pin rmbg to whatever document is currently active in Krita.  Use this if another playground or command stole activeDocument() while rmbg was open.">
            ⟲ Rebind doc
          </button>
          <span id="kritaStatus">not checked</span>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>View</summary>
      <div class="section-body">
        <div class="row">
          <button id="resetViewBtn" title="Reset zoom and pan (Home).">Reset view</button>
        </div>
        <div class="runHint">
          The canvas is cropped to the alpha-content bounding box —
          transparent margins are removed.  Everything below operates
          within that boundary.
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
      <summary>Context</summary>
      <div class="section-body">
        <div class="field">
          <label>Context rectangle</label>
          <span style="display:flex;gap:6px;align-items:center;">
            <input type="checkbox" id="contextEnabled"
                   title="When checked, the context rectangle defines the operating region, constrained within the alpha-content boundary. When unchecked, the whole alpha-content region is used.">
            <button id="maskFromRectBtn"
                    title="Create a new mask from the current context rectangle."
                    disabled>+ As mask</button>
          </span>
        </div>
        <div id="contextHint" class="runHint"
             style="margin-top:4px;display:none;">
          Drag anywhere on the context rectangle's border or interior to move it.
          Drag a handle (four corners, four edge midpoints) to resize.
          Hold Shift while dragging a handle to resize symmetrically around the center.
          The rectangle is bounded to the alpha-content region.
        </div>
        <div id="statusLine">idle</div>
      </div>
    </details>
"""