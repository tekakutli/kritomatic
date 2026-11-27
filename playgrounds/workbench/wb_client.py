# workbench/wb_client.py
WB_CLIENT_JS = r"""
/* workbench client — hub listener + iframe tab manager + catalog launcher.

   Runs at http://hub:8780/workbench/workbench.html (proxied from
   the workbench's own port by the hub).

   No role="remote" connections; each iframe's own
   pgkit/remote_client.js handles that with role="browser".  This
   page is purely a container.

   The hub WS is opened by the shared `createHubControl` factory from
   pgruntime/hub_control.py — same snippet used by the remote panel.
   This file wires its callbacks into the tab manager and the
   "+ Start & Open" popup.

   START & OPEN — TWO LAYERS
   =========================
   The catalog of *available* playgrounds lives on the workbench's
   own HTTP server at POST /catalog, not in the hub's `playgrounds`
   broadcast.  Reason: `playgrounds` only reflects what is
   currently registered.  If the user is running playgrounds
   individually (`python outpaint/outpaint_playground.py`) — the
   common case — there is no launcher registration, so
   `launcher.available` is empty and the "+" button has nothing to
   offer.  `/catalog` is served by the workbench itself via
   `pkgutil.iter_modules` over `playgrounds/`, so it always lists
   every playground that can be started.

   Clicking a name in the "+" popup POSTs to `/start`, which spawns
   `python <entry>` as a subprocess, detached (`start_new_session`).
   The subprocess registers with the hub independently, the hub
   broadcasts a new `playgrounds` event, and this page's tab
   manager picks it up — no further action from wbStartAndOpen
   beyond waiting for the broadcast to arrive.

   The hub's `playgrounds` list is still used to filter the catalog
   down to *not-yet-running* names, and to detect that a just-
   spawned subprocess has finished registering.

   THE `+` BUTTON IS NOT GATED ON HUB CONNECTIVITY
   ================================================
   The `/start` route is served by the workbench's own HTTP server —
   it does not touch the hub.  Spawning a playground succeeds with
   the hub down; the newly-started playground's own hub_client will
   spawn the hub if needed.  When the hub comes up, this page's
   `onConnected` fires, `playgrounds` broadcast arrives, and the
   tab appears.

   Gating the `+` button on `hubCtrl.isConnected()` used to make the
   button dead for the first 1–3 seconds of every fresh session
   (hub has to spawn).  That looked like the workbench "not being
   ready".  Removed: the button is always clickable.  If the user
   clicks it while the hub is still down, `wbStartAndOpen` spawns
   the subprocess normally and its 20 s poll window is enough for
   the hub to come up and broadcast the registration.
*/

const _wb = {
  playgrounds: [],       // populated from onPlaygrounds
  tabs:        new Map(),   // name -> { tabEl, iframeEl, url }
  activeTab:   null,
  catalog:     [],       // populated from POST /catalog
};

/* ============ HUB CONTROL ============ */

const hubCtrl = createHubControl({
  onPlaygrounds: function (list) {
    _wb.playgrounds = list;
    wbSyncTabs();
    wbRefreshStartButton();
  },
  onConnected: function () {
    wbRefreshStartButton();
  },
  onDisconnected: function () {
    _wb.playgrounds = [];
    wbRefreshStartButton();
    const pop = document.getElementById("wbStartPopup");
    if (pop) pop.hidden = true;
  },
  onError: function (msg) {
    try { console.error("[wb hub]", msg); } catch (_) {}
  },
});

/* ============ TAB LIFECYCLE ============ */

function wbSyncTabs() {
  const base = hubCtrl.hubHttpBase();
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

  /* Activate the hash-named tab, creating it if a matching
     playground is now registered but has no tab yet.  This is what
     allows remote_panel's "Open in workbench" link (hash only) to
     deliver a playground into an already-open workbench without a
     reload — the hash fires, wbSyncTabs runs, and if the tab did
     not exist yet we create it here. */
  const hashName = (location.hash || "").slice(1);
  if (hashName && wbActivateOrCreateTab(hashName)) return;

  if (!_wb.activeTab && _wb.tabs.size > 0) {
    wbActivateTab(Array.from(_wb.tabs.keys()).sort()[0]);
  }
}

function wbActivateOrCreateTab(name) {
  if (!name) return false;
  if (_wb.tabs.has(name)) {
    if (_wb.activeTab !== name) wbActivateTab(name);
    return true;
  }
  const base = hubCtrl.hubHttpBase();
  if (!base) return false;
  const pg = _wb.playgrounds.find(
    p => p.name === name && p.role === "playground"
  );
  if (!pg) return false;
  wbCreateTab(pg, base);
  wbActivateTab(name);
  return true;
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
  wbActivateOrCreateTab(name);
});

/* ============ BANNER (transient error reporting) ============ */

function _wbShowBanner(text, cls) {
  const banner = document.getElementById("wbBanner");
  if (!banner) return;
  banner.textContent = text;
  banner.className = "wb-banner visible" + (cls ? " " + cls : "");
  setTimeout(() => banner.classList.remove("visible"), 6000);
}

/* ============ START & OPEN ============ */

/* Fetch the catalog from the workbench's own HTTP server.
   Populates _wb.catalog.  Silent on network error — the popup
   handles the empty case. */
async function wbRefreshCatalog() {
  try {
    const res = await fetch("./catalog", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.success) {
      _wb.catalog = data.playgrounds || [];
    }
  } catch (e) {
    try { console.error("[wb catalog]", e); } catch (_) {}
  }
}

/* Catalog names minus what is currently registered. */
function wbAvailableNames() {
  const running = new Set();
  for (const p of _wb.playgrounds) {
    if (p.role === "playground") running.add(p.name);
  }
  const out = [];
  for (const d of _wb.catalog) {
    if (!running.has(d.name)) out.push(d.name);
  }
  return out.sort();
}

/* The + button is never disabled — see module docstring,
   THE `+` BUTTON IS NOT GATED ON HUB CONNECTIVITY.  The tooltip
   is still informative: it tells the user the hub is not yet up,
   which is useful context if clicking + fails to produce a tab
   within a few seconds. */
function wbRefreshStartButton() {
  const btn = document.getElementById("wbStartBtn");
  if (!btn) return;
  btn.disabled = false;
  btn.title = hubCtrl.isConnected()
    ? "Start a playground and open it here"
    : "Hub not connected yet \u2014 the playground will start and "
      + "register once the hub comes up";
}

async function wbOpenStartPopup() {
  const pop = document.getElementById("wbStartPopup");
  if (!pop) return;
  if (!pop.hidden) { pop.hidden = true; return; }

  pop.innerHTML = "";
  const loading = document.createElement("div");
  loading.className = "wb-start-empty";
  loading.textContent = "Loading\u2026";
  pop.appendChild(loading);
  pop.hidden = false;

  await wbRefreshCatalog();

  pop.innerHTML = "";
  const names = wbAvailableNames();
  if (names.length === 0) {
    const empty = document.createElement("div");
    empty.className = "wb-start-empty";
    empty.textContent = hubCtrl.isConnected()
      ? "No playgrounds available to start."
      : "Hub not connected \u2014 no playgrounds to start.";
    pop.appendChild(empty);
  } else {
    for (const name of names) {
      const row = document.createElement("button");
      row.className = "wb-start-item";
      row.textContent = name;
      row.title = "Start " + name + " and open it in the workbench.";
      row.addEventListener("click", () => {
        pop.hidden = true;
        wbStartAndOpen(name);
      });
      pop.appendChild(row);
    }
  }

  const dismiss = () => {
    pop.hidden = true;
    document.removeEventListener("mousedown", onMouse, true);
    document.removeEventListener("keydown", onKey, true);
  };
  const onMouse = (ev) => {
    if (pop.contains(ev.target)) return;
    if (ev.target && ev.target.id === "wbStartBtn") return;
    dismiss();
  };
  const onKey = (ev) => { if (ev.key === "Escape") dismiss(); };
  setTimeout(() => {
    document.addEventListener("mousedown", onMouse, true);
    document.addEventListener("keydown", onKey, true);
  }, 0);
}

async function wbStartAndOpen(name) {
  /* Already running?  Just activate.  This handles the case where
     the subprocess registered before we got here (e.g. rapid
     double-click on the same name, or a stale catalog entry). */
  const existing = hubCtrl.findPlayground(name);
  if (existing) {
    if (_wb.tabs.has(existing.name)) wbActivateTab(existing.name);
    else {
      const base = hubCtrl.hubHttpBase();
      if (base) { wbCreateTab(existing, base); wbActivateTab(existing.name); }
    }
    return;
  }

  let data;
  try {
    const res = await fetch("./start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    data = await res.json();
  } catch (e) {
    _wbShowBanner("Start " + name + " failed: " + (e.message || e));
    return;
  }
  if (!data || !data.success) {
    const detail = data
      ? (data.message || data.error || "unknown")
      : "no response";
    _wbShowBanner("Start " + name + " failed: " + detail);
    if (data && data.stderr) {
      try { console.error("[wb start stderr]", data.stderr); } catch (_) {}
    }
    return;
  }

  /* The subprocess is alive.  Wait for its registration to reach
     this workbench via the hub's `playgrounds` broadcast, then
     activate the tab.  The tab itself is created by wbSyncTabs
     when the broadcast arrives. */
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const pg = hubCtrl.findPlayground(name);
    if (pg) {
      if (!_wb.tabs.has(pg.name)) {
        const base = hubCtrl.hubHttpBase();
        if (base) wbCreateTab(pg, base);
      }
      if (_wb.tabs.has(pg.name)) wbActivateTab(pg.name);
      return;
    }
    await new Promise((r) => setTimeout(r, 150));
  }
  _wbShowBanner("Start " + name + " timed out waiting for registration");
}

/* ============ INSTALL ============ */

(function wbInstall() {
  function start() {
    wbRefreshStartButton();
    const btn = document.getElementById("wbStartBtn");
    if (btn) btn.addEventListener("click", () => { wbOpenStartPopup(); });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
"""
