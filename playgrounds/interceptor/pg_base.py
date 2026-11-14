"""Interceptor's panel body and CSS.

Sections: Tasks, Selected task, Canvas.

The Selected task section shows the workflow name, params, crop
bounds, and (if present) warnings from the daemon, plus the prompt
textarea and the Run / Discard buttons.  After Run returns, the
result line (pasted layers, timing) appears below the buttons.

The Canvas section carries:
  · the pan/zoom hint text,
  · two opt-in overlay toggles.

Overlay defaults
================
Both toggles default OFF.  The canvas is drawn as the raw prepared
image the model will receive — no tint, no wash, no annotation.  This
is the "raw image" the reviewer expects to see.

When a toggle is on, the corresponding mask is tinted over the
canvas:
  · selection  → cyan at 45%   (what the model edits)
  · context    → purple at 25% (what region the workflow crops to)

Tinting is useful for verification — "yes, the mask is where I drew
it" — but it obscures the raw image, so it is a review aid, not a
default.  The cropped path's context mask is all-white (whole crop is
context), so its tint is a full-frame wash; the toggle is there so a
user who wants that view can opt into it, but they never see it
unasked.

CANVAS INTERACTION (hinted in the panel, implemented in
`pg_dispatch.js`):

    wheel            zoom at cursor
    drag             pan
    space + drag     pan
    middle drag      pan
    Home / 0         reset view
"""

PG_CSS = r"""
  #ui button#runBtn {
    border-color: rgba(180, 155, 255, 0.72);
    color: #d5c8ff;
  }
  #ui button#runBtn:hover:not(:disabled) {
    border-color: rgba(200, 180, 255, 1.00);
    background: rgba(180, 155, 255, 0.10);
    color: #e8e0ff;
  }
  #ui button#runBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  #ui button#discardBtn {
    border-color: rgba(255, 105, 125, 0.55);
    color: #ffb0bc;
  }
  #ui button#discardBtn:hover:not(:disabled) {
    border-color: rgba(255, 130, 150, 1.00);
    background: rgba(220, 90, 110, 0.10);
    color: #ffd0d8;
  }
  #ui button#discardBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .taskList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:220px; overflow-y:auto;
  }
  .taskList::-webkit-scrollbar { width:6px; }
  .taskList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .taskList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .taskRow {
    display:flex; align-items:center; gap:7px;
    padding:3px 6px;
    border:1px solid #223040;
    border-radius:2px;
    cursor:pointer;
    font-size:10px;
    color:#a8b5c4;
    letter-spacing:0.04em;
    flex:0 0 auto;
  }
  .taskRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .taskRow.selected {
    border-color:rgba(0, 229, 255, 0.72);
    color:#b8ecff;
    background:rgba(0, 229, 255, 0.06);
  }
  .taskLabel {
    flex:1 1 auto; font-weight:700;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .taskStatus {
    flex:0 0 auto; font-size:9px;
    text-transform:uppercase; letter-spacing:0.10em;
    font-weight:700;
  }
  .taskStatus.queued    { color:#ffe600; }
  .taskStatus.running   { color:#ffc966; }
  .taskStatus.done      { color:#00e5ff; }
  .taskStatus.error     { color:#d48590; }
  .taskStatus.cancelled { color:#5a6774; }

  #taskInfo {
    font-size:10px; color:#5a6774;
    line-height:1.5;
    margin-bottom:6px;
    white-space: pre-line;
    overflow-wrap: anywhere;
  }

  #taskPayload {
    font-size:10px;
    line-height:1.5;
    margin-bottom:6px;
    color:#7b8794;
    white-space: pre-line;
    overflow-wrap: anywhere;
  }

  #promptRow {
    margin-top:8px;
  }
  .small-label {
    font-size:9px;
    color:#5a6774;
    text-transform:uppercase;
    letter-spacing:0.10em;
    font-weight:700;
    margin-bottom:4px;
  }
  #promptInput {
    width:100%; box-sizing:border-box;
    background:#0d1219; color:#00e5ff;
    border:1px solid #223040;
    border-radius:2px;
    font-family:inherit; font-size:11px;
    padding:4px 6px;
    outline:none;
    resize:vertical;
    min-height: 34px;
  }
  #promptInput:focus { border-color:#00e5ff; }

  #resultInfo {
    font-size:10px;
    line-height:1.5;
    margin-top:6px;
    color:#7b8794;
    white-space: pre-line;
    overflow-wrap: anywhere;
  }
  #resultInfo.ok  { color: #00e5ff; }
  #resultInfo.err { color: #d48590; }

  .overlayRow {
    display: flex; align-items: center; gap: 12px;
    font-size: 10px; color: #a8b5c4;
    margin-top: 6px;
  }
  .overlayRow .label {
    font-size: 9px;
    color: #5a6774;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 700;
  }
  .overlayRow label.check {
    display: flex; gap: 4px; align-items: center;
    cursor: pointer;
  }
  .overlayRow input[type=checkbox] {
    accent-color: #00e5ff;
    margin: 0;
    cursor: pointer;
  }
"""

PG_PANEL_BODY = r"""
    <details class="section" open>
      <summary>Tasks</summary>
      <div class="section-body">
        <div id="taskList" class="taskList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Selected task</summary>
      <div class="section-body">
        <div id="taskInfo">no task selected</div>
        <div id="taskPayload"></div>
        <div id="promptRow">
          <div class="small-label">Prompt</div>
          <textarea id="promptInput" rows="2" spellcheck="false"
                    placeholder="prompt override (blank = keep original)"
          ></textarea>
        </div>
        <div class="row" style="margin-top:8px;">
          <button id="runBtn" disabled>Inpaint</button>
          <button id="discardBtn" disabled>Discard</button>
        </div>
        <div id="resultInfo"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Canvas</summary>
      <div class="section-body">
        <div style="font-size:9px;color:#3d4756;margin-bottom:6px;
                    line-height:1.5;">
          wheel: zoom  ·  drag: pan  ·  space+drag: pan
        </div>
        <div class="overlayRow">
          <span class="label">Overlays</span>
          <label class="check">
            <input type="checkbox" id="showSelection">
            selection
          </label>
          <label class="check">
            <input type="checkbox" id="showContext">
            context
          </label>
        </div>
      </div>
    </details>
"""