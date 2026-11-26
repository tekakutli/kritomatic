# workbench/wb_base.py
WB_CSS = """
  html, body {
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    background: #0a0e14; color: #dce6f2;
    font-family: 'JetBrains Mono','Fira Code','Consolas','SF Mono',monospace;
    font-size: 11px;
  }
  body { display: flex; flex-direction: column; height: 100vh; }

  #wbTabBar {
    display: flex; flex: 0 0 auto;
    background: #0b1018; border-bottom: 1px solid #1e2836;
    overflow-x: auto; overflow-y: hidden; white-space: nowrap;
    min-height: 34px;
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

  #wbFrameContainer {
    flex: 1 1 auto; position: relative; overflow: hidden;
    background: #0a0e14;
  }
  .wb-frame {
    position: absolute; inset: 0;
    width: 100%; height: 100%; border: 0;
  }
"""

WB_BODY = """
<div id="wbTabBar"></div>
<div id="wbFrameContainer"></div>
"""