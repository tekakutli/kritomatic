"""
rp_base.py — the remote panel's CSS and HTML structure.

Adds a small `#rpMgmtStatus` span to the Manage modal's footer so the
shutdown flow can report state ("waiting for hub acknowledgment…",
"hub is shutting down…", "no acknowledgment within 5s") without the
user having to open the collapsed log panel.  `.rp-modal-status`
carries the color classes used by that span.

Everything else — topbar, actions panel, fields panel, log panel,
action-form modal, state-JSON modal, kill/force buttons, management
list rows — is unchanged.
"""

RP_CSS = r"""
  :root {
    --bg:        #0a0e14;
    --bg-panel:  #0b1018;
    --bg-card:   #0e1622;
    --bg-input:  #0d1219;
    --border:    #1e2836;
    --border-2:  #223040;
    --border-3:  #34445a;
    --text:      #dce6f2;
    --text-dim:  #a8b5c4;
    --text-mute: #5a6774;
    --text-faint:#3d4756;
    --accent:    #00e5ff;
    --accent-2:  #b8ecff;
    --danger:    #d48590;
    --danger-b:  #ffb0bc;
    --danger-a:  #ff5a69;
    --warn:      #ffe600;
    --ok:        #00e5ff;
    --mono: 'JetBrains Mono', 'Fira Code', 'Consolas',
            'SF Mono', monospace;
  }

  * { box-sizing: border-box; }

  html, body {
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    background: var(--bg); color: var(--text);
    font-family: var(--mono);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }

  body {
    display: grid;
    grid-template-rows: auto auto 1fr auto;
    height: 100vh;
  }

  #topbar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 10px 16px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }
  #topbar .title {
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--text);
    white-space: nowrap;
  }
  #topbar .conn {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1 1 auto;
    min-width: 240px;
  }
  #topbar input[type=text] {
    flex: 1 1 auto;
    min-width: 0;
    background: var(--bg-input);
    color: var(--accent);
    border: 1px solid var(--border-2);
    border-radius: 2px;
    padding: 5px 8px;
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 700;
    outline: none;
  }
  #topbar input[type=text]:focus { border-color: var(--accent); }
  #topbar select {
    background: var(--bg-input);
    color: var(--text-dim);
    border: 1px solid var(--border-2);
    border-radius: 2px;
    padding: 5px 6px;
    font-family: var(--mono);
    font-size: 11px;
    outline: none;
    cursor: pointer;
  }
  #topbar select:hover { border-color: var(--border-3); }
  #topbar button {
    padding: 5px 12px;
    cursor: pointer;
    border: 1px solid var(--border-2);
    background: transparent;
    color: var(--text-dim);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    font-weight: 700;
    white-space: nowrap;
    transition: background 0.08s ease, border-color 0.08s ease;
  }
  #topbar button:hover {
    border-color: var(--border-3);
    color: var(--text);
    background: rgba(220,230,242,0.03);
  }
  #topbar button:disabled {
    color: var(--text-faint);
    border-color: var(--border);
    cursor: not-allowed;
  }
  #topbar #rpConnectBtn {
    border-color: rgba(0,229,255,0.55);
    color: var(--accent-2);
  }
  #topbar #rpConnectBtn:hover:not(:disabled) {
    border-color: var(--accent);
    background: rgba(0,229,255,0.08);
  }
  #topbar #rpDisconnectBtn {
    border-color: rgba(255,105,125,0.55);
    color: var(--danger-b);
  }
  #topbar #rpDisconnectBtn:hover:not(:disabled) {
    border-color: var(--danger-a);
    background: rgba(255,90,105,0.08);
  }
  #topbar #rpStateBtn {
    border-color: rgba(0,229,255,0.35);
    color: var(--accent-2);
  }
  #topbar #rpStateBtn:hover:not(:disabled) {
    border-color: var(--accent);
    background: rgba(0,229,255,0.08);
  }
  #topbar #rpMgmtBtn {
    border-color: rgba(255,200,90,0.55);
    color: #ffd89b;
  }
  #topbar #rpMgmtBtn:hover:not(:disabled) {
    border-color: rgba(255,200,90,1.0);
    background: rgba(255,200,90,0.08);
    color: #ffe6b8;
  }

  #topbar .status {
    display: flex;
    align-items: center;
    gap: 10px;
    white-space: nowrap;
  }
  #rpStatus {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: var(--text-mute);
  }
  #rpStatus.ok   { color: var(--accent); }
  #rpStatus.warn { color: var(--warn); }
  #rpStatus.bad  { color: var(--danger); }
  #rpPresence {
    font-size: 10px;
    color: var(--text-mute);
    letter-spacing: 0.02em;
  }

  #actionsPanel {
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-direction: column;
  }
  #actionsPanel.collapsed .body { display: none; }
  #actionsPanel .header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 16px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-mute);
    border-bottom: 1px solid var(--border);
  }
  #actionsPanel .header .label { flex: 1 1 auto; }
  #actionsPanel .header button {
    padding: 2px 8px;
    cursor: pointer;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-mute);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    font-weight: 700;
  }
  #actionsPanel .header button:hover {
    border-color: var(--border-3);
    color: var(--text-dim);
  }
  #actionsPanel .body {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    padding: 10px 16px;
  }
  .action-group {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 8px 10px;
    min-width: 200px;
  }
  .action-group h4 {
    margin: 0 0 6px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
  }
  .action-group .btns {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .action-group .action-items {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 6px;
    border-top: 1px solid var(--border);
    padding-top: 6px;
  }
  .action-item {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 4px;
    border-radius: 2px;
    font-size: 10px;
  }
  .action-item:hover {
    background: rgba(220, 230, 242, 0.04);
  }
  .action-item.selected {
    background: rgba(0, 229, 255, 0.08);
    color: var(--accent-2);
  }
  .action-item-label {
    flex: 1 1 auto;
    cursor: pointer;
    color: var(--text-dim);
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .action-item.selected .action-item-label {
    color: var(--accent-2);
  }
  .action-item-label:hover {
    color: var(--text);
  }

  .action-btn {
    padding: 4px 10px;
    cursor: pointer;
    border: 1px solid var(--border-2);
    background: transparent;
    color: var(--text-dim);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.06em;
    font-weight: 700;
    white-space: nowrap;
    transition: background 0.08s ease, border-color 0.08s ease,
                color 0.08s ease;
  }
  .action-btn:hover:not(:disabled) {
    border-color: var(--border-3);
    color: var(--text);
    background: rgba(220,230,242,0.03);
  }
  .action-btn:active:not(:disabled) {
    background: rgba(220,230,242,0.08);
  }
  .action-btn:disabled {
    color: var(--text-faint);
    border-color: var(--border);
    cursor: not-allowed;
  }
  .action-btn.small {
    padding: 1px 6px;
    font-size: 10px;
  }
  .action-btn.primary {
    border-color: rgba(0,229,255,0.55);
    color: var(--accent-2);
  }
  .action-btn.primary:hover:not(:disabled) {
    border-color: var(--accent);
    background: rgba(0,229,255,0.08);
    color: #d8f4ff;
  }
  .action-btn.danger {
    border-color: rgba(255,105,125,0.55);
    color: var(--danger-b);
  }
  .action-btn.danger:hover:not(:disabled) {
    border-color: var(--danger-a);
    background: rgba(255,90,105,0.08);
    color: #ffd0d8;
  }

  #fieldsPanel {
    overflow-y: auto;
    overflow-x: hidden;
    padding: 12px 16px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    grid-auto-rows: min-content;
    gap: 12px;
    align-content: start;
  }
  #fieldsPanel::-webkit-scrollbar { width: 8px; }
  #fieldsPanel::-webkit-scrollbar-track { background: transparent; }
  #fieldsPanel::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 4px;
  }
  #fieldsPanel::-webkit-scrollbar-thumb:hover {
    background: var(--border-3);
  }

  #fieldsPanel .empty {
    grid-column: 1 / -1;
    font-size: 11px;
    color: var(--text-faint);
    font-style: italic;
    padding: 24px;
    text-align: center;
    letter-spacing: 0.04em;
  }

  .field-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 3px;
    overflow: hidden;
  }
  .field-card h4 {
    margin: 0;
    padding: 6px 12px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
    background: rgba(0,229,255,0.04);
    border-bottom: 1px solid var(--border);
  }
  .field-card .field-body {
    padding: 8px 12px;
  }
  .field-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 6px 0;
  }
  .field-row label {
    flex: 0 0 100px;
    font-size: 9px;
    color: var(--text-mute);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-weight: 700;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .field-row input[type=text] {
    flex: 1 1 auto;
    min-width: 0;
    background: var(--bg-input);
    color: var(--accent);
    border: 1px solid var(--border-2);
    border-radius: 2px;
    padding: 4px 8px;
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 700;
    text-align: right;
    outline: none;
    transition: border-color 0.1s ease;
  }
  .field-row input[type=text]:focus { border-color: var(--accent); }
  .field-row select {
    flex: 1 1 auto;
    min-width: 0;
    background: var(--bg-input);
    color: var(--text-dim);
    border: 1px solid var(--border-2);
    border-radius: 2px;
    padding: 4px 6px;
    font-family: var(--mono);
    font-size: 11px;
    outline: none;
    cursor: pointer;
  }
  .field-row select:focus { border-color: var(--accent); }
  .field-row input[type=checkbox] {
    flex: 0 0 auto;
    width: 16px;
    height: 16px;
    accent-color: var(--accent);
    cursor: pointer;
  }

  #logPanel {
    display: flex;
    flex-direction: column;
    background: var(--bg-panel);
    border-top: 1px solid var(--border);
    max-height: 32vh;
  }
  #logPanel.collapsed {
    max-height: none;
  }
  #logPanel.collapsed .body { display: none; }
  #logPanel .header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 16px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-mute);
    border-bottom: 1px solid var(--border);
  }
  #logPanel .header .label { flex: 1 1 auto; }
  #logPanel .header button {
    padding: 2px 8px;
    cursor: pointer;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-mute);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    font-weight: 700;
  }
  #logPanel .header button:hover {
    border-color: var(--border-3);
    color: var(--text-dim);
  }
  #logPanel .body {
    overflow-y: auto;
    padding: 6px 16px 8px;
    font-size: 10px;
    line-height: 1.5;
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
  }
  #logPanel .body::-webkit-scrollbar { width: 8px; }
  #logPanel .body::-webkit-scrollbar-track { background: transparent; }
  #logPanel .body::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 4px;
  }
  .log-line {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .log-info      { color: var(--text-mute); }
  .log-state     { color: var(--text-dim); }
  .log-schema    { color: var(--text-dim); }
  .log-entities  { color: var(--text-dim); }
  .log-patch     { color: var(--accent); }
  .log-cmd       { color: var(--warn); }
  .log-error     { color: var(--danger); }

  #rpModalBackdrop,
  #rpStateModal,
  #rpMgmtModal {
    position: fixed; inset: 0; z-index: 100;
    background: rgba(0, 0, 0, 0.55);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  #rpModalBackdrop[hidden],
  #rpStateModal[hidden],
  #rpMgmtModal[hidden] { display: none; }

  .rp-modal {
    background: var(--bg-panel);
    border: 1px solid var(--border-2);
    border-radius: 4px;
    box-shadow: 0 12px 48px rgba(0, 0, 0, 0.7);
    min-width: 380px;
    max-width: 520px;
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  #rpStateModal .rp-modal {
    width: 80vw;
    max-width: 900px;
  }
  #rpMgmtModal .rp-modal {
    width: 80vw;
    max-width: 720px;
  }
  .rp-modal-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--accent);
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
  }
  #rpModalBody {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .rp-modal-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .rp-modal-field label {
    font-size: 9px;
    color: var(--text-mute);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-weight: 700;
  }
  .rp-modal-field input[type=text] {
    background: var(--bg-input);
    color: var(--accent);
    border: 1px solid var(--border-2);
    border-radius: 2px;
    padding: 6px 10px;
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 700;
    outline: none;
  }
  .rp-modal-field input[type=text]:focus { border-color: var(--accent); }
  .rp-modal-file-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .rp-modal-file-btn {
    padding: 5px 12px;
    cursor: pointer;
    border: 1px solid var(--accent);
    background: transparent;
    color: var(--accent);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 700;
  }
  .rp-modal-file-btn:hover { background: rgba(0,229,255,0.08); }
  .rp-modal-file-btn.chosen {
    border-color: var(--border-3);
    color: var(--text-dim);
  }
  .rp-modal-file-status {
    font-size: 11px;
    color: var(--text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rp-modal-buttons {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding-top: 6px;
    border-top: 1px solid var(--border);
  }
  .rp-modal-buttons button {
    padding: 6px 14px;
    cursor: pointer;
    border: 1px solid var(--border-2);
    background: transparent;
    color: var(--text-dim);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    font-weight: 700;
  }
  .rp-modal-buttons button:hover:not(:disabled) {
    border-color: var(--border-3);
    color: var(--text);
    background: rgba(220,230,242,0.03);
  }
  .rp-modal-buttons button:disabled {
    color: var(--text-faint);
    border-color: var(--border);
    cursor: not-allowed;
  }

  /* Inline status text in the modal footer.  Sits to the left of the
     buttons (the buttons carry `flex: 0 0 auto` implicitly; the span
     grows to fill the space in between).  Used by the hub-shutdown
     flow to report state without requiring the log panel to be
     open. */
  .rp-modal-status {
    flex: 1 1 auto;
    align-self: center;
    font-size: 10px;
    color: var(--text-mute);
    letter-spacing: 0.02em;
    text-align: right;
    padding-right: 6px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .rp-modal-status.ok   { color: var(--accent); }
  .rp-modal-status.warn { color: var(--warn); }
  .rp-modal-status.bad  { color: var(--danger); }

  #rpModalSend,
  #rpStateDownload {
    border-color: rgba(0,229,255,0.55);
    color: var(--accent-2);
  }
  #rpModalSend:hover:not(:disabled),
  #rpStateDownload:hover:not(:disabled) {
    border-color: var(--accent);
    background: rgba(0,229,255,0.08);
  }
  #rpMgmtShutdownHub {
    border-color: rgba(255,105,125,0.55);
    color: var(--danger-b);
  }
  #rpMgmtShutdownHub:hover:not(:disabled) {
    border-color: var(--danger-a);
    background: rgba(255,90,105,0.08);
    color: #ffd0d8;
  }

  /* Management list rows (built dynamically by rp_ui.py). */
  #rpMgmtList .mgmt-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    font-size: 11px;
  }
  #rpMgmtList .mgmt-row:last-child { border-bottom: none; }
  #rpMgmtList .mgmt-name {
    flex: 0 0 130px;
    font-weight: 700;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  #rpMgmtList .mgmt-role {
    flex: 0 0 78px;
    font-size: 9px;
    color: var(--text-mute);
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 700;
  }
  #rpMgmtList .mgmt-port {
    flex: 0 0 60px;
    font-size: 10px;
    color: var(--text-faint);
    font-variant-numeric: tabular-nums;
  }
  #rpMgmtList .mgmt-pid {
    flex: 0 0 90px;
    font-size: 10px;
    color: var(--text-faint);
    font-variant-numeric: tabular-nums;
  }
  #rpMgmtList .mgmt-actions {
    flex: 1 1 auto;
    display: flex;
    gap: 6px;
    justify-content: flex-end;
  }
  #rpMgmtList .mgmt-actions button {
    padding: 3px 10px;
    cursor: pointer;
    border: 1px solid var(--border-2);
    background: transparent;
    color: var(--text-dim);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 700;
  }
  #rpMgmtList .mgmt-actions button:hover {
    border-color: var(--border-3);
    color: var(--text);
    background: rgba(220,230,242,0.03);
  }
  #rpMgmtList .mgmt-actions button.danger {
    border-color: rgba(255,105,125,0.55);
    color: var(--danger-b);
  }
  #rpMgmtList .mgmt-actions button.danger:hover {
    border-color: var(--danger-a);
    background: rgba(255,90,105,0.08);
    color: #ffd0d8;
  }
  #rpMgmtList .mgmt-empty {
    padding: 20px;
    text-align: center;
    font-size: 11px;
    color: var(--text-faint);
    font-style: italic;
  }

  .rp-state-body {
    margin: 0;
    padding: 10px 14px;
    max-height: 60vh;
    overflow: auto;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 2px;
    font-family: var(--mono);
    font-size: 10px;
    line-height: 1.4;
    color: var(--accent);
    white-space: pre;
    font-variant-numeric: tabular-nums;
  }
  .rp-state-body::-webkit-scrollbar { width: 8px; height: 8px; }
  .rp-state-body::-webkit-scrollbar-track { background: transparent; }
  .rp-state-body::-webkit-scrollbar-thumb {
    background: var(--border-3);
    border-radius: 4px;
  }
"""

