# workbench/wb_client.py
WB_CLIENT_JS = r"""
/* workbench client — hub listener + iframe tab manager.
   Runs at http://hub:8780/workbench/workbench.html.
   No role="remote" connections; each iframe's own pgkit/remote_client.js
   handles that with role="browser". This page is purely a container. */

const _wb = {
  hubSocket: null,
  hubUrl: null,
  hubConnected: false,
  hubReconnectMs: 1000,
  hubReconnectTimer: null,
  playgrounds: [],
  tabs: new Map(),       // name -> { tabEl, iframeEl, url }
  activeTab: null,
};

/* ============ HUB ============ */

function wbDiscoverHub() {
  let url;
  try {
    const u = new URL("/__hub_ws", document.baseURI);
    u.protocol = (u.protocol === "https:") ? "wss:" : "ws:";
    url = u.toString();
  } catch (e) { return; }
  _wb.hubUrl = url;
  wbConnectHub();
}

function wbConnectHub() {
  if (_wb.hubReconnectTimer) {
    clearTimeout(_wb.hubReconnectTimer); _wb.hubReconnectTimer = null;
  }
  if (_wb.hubSocket) { try { _wb.hubSocket.close(); } catch (e) {} _wb.hubSocket = null; }

  let sock;
  try { sock = new WebSocket(_wb.hubUrl); }
  catch (e) { wbScheduleHubReconnect(); return; }
  _wb.hubSocket = sock;

  sock.addEventListener("open", () => {
    _wb.hubConnected = true;
    _wb.hubReconnectMs = 1000;
    try { sock.send(JSON.stringify({ type: "hello", role: "panel" })); } catch (e) {}
  });

  sock.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "playgrounds") {
      _wb.playgrounds = msg.playgrounds || [];
      wbSyncTabs();
    }
  });

  sock.addEventListener("close", () => {
    _wb.hubConnected = false;
    _wb.playgrounds = [];
    wbScheduleHubReconnect();
  });

  sock.addEventListener("error", () => {});
}

function wbScheduleHubReconnect() {
  if (_wb.hubReconnectTimer) return;
  _wb.hubReconnectTimer = setTimeout(() => {
    _wb.hubReconnectTimer = null;
    wbConnectHub();
  }, _wb.hubReconnectMs);
  _wb.hubReconnectMs = Math.min(_wb.hubReconnectMs * 2, 10000);
}

function wbHubHttpBase() {
  if (!_wb.hubUrl) return null;
  try {
    const u = new URL(_wb.hubUrl);
    const proto = (u.protocol === "wss:") ? "https:" : "http:";
    return proto + "//" + u.host;
  } catch (e) { return null; }
}

/* ============ TAB LIFECYCLE ============ */

function wbSyncTabs() {
  const base = wbHubHttpBase();
  if (!base) return;

  const active = new Set();
  for (const p of _wb.playgrounds) {
    if (p.role !== "playground") continue;
    active.add(p.name);
    if (!_wb.tabs.has(p.name)) wbCreateTab(p, base);
  }
  for (const name of Array.from(_wb.tabs.keys())) {
    if (!active.has(name)) wbRemoveTab(name);
  }

  /* Activate hash-named tab if present, else the first tab. */
  const hashName = (location.hash || "").slice(1);
  if (hashName && _wb.tabs.has(hashName) && _wb.activeTab !== hashName) {
    wbActivateTab(hashName);
  } else if (!_wb.activeTab && _wb.tabs.size > 0) {
    wbActivateTab(Array.from(_wb.tabs.keys()).sort()[0]);
  }
}

function wbCreateTab(pg, base) {
  const name = pg.name;
  const html = pg.html_file || (name + "_playground.html");
  const url = base + "/" + name + "/" + html;

  const tabEl = document.createElement("button");
  tabEl.className = "wb-tab";
  tabEl.dataset.name = name;
  tabEl.textContent = name;
  tabEl.title = url;
  tabEl.addEventListener("click", () => wbActivateTab(name));

  const iframeEl = document.createElement("iframe");
  iframeEl.className = "wb-frame";
  iframeEl.src = url;
  iframeEl.style.display = "none";
  iframeEl.dataset.name = name;

  document.getElementById("wbTabBar").appendChild(tabEl);
  document.getElementById("wbFrameContainer").appendChild(iframeEl);

  _wb.tabs.set(name, { tabEl, iframeEl, url });

  if ((location.hash || "").slice(1) === name) wbActivateTab(name);
}

function wbRemoveTab(name) {
  const t = _wb.tabs.get(name);
  if (!t) return;
  try { t.iframeEl.src = "about:blank"; } catch (e) {}
  try { t.iframeEl.remove(); } catch (e) {}
  try { t.tabEl.remove(); } catch (e) {}
  _wb.tabs.delete(name);
  if (_wb.activeTab === name) {
    _wb.activeTab = null;
    if (_wb.tabs.size > 0) {
      wbActivateTab(Array.from(_wb.tabs.keys()).sort()[0]);
    }
  }
}

function wbActivateTab(name) {
  if (!_wb.tabs.has(name)) return;
  for (const [n, t] of _wb.tabs) {
    t.tabEl.classList.toggle("active", n === name);
    t.iframeEl.style.display = (n === name) ? "block" : "none";
  }
  _wb.activeTab = name;
  try { history.replaceState(null, "", "#" + name); } catch (e) {}
}

window.addEventListener("hashchange", () => {
  const name = (location.hash || "").slice(1);
  if (name && _wb.tabs.has(name)) wbActivateTab(name);
});

/* ============ BOOT ============ */

(function wbBoot() {
  const start = () => setTimeout(wbDiscoverHub, 200);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
"""