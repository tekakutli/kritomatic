"""Interceptor's panel body and CSS.

The prompt is not shown as a text field; it is still registered as
the `intercept.prompt` pgkit field.

CANVAS INTERACTION (hinted in the panel, implemented in
`pg_dispatch.js`):

    wheel            zoom at cursor
    drag empty       pan
    space + drag     pan
    middle drag      pan
    drag edge handle add surrounding context
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
  #ui button#openKritaBtn,
  #ui button#refreshKritaBtn {
    border-color: rgba(140, 225, 160, 0.72);
    color: #b8ecc8;
  }
  #ui button#openKritaBtn:hover:not(:disabled),
  #ui button#refreshKritaBtn:hover:not(:disabled) {
    border-color: rgba(160, 240, 180, 1.00);
    background: rgba(140, 225, 160, 0.10);
    color: #d8ffe0;
  }
  #ui button#openKritaBtn:disabled,
  #ui button#refreshKritaBtn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  #ui button#resetExpansionBtn {
    border-color: rgba(255, 200, 90, 0.55);
    color: #ffd89b;
    font-size: 9px;
    padding: 2px 8px;
    width: auto;
  }
  #ui button#resetExpansionBtn:hover:not(:disabled) {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
  }
  #ui button#resetExpansionBtn:disabled {
    opacity: 0.35;
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

  #viewToggle {
    font-size:10px; color:#5a6774;
    margin:4px 0 4px;
    display:none;
  }
  #viewToggle.visible { display:block; }
  #viewToggle a {
    color:#00e5ff; text-decoration:none; cursor:pointer;
    border-bottom:1px dotted #00e5ff;
  }
  #viewToggle a:hover { color:#b8ecff; }

  #taskInfo {
    font-size:10px; color:#5a6774;
    line-height:1.5;
    margin-bottom:6px;
    white-space: pre-line;
    overflow-wrap: anywhere;
  }

  #expansionInfo {
    font-size:10px;
    line-height:1.5;
    margin-bottom:6px;
    color:#7b8794;
    white-space: pre-line;
  }
  #expansionInfo.active { color:#cbbdf5; }
  #expansionInfo.warn   { color:#ffe600; }
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
        <div id="viewToggle">
          <a id="toggleViewLink">show result</a>
        </div>
        <div class="row" style="margin-top:8px;">
          <button id="runBtn" disabled>Inpaint</button>
          <button id="discardBtn" disabled>Discard</button>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Canvas context</summary>
      <div class="section-body">
        <div id="expansionInfo">
          wheel: zoom  ·  drag: pan  ·  space+drag: pan
          drag boundary handles outward to add context
        </div>
        <div class="row">
          <button id="openKritaBtn" disabled>Open in Krita</button>
          <button id="refreshKritaBtn" disabled>Refresh from Krita</button>
        </div>
        <div class="row">
          <button id="resetExpansionBtn" disabled>Reset expansion</button>
        </div>
      </div>
    </details>
"""