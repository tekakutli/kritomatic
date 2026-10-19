"""
pg_base.py — the pair playground's panel content and local CSS.

PAIR_CSS extends pgkit's SHELL_CSS with pair-specific row states
(overlap highlight, shift-set member).  PAIR_PANEL_BODY is the
panel's HTML; the JS that wires its controls lives in
pg_panel_scrubs.py and pg_panel_bindings.py.
"""

PAIR_CSS = r"""
  /* Pair-only: overlap and shift-set markers on the two lists' rows. */
  #ui .field input#overlapHighlight {
    accent-color: #ff5a69;
  }

  .quadRow.overlapRow {
    border-color: rgba(255, 90, 105, 0.55);
  }
  .quadRow.overlapRow.selected {
    border-color: rgba(255, 90, 105, 0.95);
  }
  .quadRow.overlapRow .quadSwatch {
    box-shadow: 0 0 0 2px rgba(255, 90, 105, 0.55);
  }
  .quadRow.shiftMember {
    box-shadow: inset 0 0 0 1px rgba(0, 229, 255, 0.55);
  }
  .quadRow.shiftMember.selected {
    box-shadow: inset 0 0 0 1px rgba(0, 229, 255, 0.90);
  }
  .quadRow.squareRow.selected {
    border-color: rgba(120, 220, 255, 0.65);
    color: #b8ecff;
    background: rgba(120, 220, 255, 0.06);
  }
  .quadRow.squareRow .quadSwatch {
    background: rgba(120, 220, 255, 0.55);
    border-color: rgba(140, 225, 255, 0.85);
  }
  .quadRow.squareRow.selected .quadSwatch {
    background: rgba(180, 240, 255, 0.95);
    border-color: #b8ecff;
  }
"""

PAIR_PANEL_BODY = r"""
    <details class="section" open>
      <summary>Pair</summary>
      <div class="section-body">
        <div class="field">
          <label>Incl.°</label>
          <input type="text" id="pairInclinationVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Inclination of the pair against the horizontal axis, degrees, in [-90, +90].">
        </div>
        <div class="field">
          <label>Metric A</label>
          <input type="text" id="pairMetricAVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="World units per abstract unit along line A.">
        </div>
        <div class="field">
          <label>Metric B</label>
          <input type="text" id="pairMetricBVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="World units per abstract unit along line B.">
        </div>
        <div class="field">
          <label>Offset</label>
          <input type="text" id="pairOffsetVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Shift of line B along the direction of line A, in world units.">
        </div>
        <div class="field">
          <label>Gap</label>
          <input type="text" id="pairGapVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Perpendicular distance between the two lines, in world units.">
        </div>
        <div class="field">
          <label>Scale</label>
          <input type="text" id="pairScaleVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Whole-pair zoom factor about the pair's anchor point. Scales both lines, the gap, the offset, and the two metrics. 1 = identity. Abstract segment intervals are unaffected.">
        </div>
        <div class="field">
          <label>Loop gap</label>
          <input type="text" id="pairLoopbackVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Extra abstract distance appended to the pair's axis period. 0 reproduces the historic behaviour.">
        </div>
        <div class="row">
          <button id="addPairBtn">+ Pair</button>
          <button id="clonePairBtn">+ Clone</button>
        </div>
        <div id="pairList" class="quadList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Segments</summary>
      <div class="section-body">
        <div class="field">
          <label>Overlap</label>
          <input type="checkbox" id="overlapHighlight"
                 title="Highlight segments whose trapezoids visually overlap a trapezoid on another pair, on the canvas.">
        </div>
        <div class="row">
          <button id="drawSegmentBtn">Draw Segment</button>
        </div>
        <div class="row">
          <button id="toggleFillBtn">Toggle Fill</button>
          <button id="clearSegmentsBtn">Clear Segments</button>
        </div>
        <div id="segmentList" class="quadList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Export</summary>
      <div class="section-body">
        <div class="row">
          <button id="saveSceneBtn">Save scene</button>
          <button id="loadSceneBtn">Load scene</button>
        </div>
        <div class="row">
          <button id="exportBtn">Export visual state</button>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>KRA</summary>
      <div class="section-body">
        <div class="field">
          <label>Position</label>
          <select id="kraTextPos">
            <option value="center">center</option>
            <option value="tl">top-left</option>
            <option value="tc">top-center</option>
            <option value="tr">top-right</option>
            <option value="ml">mid-left</option>
            <option value="mr">mid-right</option>
            <option value="bl">bottom-left</option>
            <option value="bc">bottom-center</option>
            <option value="br">bottom-right</option>
            <option value="top">top edge</option>
            <option value="bottom">bottom edge</option>
            <option value="left">left edge</option>
            <option value="right">right edge</option>
          </select>
        </div>
        <div class="field">
          <label>Padding</label>
          <input type="text" id="kraTextPad" class="scrubInput"
                 value="0.06" autocomplete="off" spellcheck="false">
        </div>
        <div class="field">
          <label>Rectangles</label>
          <input type="checkbox" id="kraDrawRects" checked
                 title="When checked, each filled segment also emits a filled polygon matching its trapezoid.">
        </div>
        <div class="row">
          <button id="generateKraBtn" class="accent-red">Generate .kra</button>
        </div>
      </div>
    </details>
"""
