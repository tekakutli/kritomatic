"""Interceptor's panel body and CSS.

The prompt is not shown as a text field.  It is still registered as the
`intercept.prompt` pgkit field (see `pg_panel_bindings.py`) so a
scripting client can override it before triggering `action.run`.  When
no override is set, the interceptor sends the payload's original prompt
back to `handle_inpaint` unchanged.
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
"""