RP_BODY = r"""
<div id="topbar">
  <div class="title">Remote panel</div>
  <div class="conn">
    <input type="text" id="rpTarget"
           placeholder="ws://host:port/remote"
           autocomplete="off" spellcheck="false">
    <select id="rpPreset">
      <option value="">custom</option>
    </select>
    <button id="rpConnectBtn">Connect</button>
    <button id="rpDisconnectBtn" disabled>Disconnect</button>
    <button id="rpStateBtn" title="Show everything the panel knows">State JSON</button>
    <button id="rpMgmtBtn" title="Manage playgrounds (requires hub)">Manage</button>
  </div>
  <div class="status">
    <span id="rpStatus">disconnected</span>
    <span id="rpPresence"></span>
  </div>
</div>

<div id="actionsPanel">
  <div class="header">
    <span class="label">Actions</span>
    <button id="rpActionsToggle" title="Show / hide actions">Hide</button>
  </div>
  <div class="body" id="rpActionsBody"></div>
</div>

<div id="fieldsPanel">
  <div class="empty">not connected</div>
</div>

<div id="logPanel" class="collapsed">
  <div class="header">
    <span class="label">Log</span>
    <button id="rpLogToggle" title="Show / hide log">Show</button>
    <button id="rpLogClear" title="Clear the log">Clear</button>
  </div>
  <div class="body" id="rpLogBody"></div>
</div>

<div id="rpModalBackdrop" hidden>
  <div class="rp-modal">
    <div class="rp-modal-title" id="rpModalTitle">Action</div>
    <div id="rpModalBody"></div>
    <div class="rp-modal-buttons">
      <button id="rpModalCancel">Cancel</button>
      <button id="rpModalSend" disabled>Send</button>
    </div>
  </div>
</div>

<div id="rpStateModal" hidden>
  <div class="rp-modal">
    <div class="rp-modal-title">State JSON</div>
    <pre id="rpStateBody" class="rp-state-body">{}</pre>
    <div class="rp-modal-buttons">
      <button id="rpStateCopy">Copy</button>
      <button id="rpStateDownload">Download</button>
      <button id="rpStateClose">Close</button>
    </div>
  </div>
</div>

<div id="rpMgmtModal" hidden>
  <div class="rp-modal">
    <div class="rp-modal-title">Manage playgrounds</div>
    <div id="rpMgmtHint"
         style="font-size:10px;color:#5a6774;line-height:1.5;">
      Playgrounds are registered with the pgkit hub.  Click Kill to
      ask one to shut down.  Force escalates to SIGTERM if cooperative
      shutdown does not complete within five seconds.
    </div>
    <div id="rpMgmtList"
         style="max-height:50vh;overflow:auto;
                border:1px solid #1e2836;border-radius:2px;">
    </div>
    <div class="rp-modal-buttons">
      <span id="rpMgmtStatus" class="rp-modal-status"></span>
      <button id="rpMgmtShutdownHub"
              title="Ask the hub to shut down, and every playground
                     it manages with it.">
        Shut down hub
      </button>
      <button id="rpMgmtClose">Close</button>
    </div>
  </div>
</div>
"""
