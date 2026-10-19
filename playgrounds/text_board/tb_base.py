"""
tb_base.py — text board's project CSS and panel content.

The shared shell — the `<html>`/`<head>` structure, the panel
wrapper, the status line, the restore button, the "controlled
remotely" banner, and the `<canvas>` — is provided by
`pgkit.html_shell.page()`.  This file supplies the two pieces that
are specific to the text board:

    TB_CSS          extras beyond pgkit's SHELL_CSS:
                    plain text/number input styling, the item list,
                    the coloured accent buttons, the cursor readout.

    TB_PANEL_BODY   the panel's field groups, lists, and buttons.
                    The Apply button is gone: every editor field
                    applies on commit (blur / Enter), via the
                    per-item debounce buffer in
                    tb_panel_fields.py.

EDITOR FIELD SEMANTICS
======================
Each editor field is a normal DOM input (text area, text, select).
Its `onchange` (fires on blur or Enter) calls `_applyItemField` in
tb_panel_fields.py, which:

    1. mutates board.items[i][key] immediately,
    2. queues a per-item patch to /krita/patch_text in the debounce
       buffer (250ms, keyed by item.id, batched across all edits on
       that item),
    3. fires pgkitNotifyLocal("item.<key>") — so the remote panel
       sees the change within one animation frame.

The set() for the same field (called by the remote client when a
remote patch arrives) uses the same `_applyItemField`.  The kit's
`_pgkitApplyingRemote` guard prevents the echo; the Krita round trip
still happens because the remote edit is a real edit of the shape.
"""

# ----------------------------------------------------------------------------
#  TB_CSS — text-board-specific additions on top of pgkit's SHELL_CSS
# ----------------------------------------------------------------------------

TB_CSS = r"""
  /* Plain <input type=text|number> — pgkit's SHELL_CSS only styles
     `.scrubInput` and `<select>`; the editor fields here are neither
     (they aren't scrubbable).  Same visual weight, no drag cursor. */
  #ui .field input[type=text],
  #ui .field input[type=number] {
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
    padding: 2px 4px;
    margin: 0;
    outline: none;
  }
  #ui .field input[type=text]:focus,
  #ui .field input[type=number]:focus {
    border-bottom-color: #00e5ff;
  }
  #ui .field input[type=text]:disabled,
  #ui .field input[type=number]:disabled {
    color: #3d4756;
    border-bottom-color: #1e2836;
    cursor: not-allowed;
  }

  #ui textarea {
    width: 100%; box-sizing: border-box;
    background: #0d1219; color: #00e5ff;
    border: 1px solid #223040; border-radius: 2px;
    font-family: inherit; font-size: 12px;
    padding: 4px 6px; margin: 2px 0 6px; outline: none;
    resize: vertical; min-height: 44px;
  }
  #ui textarea:focus { border-color: #00e5ff; }
  #ui textarea:disabled { color: #3d4756; }

  /* Item list — one row per text shape. */
  .itemList {
    display:flex; flex-direction:column; gap:3px;
    margin:6px 0 0; max-height:180px; overflow-y:auto;
  }
  .itemList::-webkit-scrollbar { width:6px; }
  .itemList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .itemList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .itemRow {
    display:flex; align-items:center; gap:7px;
    padding:3px 6px; border:1px solid #223040;
    border-radius:2px; cursor:pointer;
    font-size:10px; color:#a8b5c4;
    letter-spacing:0.02em;
  }
  .itemRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .itemRow.selected {
    border-color:rgba(0,229,255,0.72);
    color:#b8ecff; background:rgba(0,229,255,0.06);
  }
  .itemSwatch {
    width:9px; height:9px; border-radius:1px; flex:0 0 auto;
  }
  .itemText {
    flex:1 1 auto; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap;
  }
  .itemMeta {
    flex:0 0 auto; font-size:9px; color:#5a6774;
  }

  /* Coloured accent buttons. */
  #ui button#refreshBtn {
    border-color: rgba(0,229,255,0.72); color:#b8ecff;
  }
  #ui button#refreshBtn:hover {
    border-color:rgba(140,225,255,1.0);
    background:rgba(0,229,255,0.10); color:#d8f4ff;
  }
  #ui button#addFilesBtn {
    border-color:rgba(120,220,255,0.72); color:#b8ecff;
  }
  #ui button#addFilesBtn:hover {
    border-color:rgba(140,225,255,1.00);
    background:rgba(120,220,255,0.10); color:#d8f4ff;
  }
  #ui button#openAllBtn {
    border-color:rgba(255,200,90,0.72); color:#ffd89b;
  }
  #ui button#openAllBtn:hover {
    border-color:rgba(255,200,90,1.00);
    background:rgba(255,200,90,0.10); color:#ffe6b8;
  }
  #ui button.accent-red {
    border-color: rgba(255,105,125,0.80);
    color: #ffb0bc;
    text-shadow: 0 0 4px rgba(255,80,100,0.45);
  }
  #ui button.accent-red:hover {
    border-color: rgba(255,130,150,1.00);
    color: #ffd0d8;
    background: rgba(220,90,110,0.12);
  }

  /* Cursor readout — sits below the item list. */
  .cursorRow {
    display:flex; align-items:center; gap:8px;
    margin-top:10px; padding-top:6px;
    border-top:1px solid #1e2836;
    font-size:10px;
  }
  .cursorRow .label {
    flex:0 0 auto;
    font-size:9px; color:#5a6774;
    letter-spacing:0.14em; text-transform:uppercase;
    font-weight:700;
  }
  .cursorRow .doc {
    flex:1 1 auto;
    text-align:left; color:#7b8794;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .cursorRow .pos {
    flex:0 0 90px;
    text-align:right; color:#00e5ff;
    font-variant-numeric: tabular-nums;
  }
"""


