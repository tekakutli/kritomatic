"""
rp_base.py — the remote panel's panel body and local CSS.

The shell comes from pgkit's html_shell.page().  This file supplies:

    RP_PANEL_BODY    the connection block, the field container
                     (filled from the playground's schema), the
                     command block, and the message log

    RP_CSS           styles that extend the shared stylesheet:
                     the connection strip, the field container,
                     the message log, and the rules that hide the
                     unused canvas and pgkit's help button

The connection block is at the top of the panel body and can be
collapsed with the whole panel — that's the expected behaviour;
when you collapse the remote panel, you're not using it.
"""

RP_CSS = r"""
  /* This page has no canvas of its own — the playgrounds it talks to
     do.  Hide pgkit's <canvas>.  Also hide pgkit's help button; a
     remote panel has nothing useful to say in a help popup, and the
     space is better used by the connect strip. */
  canvas#c     { display: none; }
  #ui #helpBtn { display: none; }

  /* The connection strip: target input, preset dropdown, connect and
     disconnect buttons.  Visually a full-width row of controls at the
     top of the panel body. */
  #rpConn {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-bottom: 6px;
    border-bottom: 1px solid #1e2836;
    margin-bottom: 6px;
  }
  #rpConn .row { margin: 2px 0; }
  #rpConn input[type=text] {
    background: transparent;
    border: 0;
    border-bottom: 1px solid #223040;
    color: #00e5ff;
    font-family: inherit;
    font-size: 11px;
    font-weight: 700;
    padding: 3px 4px;
    outline: none;
    width: 100%;
    box-sizing: border-box;
  }
  #rpConn input[type=text]:focus { border-bottom-color: #00e5ff; }
  #rpConn select {
    background: #0e1622;
    color: #b8c4d2;
    border: 1px solid #223040;
    font-family: inherit;
    font-size: 11px;
    padding: 2px 4px;
    border-radius: 2px;
    outline: none;
    cursor: pointer;
    width: 100%;
    box-sizing: border-box;
  }
  #rpConn button {
    padding: 5px 10px;
    cursor: pointer;
    border: 1px solid #223040;
    background: transparent;
    color: #b8c4d2;
    border-radius: 2px;
    font-family: inherit;
    font-size: 10px;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    font-weight: 700;
    flex: 1 1 auto;
  }
  #rpConn button:hover {
    border-color: #34445a;
    color: #dce6f2;
    background: rgba(220, 230, 242, 0.03);
  }
  #rpConn button:disabled {
    color: #3d4756;
    border-color: #1e2836;
    cursor: not-allowed;
  }
  #rpConn #rpConnectBtn    { border-color: rgba(0, 229, 255, 0.55); color: #b8ecff; }
  #rpConn #rpDisconnectBtn { border-color: rgba(255, 105, 125, 0.55); color: #ffb0bc; }

  #rpStatusStrip {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;
    font-size: 10px;
    color: #7b8794;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  #rpStatusStrip #rpStatus {
    font-weight: 700;
    color: #5a6774;
  }
  #rpStatusStrip #rpStatus.ok   { color: #00e5ff; }
  #rpStatusStrip #rpStatus.warn { color: #ffe600; }
  #rpStatusStrip #rpStatus.bad  { color: #d48590; }
  #rpStatusStrip #rpPresence {
    color: #5a6774;
    font-weight: 700;
    letter-spacing: 0.02em;
    text-transform: none;
  }

  /* The field container.  Its per-field rows use pgkit's .field
     styling.  Number inputs re-use pgkit's .scrubInput look so the
     panel visually matches the playground's. */
  #rpFields .empty {
    font-size: 10px;
    color: #3d4756;
    font-style: italic;
    padding: 3px 4px;
  }
  #rpFields .field input[type=text] {
    flex: 1 1 auto;
    min-width: 0;
    background: transparent;
    border: 0;
    border-bottom: 1px solid #223040;
    color: #00e5ff;
    font-family: inherit;
    font-size: 12px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    text-align: right;
    padding: 2px 4px;
    margin: 0;
    outline: none;
    transition: border-color 0.1s ease;
  }
  #rpFields .field input[type=text]:focus {
    border-bottom-color: #00e5ff;
  }
  #rpFields .field select {
    flex: 1 1 auto;
    min-width: 0;
    background: #0e1622;
    color: #b8c4d2;
    border: 1px solid #223040;
    font-family: inherit;
    font-size: 11px;
    padding: 2px 4px;
    border-radius: 2px;
    outline: none;
    cursor: pointer;
  }

  /* The message log.  A small scrollable strip near the bottom. */
  .rp-log {
    max-height: 140px;
    overflow-y: auto;
    font-size: 10px;
    color: #7b8794;
    line-height: 1.5;
    font-variant-numeric: tabular-nums;
    padding-right: 4px;
  }
  .rp-log::-webkit-scrollbar { width: 6px; }
  .rp-log::-webkit-scrollbar-thumb {
    background: #1e2836;
    border-radius: 3px;
  }
  .rp-log-line { white-space: pre-wrap; }
  .rp-log-info    { color: #7b8794; }
  .rp-log-state   { color: #7b8794; }
  .rp-log-schema  { color: #7b8794; }
  .rp-log-patch   { color: #00e5ff; }
  .rp-log-cmd     { color: #ffe600; }
  .rp-log-error   { color: #d48590; }
"""

RP_PANEL_BODY = r"""
    <div id="rpConn">
      <input type="text" id="rpTarget" placeholder="ws://127.0.0.1:8767/remote"
             autocomplete="off" spellcheck="false">
      <select id="rpPreset">
        <option value="ws://127.0.0.1:8767/remote">pair</option>
        <option value="ws://127.0.0.1:8766/remote">cone</option>
        <option value="ws://127.0.0.1:8770/remote">board</option>
        <option value="ws://127.0.0.1:8771/remote">text_board</option>
        <option value="">custom</option>
      </select>
      <div class="row">
        <button id="rpConnectBtn">Connect</button>
        <button id="rpDisconnectBtn" disabled>Disconnect</button>
      </div>
    </div>

    <div id="rpStatusStrip">
      <span id="rpStatus">disconnected</span>
      <span id="rpPresence"></span>
    </div>

    <details class="section" open>
      <summary>Commands</summary>
      <div class="section-body">
        <div class="field">
          <label>kind</label>
          <input type="text" id="rpCmdKind"
                 placeholder="reset_view | deselect | ..."
                 autocomplete="off" spellcheck="false">
        </div>
        <div class="field">
          <label>payload</label>
          <input type="text" id="rpCmdPayload"
                 placeholder='{"optional": "json"}'
                 autocomplete="off" spellcheck="false">
        </div>
        <div class="row">
          <button id="rpCmdBtn">Send command</button>
        </div>
      </div>
    </details>

    <div id="rpFields"></div>

    <details class="section" open>
      <summary>Log</summary>
      <div class="section-body">
        <div id="rpLog" class="rp-log"></div>
      </div>
    </details>
"""
