# workbench/wb_base.py
"""
wb_base.py — the workbench's CSS and HTML structure.

WB_CSS styles the topbar (tab bar, +, ⚙), the iframe container, the
start popup, and the transient banner.  The Manage-playgrounds modal
frame, list rows, and status styles come from
pgruntime/hub_manage_ui.py (HUB_MANAGE_UI_CSS) so the same modal
looks identical in remote_panel and workbench.

The ⚙ button (id `wbManageBtn`) is new: clicking it opens the shared
Manage-playgrounds modal.  It's a `.wb-start-btn` so it inherits the
existing + button styling — no new CSS needed.
"""

WB_CSS = """
  html, body {
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    background: #0a0e14; color: #dce6f2;
    font-family: 'JetBrains Mono','Fira Code','Consolas','SF Mono',monospace;
    font-size: 11px;
  }
  body { display: flex; flex-direction: column; height: 100vh; }

  #wbTabRow {
    display: flex;
    align-items: stretch;
    background: #0b1018;
    border-bottom: 1px solid #1e2836;
    position: relative;
    min-height: 34px;
    flex: 0 0 auto;
  }
  #wbTabBar {
    display: flex;
    flex: 1 1 auto;
    overflow-x: auto;
    overflow-y: hidden;
    white-space: nowrap;
  }
  #wbTabBar::-webkit-scrollbar { height: 4px; }
  #wbTabBar::-webkit-scrollbar-thumb { background: #1e2836; border-radius: 2px; }

  .wb-tab {
    padding: 8px 14px; margin: 0; border: 0;
    border-right: 1px solid #1e2836; background: transparent;
    color: #7b8794; font-family: inherit;
    font-size: 10px; font-weight: 700;
    letter-spacing: 0.10em; text-transform: uppercase;
    cursor: pointer; flex: 0 0 auto;
    transition: color 0.08s ease, background 0.08s ease;
  }
  .wb-tab:hover { color: #dce6f2; background: rgba(220,230,242,0.03); }
  .wb-tab.active {
    color: #00e5ff; background: #0e1622;
    border-bottom: 2px solid #00e5ff; margin-bottom: -1px;
  }

  .wb-start-btn {
    flex: 0 0 auto;
    padding: 0 16px;
    min-width: 44px;
    border: 0;
    border-left: 1px solid #1e2836;
    background: transparent;
    color: #00e5ff;
    font-family: inherit;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    transition: background 0.08s ease;
  }
  .wb-start-btn:hover:not(:disabled) { background: rgba(0,229,255,0.08); }
  .wb-start-btn:disabled { color: #3d4756; cursor: not-allowed; }

  /* The ⚙ button uses a slightly smaller glyph size than the + button
     so both read as peers rather than the gear dominating. */
  #wbManageBtn {
    font-size: 15px;
    padding: 0 14px;
  }

  #wbStartPopup {
    position: absolute;
    top: 100%;
    right: 0;
    min-width: 180px;
    background: #0b1018;
    border: 1px solid #223040;
    border-top: 0;
    border-radius: 0 0 3px 3px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.65);
    padding: 4px 0;
    z-index: 100;
  }
  #wbStartPopup[hidden] { display: none; }
  .wb-start-item {
    display: block;
    width: 100%;
    padding: 6px 12px;
    border: 0;
    background: transparent;
    color: #dce6f2;
    text-align: left;
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  .wb-start-item:hover {
    background: rgba(0,229,255,0.08);
    color: #b8ecff;
  }
  .wb-start-empty {
    padding: 10px 12px;
    color: #5a6774;
    font-size: 10px;
    font-style: italic;
  }

  #wbFrameContainer {
    flex: 1 1 auto; position: relative; overflow: hidden;
    background: #0a0e14;
  }
  .wb-frame {
    position: absolute; inset: 0;
    width: 100%; height: 100%; border: 0;
  }

  .wb-banner {
    position: fixed;
    left: 50%;
    bottom: 24px;
    transform: translateX(-50%);
    background: #1a0a0e;
    color: #ffb0b0;
    border: 1px solid #d48590;
    border-radius: 3px;
    padding: 8px 16px;
    font-size: 11px;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s;
    z-index: 200;
  }
  .wb-banner.visible { opacity: 1; pointer-events: auto; }
"""

WB_BODY = """
<div id="wbTabRow">
  <div id="wbTabBar"></div>
  <button id="wbStartBtn" class="wb-start-btn"
          title="Start a playground and open it here"
          disabled>+</button>
  <button id="wbManageBtn" class="wb-start-btn"
          title="Manage playgrounds (running + available to start)">&#9881;</button>
  <div id="wbStartPopup" hidden></div>
</div>
<div id="wbFrameContainer"></div>
<div id="wbBanner" class="wb-banner"></div>
"""