# ----------------------------------------------------------------------------
#  TB_PANEL_BODY — the panel's content
# ----------------------------------------------------------------------------

TB_PANEL_BODY = r"""
    <div class="row">
      <button id="refreshBtn" title="Ask the daemon for every text shape (R)">
        Refresh from Krita
      </button>
    </div>

    <div class="row">
      <button id="addFilesBtn"
              title="Pick .kra files to add to the board and open in Krita">
        + Add files…
      </button>
    </div>

    <div class="row">
      <button id="openAllBtn"
              title="Open every picked file that Krita does not already have open">
        Open all
      </button>
    </div>

    <div class="row">
      <button id="gridBtn" title="Rearrange cards into a grid (G)">Arrange grid</button>
      <button id="fitBtn"  title="Fit every card in view (F)">Fit</button>
    </div>

    <div class="row">
      <button id="resetBtn" title="Reset zoom and pan">Reset view</button>
      <button id="groupBtn" title="Recolour and regroup cards by their formatting signature">
        Group by format
      </button>
    </div>

    <hr>

    <div class="field">
      <label>Card width</label>
      <input type="range" id="cardWSlider"
             min="120" max="720" step="10" value="320">
      <span class="val" id="cardWVal">320</span>
    </div>

    <div class="field">
      <label>Word boxes</label>
      <input type="checkbox" id="wordBoxToggle" checked>
    </div>

    <hr>

    <div class="field">
      <label>Selected</label>
      <span id="selLabel" style="font-size:10px; color:#7b8794;">—</span>
    </div>

    <textarea id="editText" disabled placeholder="select a card"
              spellcheck="false"></textarea>

    <div class="field">
      <label>Font</label>
      <input type="text" id="editFont" disabled autocomplete="off"
             spellcheck="false">
      <label style="flex:0 0 42px;">Size</label>
      <input type="text" id="editSize" disabled autocomplete="off"
             spellcheck="false">
    </div>

    <div class="field">
      <label>Color</label>
      <input type="text" id="editColor" disabled autocomplete="off"
             spellcheck="false">
      <label style="flex:0 0 42px;">Align</label>
      <select id="editAlign" disabled>
        <option value="left">left</option>
        <option value="center">center</option>
        <option value="right">right</option>
      </select>
    </div>

    <div class="field">
      <label>Rot°</label>
      <input type="text" id="editRot" disabled autocomplete="off"
             spellcheck="false">
      <span style="font-size:9px; color:#3d4756;">+CW</span>
    </div>

    <hr>

    <div class="field" style="margin-bottom:2px;">
      <label>Items</label>
      <span id="itemCount" style="font-size:10px; color:#5a6774;">0</span>
      <button id="clearBtn"
              style="flex:0 0 auto;"
              title="Empty the board (does not touch Krita).">Clear</button>
    </div>
    <div id="itemList" class="itemList"></div>

    <div class="cursorRow">
      <span class="label">Cursor</span>
      <span id="cursorDoc" class="doc">—</span>
      <span id="cursorPos" class="pos">—</span>
    </div>

    <hr>

    <div class="row">
      <button id="exportJsonBtn"
              title="Download a JSON dump of every canvas object, with document, board, and screen coordinates">
        Export canvas JSON
      </button>
    </div>
    <div class="row">
      <button id="saveSceneBtn">Save board</button>
      <button id="loadSceneBtn">Load board</button>
    </div>
"""
