"""
pg_base.py — cone's panel body and Cone-specific CSS.

The shell (head, panel wrapper, status line, restore button,
"controlled remotely" banner, `<canvas>`) comes from
`pgkit.html_shell.page()`.  This module supplies only:

    CONE_CSS          empty — pgkit's SHELL_CSS covers every style
                      cone uses.  The one exception — the red accent
                      on the "Generate .kra" button — is applied via
                      the standard `accent-red` class in the panel
                      body.

    CONE_PANEL_BODY   the panel body markup — five collapsible
                      sections: View, Patches, Squares, Draw, Export.

The panel title (including the `?` help button) is passed as a raw
string to `page()` from `cone_html.py`, matching the docstring in
`pgkit.html_shell`.  That's how the help button lives in the `h3`
title bar without pgkit needing to know about it.
"""

CONE_CSS = ""


CONE_PANEL_BODY = r"""
    <details class="section" open>
      <summary>View</summary>
      <div class="section-body">
        <div class="field">
          <label>Depth</label>
          <input type="range" id="depthSlider"
                 min="3" max="40" step="0.5" value="15">
          <span class="val" id="depthVal">15.0</span>
        </div>
        <div class="field">
          <label>Half-angle</label>
          <input type="range" id="angleSlider"
                 min="10" max="60" step="1" value="30">
          <span class="val" id="angleVal">30°</span>
        </div>
        <div class="field">
          <label>Rings</label>
          <input type="range" id="ringsSlider"
                 min="4" max="40" step="1" value="14">
          <span class="val" id="ringsVal">14</span>
        </div>
        <div class="field">
          <label>Meridians</label>
          <input type="range" id="meridiansSlider"
                 min="4" max="48" step="1" value="24">
          <span class="val" id="meridiansVal">24</span>
        </div>
        <div class="field">
          <label>Shape depth</label>
          <input type="range" id="shapeDepthSlider"
                 min="0.05" max="2.00" step="0.05" value="1.00">
          <span class="val" id="shapeDepthVal">1.00</span>
        </div>
        <div class="row">
          <button id="resetBtn">Reset</button>
          <button id="centerBtn">Center apex</button>
        </div>
      </div>
    </details>

    <details class="section" open>
      <summary>Patches</summary>
      <div class="section-body">
        <div class="field">
          <label>Patch &phi;</label>
          <input type="text" id="patchPhiVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Patch s</label>
          <input type="text" id="patchSVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Mirror</label>
          <input type="checkbox" id="patchMirror"
                 title="Draw a visual clone of every square on this
                        patch on the diametrically opposite side of
                        the cone">
        </div>
        <div class="field">
          <label>Mirror &ang;</label>
          <input type="text" id="patchMirrorAngle" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Mirror flip</label>
          <input type="checkbox" id="patchMirrorFlip"
                 title="Negate the mirror's rotation so tilted shapes
                        are not 180°-rotated relative to the
                        original">
        </div>

        <div class="field">
          <label>Plane warp</label>
          <input type="text" id="patchWarp" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled
                 title="Reparametrise the patch's own tangential
                        plane.  0 = the historic axial (rectangular)
                        chart, 1 = a concentric chart centred on the
                        apex.">
        </div>

        <div class="field" style="margin-top:6px; margin-bottom:2px;">
          <label>List</label>
          <button id="addQuadBtn" style="flex:1 1 auto;">+ Patch</button>
          <button id="cloneQuadBtn" style="flex:1 1 auto;">+ Clone</button>
        </div>
        <div id="quadList" class="quadList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Squares</summary>
      <div class="section-body">
        <div class="field">
          <label>Square &phi;</label>
          <input type="text" id="squarePhiVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Square s</label>
          <input type="text" id="squareSVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Square W</label>
          <input type="text" id="squareWVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Square H</label>
          <input type="text" id="squareHVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
        </div>
        <div class="field">
          <label>Slope &times;</label>
          <input type="text" id="squareSlopeVal" class="scrubInput"
                 value="&#8212;" autocomplete="off" spellcheck="false"
                 disabled>
          <input type="text" id="shapeStandingMultVal" class="scrubInput"
                 value="2.00" autocomplete="off" spellcheck="false">
        </div>
        <div class="field">
          <label>Visual bot.</label>
          <input type="checkbox" id="squareVisualBottom">
        </div>

        <div class="field" style="margin-top:6px; margin-bottom:2px;">
          <label>List</label>
          <button id="addSquareBtn" style="flex:1 1 auto;">+ Square</button>
          <button id="cloneSquareBtn" style="flex:1 1 auto;">+ Clone</button>
        </div>
        <div id="squareList" class="quadList"></div>
      </div>
    </details>

    <details class="section" open>
      <summary>Draw</summary>
      <div class="section-body">
        <div class="field">
          <label>Draw fit</label>
          <select id="drawFitMode">
            <option value="shape-first" selected>shape &rarr; patch</option>
            <option value="patch-first">patch &rarr; shape</option>
          </select>
        </div>
        <div class="field">
          <label>No rotate</label>
          <input type="checkbox" id="drawNoRotate">
        </div>
        <div class="row" style="margin-top:-2px;">
          <button id="drawQuadBtn">Draw Quad</button>
        </div>
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
          <button id="generateKraBtn" class="accent-red">Generate .kra</button>
        </div>

        <div class="field" style="margin-top:6px;">
          <label>KRA text</label>
          <select id="kraTextPos">
            <option value="center">center</option>
            <option value="tl">top-left</option>
            <option value="tr">top-right</option>
            <option value="bl">bottom-left</option>
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
          <label>Text color</label>
          <select id="kraTextColor">
            <option value="color">patch hue</option>
            <option value="black">black</option>
          </select>
        </div>
        <div class="field">
          <label>Warp</label>
          <select id="kraTextWarp">
            <option value="square">per-square</option>
            <option value="patch">per-patch</option>
            <option value="text">per-text</option>
            <option value="text-shear">per-text shear</option>
            <option value="patch-shear">per-patch shear</option>
          </select>
        </div>
        <div class="field">
          <label>Rectangles</label>
          <input type="checkbox" id="kraDrawRects" checked>
        </div>
      </div>
    </details>
"""
