"""
pg_base.py — board's project CSS and panel content.

The shared shell (head, panel wrapper, status line, restore button,
"controlled remotely" banner, `<canvas>`) is provided by
`pgkit.html_shell.page()`.  This file supplies:

    PG_CSS          board-specific CSS beyond pgkit's SHELL_CSS:
                    the file-row list, the coloured accent buttons,
                    the drop overlay.

    PG_PANEL_BODY   the panel's field groups, lists, and buttons.

The drop overlay is created by JS on load (see pg_krita.py) rather
than sitting in the panel body.  It needs to be a sibling of
`<canvas>`, not a child of `#ui` — a child would inherit the
panel's `display: none` when a remote client connects, hiding the
drop target exactly when someone on the playground machine wants
to drop a file.
"""

PG_CSS = r"""
  #ui button#refreshBtn {
    border-color: rgba(0, 229, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#refreshBtn:hover {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(0, 229, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#retrieveBtn {
    border-color: rgba(120, 220, 255, 0.72);
    color: #b8ecff;
  }
  #ui button#retrieveBtn:hover {
    border-color: rgba(140, 225, 255, 1.00);
    background: rgba(120, 220, 255, 0.10);
    color: #d8f4ff;
  }
  #ui button#openAllBtn {
    border-color: rgba(255, 200, 90, 0.72);
    color: #ffd89b;
  }
  #ui button#openAllBtn:hover {
    border-color: rgba(255, 200, 90, 1.00);
    background: rgba(255, 200, 90, 0.10);
    color: #ffe6b8;
  }
  #ui button#projectBtn {
    border-color: rgba(255, 130, 210, 0.72);
    color: #ffb0e6;
  }
  #ui button#projectBtn:hover {
    border-color: rgba(255, 130, 210, 1.00);
    background: rgba(255, 130, 210, 0.10);
    color: #ffd0f0;
  }

  .fileList {
    display:flex; flex-direction:column;
    gap:3px; margin:6px 0 0;
    max-height:220px; overflow-y:auto;
  }
  .fileList::-webkit-scrollbar { width:6px; }
  .fileList::-webkit-scrollbar-thumb {
    background:#1e2836; border-radius:3px;
  }
  .fileList .empty {
    font-size:10px; color:#3d4756;
    font-style:italic; padding:3px 4px;
  }
  .fileRow {
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
  .fileRow:hover {
    border-color:#34445a; color:#dce6f2;
    background:rgba(220,230,242,0.03);
  }
  .fileRow.selected {
    border-color:rgba(0, 229, 255, 0.72);
    color:#b8ecff;
    background:rgba(0, 229, 255, 0.06);
  }
  .fileRow.closed  { opacity: 0.65; }
  .fileRow.missing {
    border-color: rgba(212, 133, 144, 0.55);
    color: #d48590;
    opacity: 0.75;
  }
  .fileRow.missing:hover {
    border-color: rgba(232, 160, 168, 0.85);
    color: #e8a0a8;
  }
  .fileSwatch {
    width:9px; height:9px;
    border-radius:1px;
    flex:0 0 auto;
  }
  .fileLabel {
    flex:1 1 auto; font-weight:700;
    font-variant-numeric: tabular-nums;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }
  .fileState {
    flex:0 0 auto;
    font-size:11px; line-height:1;
    letter-spacing:0;
  }
  .fileState.open    { color:#00e5ff; }
  .fileState.closed  { color:#5a6774; }
  .fileState.missing { color:#d48590; }
  .fileAct {
    width:auto !important;
    padding:1px 7px !important;
    font-size:10px !important;
    line-height:1.3 !important;
    letter-spacing:0.06em !important;
  }
  .fileAct.open {
    border-color: rgba(255, 200, 90, 0.55) !important;
    color: #ffd89b !important;
  }
  .fileAct.open:hover {
    background: rgba(255, 200, 90, 0.10) !important;
    border-color: rgba(255, 200, 90, 0.95) !important;
    color: #ffe6b8 !important;
  }
  .fileDel {
    width:auto !important;
    padding:1px 6px !important;
    font-size:12px !important;
    line-height:1 !important;
    border-color:#4a2830 !important;
    color:#d48590 !important;
    letter-spacing:0 !important;
  }
  .fileDel:hover {
    background:rgba(212,133,144,0.10) !important;
    border-color:#7a3e4a !important;
    color:#e8a0a8 !important;
  }

  #dropOverlay {
    position:fixed; inset:0; z-index:100;
    display:none;
    background:rgba(0,229,255,0.06);
    border:2px dashed rgba(0,229,255,0.65);
    box-sizing:border-box;
    pointer-events:none;
    align-items:center; justify-content:center;
    font-family:'JetBrains Mono','Fira Code','Consolas','SF Mono',
                 monospace;
    font-size:14px; font-weight:700;
    color:#b8ecff;
    letter-spacing:0.16em; text-transform:uppercase;
    text-shadow:0 0 12px rgba(0,229,255,0.55);
  }
  #dropOverlay.visible { display:flex; }
"""


PG_PANEL_BODY = r"""
    <div class="row">
      <button id="refreshBtn" title="Ask Krita which of the board's files are open (R)">
        Refresh from Krita
      </button>
    </div>

    <div class="row">
      <button id="addFilesBtn" title="Pick .kra (or other image) files to put on the board">
        + Add files…
      </button>
      <button id="retrieveBtn" title="Add every document currently open in Krita to the board">
        Retrieve open
      </button>
    </div>

    <div class="row">
      <button id="openAllBtn" title="Open every closed file in Krita (O)">
        Open all
      </button>
    </div>

    <div class="row">
      <button id="projectBtn" title="Copy the selected card's overlapping region into every overlapped Krita document as a new layer (P)">
        Project overlaps
      </button>
    </div>

    <div class="row">
      <button id="gridBtn"  title="Rearrange cards into a grid (G)">Arrange grid</button>
      <button id="fitBtn"   title="Fit every card in view (F)">Fit</button>
    </div>

    <div class="row">
      <button id="resetBtn" title="Reset zoom and pan to the origin">Reset view</button>
    </div>

    <hr>

    <div class="field">
      <label>Card width</label>
      <input type="range" id="cardWSlider"
             min="120" max="720" step="10" value="320"
             title="Reference width for the board.  Existing cards are rescaled proportionally, so their relative sizes are preserved.">
      <span class="val" id="cardWVal">320</span>
    </div>

    <div class="field">
      <label>Scale</label>
      <input type="text" id="cardScaleVal" class="scrubInput"
             value="—" autocomplete="off" spellcheck="false" disabled
             title="Selected card's scale relative to the reference width. Drag sideways to scrub; hold Shift to snap.">
    </div>

    <div class="field">
      <label>Rotation</label>
      <input type="text" id="cardRotVal" class="scrubInput"
             value="—" autocomplete="off" spellcheck="false" disabled
             title="Selected card's rotation in degrees.  Drag sideways to scrub; hold Shift to snap to 15°.">
    </div>

    <hr>

    <div class="field" style="margin-bottom:2px;">
      <label>Files</label>
      <button id="clearBtn"
              title="Empty the board.  Does not touch Krita."
              style="flex:0 0 auto;">Clear</button>
    </div>
    <div id="fileList" class="fileList"></div>

    <hr>

    <div class="row">
      <button id="saveSceneBtn">Save board</button>
      <button id="loadSceneBtn">Load board</button>
    </div>
"""
