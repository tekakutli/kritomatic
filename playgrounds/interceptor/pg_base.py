"""Interceptor's panel body and CSS.

Sections: Tasks, Selected task, Multimask areas, Canvas.

The Selected task section shows the workflow name, params, crop
bounds, and (if present) warnings from the daemon, plus the prompt
textarea and the Run / Discard buttons.  After Run returns, the
result line (pasted layers, timing) appears below the buttons.

The Multimask areas section is populated when the daemon sends a
multimask snapshot (i.e. the intercepted workflow uses a
`MultiAreaConditioning` node).  Each area gets a card with a tinted
swatch (same per-area colour scheme multimask itself uses), the
area's name and dimensions, and an editable prompt textarea.  The
edited prompts are pushed to multimask's `/state` by
`interceptor_server.handle_run` before the daemon resumes, so
`MultiAreaConditioning` sees exactly what the user reviewed.

Overlay defaults
================
Three toggles live in the Canvas section:

  · selection    — cyan tint at 45% (what the model edits).
  · context      — purple tint at 25% (what region the workflow
                   crops to).
  · multimask    — each area's own per-area tint at 30%, drawn
                   from the snapshot's transformed masks.  Only
                   shown when a multimask snapshot exists.

Selection and context default OFF: the canvas shows the raw
prepared image unless the user opts in.  Multimask defaults ON when
a snapshot is present, because seeing the area masks is the whole
point of reviewing a multimask workflow.

CANVAS INTERACTION (hinted in the panel, implemented in
`pg_dispatch.js`):

    wheel            zoom at cursor
    drag             pan
    space + drag     pan
    middle drag      pan
    Home / 0         reset view

OVERLAY CANVAS
==============
`#overlayCanvas` is created dynamically by `pg_state.py`.  Its CSS
lives here.  It sits above `#c` (image layer), has a transparent
background so the image shows through, and `pointer-events: none`
so all mouse input still lands on `#c` where the event handlers
are attached.

The `#overlayCanvas` selector has higher specificity than the
shared `canvas { … }` rule in `pgkit/styles.py`, so
`background: transparent` wins over the shared
`background: #0a0e14`.  Without that override the overlay would be
opaque dark and hide the image layer entirely.
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
    flex-wrap: wrap;
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

  /* ---- Multimask areas ---- */
  #multimaskInfo {
    font-size:10px; color:#5a6774;
    line-height:1.5; margin-bottom:6px;
    white-space: pre-line; overflow-wrap: anywhere;
  }
  #multimaskInfo.warn { color: #ffe600; }

  .mmAreaList {
    display: flex; flex-direction: column;
    gap: 6px; margin-top: 6px;
    max-height: 320px; overflow-y: auto;
  }
  .mmAreaList::-webkit-scrollbar { width:6px; }
  .mmAreaList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .mmAreaList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }

  .mmAreaCard {
    border:1px solid #223040;
    border-radius:3px;
    padding:5px 6px;
    background: rgba(220,230,242,0.015);
    display:flex; flex-direction:column; gap:5px;
  }
  .mmAreaCard:hover { border-color:#34445a; }

  .mmAreaHeader {
    display:flex; align-items:center; gap:6px;
  }
  .mmAreaSwatch {
    width:12px; height:12px; border-radius:2px;
    flex:0 0 auto;
    border:1px solid rgba(255,255,255,0.10);
  }
  .mmAreaName {
    flex:1 1 auto; min-width:0;
    color:#dce6f2; font-weight:700; font-size:11px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .mmAreaDims {
    flex:0 0 auto;
    font-size:9px; color:#5a6774;
    font-variant-numeric: tabular-nums;
  }
  .mmAreaPrompt {
    width:100%; box-sizing:border-box;
    background:#0d1219; color:#00e5ff;
    border:1px solid #223040; border-radius:2px;
    font-family:inherit; font-size:11px;
    padding:4px 6px; outline:none;
    resize:vertical; min-height: 34px;
  }
  .mmAreaPrompt:focus { border-color:#00e5ff; }

  /* ---- overlay layer (tints + placeholder) ----
     Created dynamically by pg_state.py.  Sits on top of #c, is
     transparent so the image shows through, and passes all pointer
     events through to #c.  Specificity (100) beats the shared
     `canvas { … }` rule (1), so `background: transparent` wins
     over the shared `background: #0a0e14`. */
  #overlayCanvas {
    position: absolute;
    top: 0;
    left: 0;
    background: transparent;
    pointer-events: none;
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

    <details class="section" open id="multimaskSection" style="display:none;">
      <summary>Multimask areas</summary>
      <div class="section-body">
        <div id="multimaskInfo"></div>
        <div id="multimaskList" class="mmAreaList"></div>
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
          <label class="check" id="showMultimaskLabel" style="display:none;">
            <input type="checkbox" id="showMultimask">
            multimask
          </label>
        </div>
      </div>
    </details>
"""