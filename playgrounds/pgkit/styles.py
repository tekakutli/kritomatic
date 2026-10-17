"""
pgkit.styles — shared CSS for every playground's panel and canvas.

`SHELL_CSS` covers the panel chrome, the field controls, the
list widgets, the status line, the restore button, and the
"controlled remotely" banner.  It is deliberately the entire
shared surface: a project that uses the kit and adds no CSS of
its own will already look like cone_playground and
pair_playground do today.

Project-specific CSS — accent colours on a particular button,
extra row states, unusual controls — is appended by the project.
The kit's own classes and IDs are stable; projects should add
new ones rather than override the kit's, so a change here always
reaches every project.
"""

SHELL_CSS = r"""
  html, body {
    margin:0; padding:0; height:100%; overflow:hidden;
    background:#0a0e14; color:#dce6f2;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas',
                 'SF Mono', monospace;
  }

  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    background:#0b1018;
    padding:12px 14px;
    border:1px solid #1e2836;
    border-radius:3px;
    box-shadow:0 6px 28px rgba(0,0,0,0.65);
    font-size:11px; color:#a8b5c4;
    user-select:none;
    width:288px; box-sizing:border-box;
    max-height: calc(100vh - 24px);
    display: flex;
    flex-direction: column;
  }
  #ui.collapsed { display:none; }

  #ui h3 {
    display:flex; align-items:center; gap:6px;
    margin:0 0 4px; padding:0 0 8px;
    border-bottom:1px solid #1e2836;
    font-size:11px; font-weight:700;
    letter-spacing:0.16em; text-transform:uppercase;
    color:#dce6f2;
    flex: 0 0 auto;
  }

  #ui .panel-body {
    overflow-y: auto;
    overflow-x: hidden;
    min-height: 0;
    flex: 1 1 auto;
    margin-right: -6px;
    padding-right: 6px;
  }
  #ui .panel-body::-webkit-scrollbar { width: 6px; }
  #ui .panel-body::-webkit-scrollbar-track { background: transparent; }
  #ui .panel-body::-webkit-scrollbar-thumb {
    background: #1e2836; border-radius: 3px;
  }
  #ui .panel-body::-webkit-scrollbar-thumb:hover {
    background: #2a3848;
  }

  #ui .field { display:flex; align-items:center; gap:8px; margin:4px 0; }
  #ui .field label {
    flex:0 0 78px;
    font-size:9px; color:#5a6774;
    letter-spacing:0.14em; text-transform:uppercase;
    font-weight:700;
  }
  #ui .field input[type=range] {
    flex:1 1 auto; min-width:0;
    accent-color:#00e5ff;
  }
  #ui .field input[type=checkbox] {
    flex:0 0 auto;
    margin-left:auto;
    accent-color:#00e5ff;
    cursor:pointer;
    width:14px; height:14px;
  }
  #ui .field input.accent-red-checkbox {
    accent-color:#ff5a69;
  }
  #ui .field .val {
    flex:0 0 44px;
    font-size:11px; color:#00e5ff;
    text-align:right; font-weight:700;
    font-variant-numeric: tabular-nums;
  }

  #ui .field input.scrubInput {
    flex:1 1 auto;
    min-width:0;
    background:transparent;
    border:0;
    border-bottom:1px solid #223040;
    color:#00e5ff;
    font-family:inherit;
    font-size:12px;
    font-weight:700;
    font-variant-numeric: tabular-nums;
    text-align:right;
    padding:2px 4px;
    margin:0;
    outline:none;
    cursor:ew-resize;
    user-select:none;
    transition: border-color 0.1s ease;
  }
  #ui .field input.scrubInput:hover {
    border-bottom-color:#34445a;
  }
  #ui .field input.scrubInput:focus {
    border-bottom-color:#00e5ff;
    cursor:text;
    user-select:text;
  }
  #ui .field input.scrubInput:disabled {
    color:#3d4756;
    border-bottom-color:#1e2836;
    cursor:not-allowed;
  }

  #ui .field select {
    flex:1 1 auto;
    min-width:0;
    background:#0e1622;
    color:#b8c4d2;
    border:1px solid #223040;
    font-family:inherit;
    font-size:11px;
    padding:2px 4px;
    border-radius:2px;
    outline:none;
    cursor:pointer;
  }
  #ui .field select:hover {
    border-color:#34445a;
    color:#dce6f2;
  }
  #ui .field select:focus {
    border-color:#00e5ff;
  }

  #ui .row { display:flex; gap:6px; margin:4px 0; }
  #ui .row button { flex:1 1 auto; }

  #ui button {
    padding:5px 10px; cursor:pointer;
    border:1px solid #223040; background:transparent;
    color:#b8c4d2; border-radius:2px;
    font-family:inherit; font-size:10px;
    letter-spacing:0.10em; text-transform:uppercase;
    font-weight:700;
    transition: background 0.08s ease, border-color 0.08s ease;
  }
  #ui button:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  #ui button:active { background:rgba(220,230,242,0.07); }

  #ui button.active {
    border-color: #00e5ff;
    color: #00e5ff;
    background: rgba(0,229,255,0.06);
  }

  #ui button.accent-red {
    border-color: rgba(255, 105, 125, 0.80);
    color: #ffb0bc;
    text-shadow: 0 0 4px rgba(255, 80, 100, 0.45);
  }
  #ui button.accent-red:hover {
    border-color: rgba(255, 130, 150, 1.00);
    color: #ffd0d8;
    background: rgba(220, 90, 110, 0.12);
    text-shadow: 0 0 6px rgba(255, 80, 100, 0.65);
  }
  #ui button.accent-red:active {
    background: rgba(220, 90, 110, 0.20);
  }

  #ui details.section { margin: 0; }
  #ui details.section > summary {
    list-style: none;
    cursor: pointer;
    user-select: none;
    padding: 6px 0 5px;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #5a6774;
    display: flex;
    align-items: center;
    gap: 6px;
    border-top: 1px solid #1e2836;
    margin-top: 6px;
    transition: color 0.08s ease;
  }
  #ui details.section:first-of-type > summary {
    margin-top: 0;
    border-top: 0;
  }
  #ui details.section > summary::-webkit-details-marker { display: none; }
  #ui details.section > summary::before {
    content: "\25B8";
    display: inline-block;
    font-size: 8px;
    color: #3d4756;
    transition: transform 0.1s ease;
  }
  #ui details.section[open] > summary::before { transform: rotate(90deg); }
  #ui details.section > summary:hover { color: #a8b5c4; }
  #ui details.section[open] > summary { color: #00e5ff; }
  #ui details.section[open] > summary::before { color: #00e5ff; }
  #ui details.section > .section-body { padding-bottom: 4px; }

  .quadList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:140px; overflow-y:auto;
  }
  .quadList::-webkit-scrollbar { width:6px; }
  .quadList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .quadList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .quadRow {
    display:flex; align-items:center; gap:5px;
    padding:3px 6px;
    border:1px solid #223040;
    border-radius:2px;
    cursor:pointer;
    font-size:10px;
    color:#a8b5c4;
    letter-spacing:0.04em;
    flex: 0 0 auto;
  }
  .quadRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .quadRow.selected {
    border-color:rgba(0,229,255,0.65);
    color:#b8ecff;
    background:rgba(0,229,255,0.06);
  }
  .quadSwatch {
    width:9px; height:9px;
    border-radius:1px;
    flex:0 0 auto;
  }
  .quadLabel {
    flex:1 1 auto; font-weight:700;
    font-variant-numeric: tabular-nums;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .quadLabelInput {
    flex:1 1 auto;
    min-width:0;
    background:transparent;
    border:0;
    border-bottom:1px solid transparent;
    color:inherit;
    font-family:inherit;
    font-size:inherit;
    font-weight:700;
    letter-spacing:inherit;
    padding:0;
    margin:0;
    outline:none;
    overflow:hidden;
    text-overflow:ellipsis;
    transition: border-color 0.1s ease;
  }
  .quadLabelInput:hover { border-bottom-color:#223040; }
  .quadLabelInput:focus { border-bottom-color:#00e5ff; }

  .quadDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
  }
  .quadDel:hover {
    background:rgba(212,133,144,0.10) !important;
    border-color:#7a3e4a !important;
    color:#e8a0a8 !important;
  }

  .quadMv {
    width:18px !important;
    min-width:18px !important;
    padding:1px 0 !important;
    font-size:11px !important;
    line-height:1 !important;
    border-color:#223040 !important;
    color:#a8b5c4 !important;
    letter-spacing:0 !important;
  }
  .quadMv:hover:not(:disabled) {
    background:rgba(220,230,242,0.06) !important;
    border-color:#34445a !important;
    color:#dce6f2 !important;
  }
  .quadMv:disabled {
    opacity:0.30 !important;
    cursor:not-allowed !important;
  }

  .quadRoom {
    width:auto !important;
    padding:1px 6px !important;
    font-size:11px !important;
    line-height:1 !important;
    border-color: rgba(255, 200, 90, 0.60) !important;
    color: #ffc966 !important;
    letter-spacing:0 !important;
  }
  .quadRoom:hover {
    background: rgba(255, 200, 90, 0.12) !important;
    border-color: rgba(255, 200, 90, 1.00) !important;
    color: #ffe0a0 !important;
  }
  .quadRoom:active {
    background: rgba(255, 200, 90, 0.20) !important;
  }

  #status {
    margin-top:6px; padding-top:6px;
    border-top:1px solid #1e2836;
    font-size:10px; color:#7b8794;
    min-height:0;
    font-variant-numeric: tabular-nums;
    overflow-wrap:anywhere; line-height:1.5;
  }
  #status.ok   { color:#00e5ff; }
  #status.bad  { color:#d48590; }
  #status.warn { color:#ffe600; }

  canvas { display:block; touch-action:none; background:#0a0e14; }

  #restoreBtn {
    position:absolute; top:12px; left:12px; z-index:10;
    display:none;
    padding:6px 12px; cursor:pointer;
    border:1px solid #1e2836; background:#0b1018;
    border-radius:3px; box-shadow:0 6px 28px rgba(0,0,0,0.65);
    font-family:inherit;
    font-size:10px; font-weight:700; letter-spacing:0.10em;
    text-transform:uppercase; color:#a8b5c4;
  }
  #restoreBtn:hover { border-color:#34445a; color:#dce6f2; }
  #restoreBtn.visible { display:block; }

  #remoteBanner {
    position:absolute; top:12px; left:12px; z-index:10;
    display:none;
    padding:6px 12px;
    border:1px solid rgba(0,229,255,0.55); background:#0b1018;
    border-radius:3px; box-shadow:0 6px 28px rgba(0,0,0,0.65);
    font-family:inherit;
    font-size:10px; font-weight:700; letter-spacing:0.10em;
    text-transform:uppercase; color:#00e5ff;
  }
  #remoteBanner.visible { display:block; }
"""
