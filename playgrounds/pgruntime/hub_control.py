"""pgruntime.hub_control — shared client-side hub control + workbench-open helpers.

Serves the JS string `HUB_CONTROL_JS`, which is concatenated into the
body `<script>` of both `remote_panel` and `workbench` pages *before*
their own page scripts.  Both pages are classic-script pages (not ES
modules); the shared snippet simply declares its API as top-level
functions, which become available to the page scripts that follow.

WHY NOT AN ES MODULE?
=====================
The playground pages use ES modules through `/modules/`, which is
served by each playground's own server.  `remote_panel` and
`workbench` are not playgrounds: their HTML is a single concatenated
`<script>` block, and every symbol they use (rpLog, flashStatus, rp*)
is defined by top-level function declarations.  Adding a module
loader to those two pages would mean re-importing every existing
symbol as a module import — a refactor with no benefit.  The shared
snippet follows the classic-script model the pages already use.

WHY PGRUNTIME, NOT PGKIT?
=========================
pgkit is the transport/shell layer (server, WS hub, HTML shell, log
mirroring).  pgruntime is the playground-runtime shared surface.  The
"open a playground in the workbench if running, else in a new tab"
convenience is a user-facing behavior — it uses the hub's management
WS to discover and launch playgrounds, but it is not a hub-protocol
primitive.  Placing it in pgruntime keeps it adjacent to the other
helpers playground-adjacent tooling uses (daemon client, ports,
event bus).  pgkit stays lean by contract.

WHAT IS EXPORTED
================
    createHubControl(opts)      — live control object
    openInWorkbench(pg, ctx)    — pure helper, returns Window|null
    openInNewTab(pg, ctx)       — pure helper, returns Window|null
    openPlayground(pg, ctx)     — try workbench first, then new tab
    launchAndOpen(name, ctrl, opts) — start (if needed), then open

The control object returned by `createHubControl` opens a listener-
panel WS (`{type:"hello", role:"panel"}`) to the hub, owns its
reconnect logic, and exposes:

    getPlaygrounds()      last broadcast `playgrounds` list
    isConnected()         WS is open
    hubHttpBase()         "http://127.0.0.1:8780" (or null)
    findPlayground(name)  registration or null
    findLauncherFor(name) launcher registration whose .available
                          contains name, or null

    startPlayground(name, {timeoutMs?})    Promise<registration>
    stopPlayground(name)                    Promise<{success, message}>
    restartPlayground(name, {timeoutMs?})   Promise<registration>
    killPlayground(name, hard)              Promise<{success, message}>
    shutdownHub()                           Promise<{success, message}>

    close()                                 stop WS, disable reconnect

`startPlayground` awaits both the launcher_result and the resulting
`playgrounds` broadcast, so callers can immediately use the returned
registration.  It rejects on: hub not connected, launcher rejected,
timeout waiting for registration.  The other commands resolve to
`{success, message}` and never reject — callers get uniform error
reporting without try/catch.

The listener-panel WS is the same one that `remote_panel` and
`workbench` opened ad-hoc before this module existed.  The wire
protocol is unchanged — the hub treats it exactly as before.
"""

HUB_CONTROL_JS = r"""
/* ==========================================================================
   HUB CONTROL — shared between remote_panel and workbench
   ==========================================================================
   Concatenated into the body <script> block of both pages, before
   their own page scripts.  All exports are top-level function
   declarations so the page scripts that follow (also classic scripts)
   can call them unqualified.  See pgruntime/hub_control.py for the
   design rationale.
   ========================================================================== */

function createHubControl(opts) {
  opts = opts || {};

  var state = {
    socket:          null,
    url:             null,
    connected:       false,
    reconnectMs:     1000,
    reconnectTimer:  null,
    playgrounds:     [],
    shuttingDown:    false,
    pendingLauncher: new Map(),
    pendingShutdown: null,
    waiters:         [],
  };

  function logError(text) {
    try { console.error("[hub_control] " + text); } catch (_) {}
    if (typeof opts.onError === "function") {
      try { opts.onError(text); } catch (_) {}
    }
  }

  function deriveUrl() {
    if (opts.hubUrl) return opts.hubUrl;
    try {
      var u = new URL("/__hub_ws", document.baseURI);
      u.protocol = (u.protocol === "https:") ? "wss:" : "ws:";
      return u.toString();
    } catch (e) {
      return null;
    }
  }

  function connect() {
    if (state.reconnectTimer) {
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }
    if (state.socket) {
      try { state.socket.close(); } catch (e) {}
      state.socket = null;
    }
    if (state.shuttingDown) return;
    if (!state.url) state.url = deriveUrl();
    if (!state.url) return;

    var sock;
    try { sock = new WebSocket(state.url); }
    catch (e) { scheduleReconnect(); return; }
    state.socket = sock;

    sock.addEventListener("open", function () {
      if (state.socket !== sock) return;
      state.connected = true;
      state.reconnectMs = 1000;
      try {
        sock.send(JSON.stringify({ type: "hello", role: "panel" }));
      } catch (e) {}
      if (typeof opts.onConnected === "function") {
        try { opts.onConnected(); } catch (_) {}
      }
    });

    sock.addEventListener("message", function (ev) {
      if (state.socket !== sock) return;
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handleMessage(msg);
    });

    sock.addEventListener("close", function () {
      if (state.socket !== sock) return;
      state.connected = false;
      state.playgrounds = [];
      if (typeof opts.onDisconnected === "function") {
        try { opts.onDisconnected(); } catch (_) {}
      }
      if (!state.shuttingDown) scheduleReconnect();
    });

    sock.addEventListener("error", function () {});
  }

  function scheduleReconnect() {
    if (state.shuttingDown) return;
    if (state.reconnectTimer) return;
    state.reconnectTimer = setTimeout(function () {
      state.reconnectTimer = null;
      connect();
    }, state.reconnectMs);
    state.reconnectMs = Math.min(state.reconnectMs * 2, 10000);
  }

  function handleMessage(msg) {
    var t = msg.type;

    if (t === "playgrounds") {
      state.playgrounds = msg.playgrounds || [];
      if (typeof opts.onPlaygrounds === "function") {
        try { opts.onPlaygrounds(state.playgrounds); }
        catch (e) { logError("onPlaygrounds threw: " + e); }
      }
      /* Resolve waiters whose playground now exists. */
      for (var i = state.waiters.length - 1; i >= 0; i--) {
        var w = state.waiters[i];
        var reg = state.playgrounds.find(function (p) {
          return p.name === w.name && p.role === "playground";
        });
        if (reg) {
          clearTimeout(w.timeoutId);
          state.waiters.splice(i, 1);
          try { w.resolve(reg); } catch (_) {}
        }
      }
      return;
    }

    if (t === "launcher_result") {
      if (typeof opts.onLauncherResult === "function") {
        try { opts.onLauncherResult(msg); } catch (_) {}
      }
      var rid = msg.request_id;
      if (rid && state.pendingLauncher.has(rid)) {
        var p = state.pendingLauncher.get(rid);
        state.pendingLauncher.delete(rid);
        if (msg.success) {
          try { p.resolve(msg); } catch (_) {}
        } else {
          try { p.reject(new Error(msg.message || "launcher failed")); }
          catch (_) {}
        }
      }
      return;
    }

    if (t === "kill_result") {
      if (typeof opts.onKillResult === "function") {
        try { opts.onKillResult(msg); } catch (_) {}
      }
      return;
    }

    if (t === "shutdown_ack") {
      if (typeof opts.onShutdownAck === "function") {
        try { opts.onShutdownAck(); } catch (_) {}
      }
      if (state.pendingShutdown) {
        var finish = state.pendingShutdown.finish;
        state.pendingShutdown = null;
        if (finish) finish({ success: true, message: "hub is shutting down" });
      }
      state.shuttingDown = true;
      if (state.socket) {
        try { state.socket.close(); } catch (_) {}
        state.socket = null;
      }
      return;
    }

    if (t === "shutdown") {
      /* Unsolicited: hub is telling us it's going away even though we
         did not send shutdown_hub. */
      if (state.pendingShutdown) {
        var f2 = state.pendingShutdown.finish;
        state.pendingShutdown = null;
        if (f2) f2({ success: true, message: "hub is shutting down" });
      }
      state.shuttingDown = true;
      if (state.socket) {
        try { state.socket.close(); } catch (_) {}
        state.socket = null;
      }
      return;
    }
  }

  function send(obj) {
    var s = state.socket;
    if (!s || s.readyState !== WebSocket.OPEN) return false;
    try { s.send(JSON.stringify(obj)); return true; }
    catch (e) { return false; }
  }

  function genReqId() {
    return "hub-" + Math.random().toString(36).slice(2, 10);
  }

  function waitForPlayground(name, timeoutMs) {
    return new Promise(function (resolve, reject) {
      var existing = state.playgrounds.find(function (p) {
        return p.name === name && p.role === "playground";
      });
      if (existing) { resolve(existing); return; }
      var w = { name: name, resolve: resolve, reject: reject, timeoutId: null };
      w.timeoutId = setTimeout(function () {
        var idx = state.waiters.indexOf(w);
        if (idx >= 0) state.waiters.splice(idx, 1);
        reject(new Error("playground " + name + " did not register within "
                         + timeoutMs + "ms"));
      }, timeoutMs);
      state.waiters.push(w);
    });
  }

  function launcherCommand(kind, name, timeoutMs) {
    timeoutMs = timeoutMs || 10000;
    return new Promise(function (resolve, reject) {
      var request_id = genReqId();
      var timeoutId = setTimeout(function () {
        if (state.pendingLauncher.has(request_id)) {
          state.pendingLauncher.delete(request_id);
          reject(new Error("launcher did not respond within "
                           + timeoutMs + "ms"));
        }
      }, timeoutMs);
      state.pendingLauncher.set(request_id, {
        resolve: function (m) { clearTimeout(timeoutId); resolve(m); },
        reject:  function (e) { clearTimeout(timeoutId); reject(e); },
        name: name,
        action: kind,
      });
      var ok = send({ type: kind, name: name, request_id: request_id });
      if (!ok) {
        clearTimeout(timeoutId);
        state.pendingLauncher.delete(request_id);
        reject(new Error("hub not connected"));
      }
    });
  }

  var api = {
    getPlaygrounds: function () { return state.playgrounds; },
    isConnected:    function () { return state.connected; },

    hubHttpBase: function () {
      if (!state.url) return null;
      try {
        var u = new URL(state.url);
        var proto = (u.protocol === "wss:") ? "https:" : "http:";
        return proto + "//" + u.host;
      } catch (e) { return null; }
    },

    findPlayground: function (name) {
      for (var i = 0; i < state.playgrounds.length; i++) {
        var p = state.playgrounds[i];
        if (p.name === name && p.role === "playground") return p;
      }
      return null;
    },

    findLauncherFor: function (name) {
      for (var i = 0; i < state.playgrounds.length; i++) {
        var p = state.playgrounds[i];
        if (p.role !== "launcher") continue;
        if ((p.available || []).indexOf(name) >= 0) return p;
      }
      return null;
    },

    startPlayground: async function (name, opts2) {
      opts2 = opts2 || {};
      var existing = api.findPlayground(name);
      if (existing) return existing;
      await launcherCommand("launcher_start", name);
      return await waitForPlayground(name, opts2.timeoutMs || 15000);
    },

    stopPlayground: function (name) {
      return new Promise(function (resolve) {
        if (!state.connected) {
          resolve({ success: false, message: "hub not connected" });
          return;
        }
        var ok = send({
          type: "launcher_stop",
          name: name,
          request_id: genReqId(),
        });
        resolve(ok
          ? { success: true,  message: "stop sent" }
          : { success: false, message: "send failed" });
      });
    },

    restartPlayground: async function (name, opts2) {
      opts2 = opts2 || {};
      await launcherCommand("launcher_restart", name);
      return await waitForPlayground(name, opts2.timeoutMs || 15000);
    },

    killPlayground: function (name, hard) {
      return new Promise(function (resolve) {
        if (!state.connected) {
          resolve({ success: false, message: "hub not connected" });
          return;
        }
        var ok = send({ type: "kill", name: name, hard: !!hard });
        resolve(ok
          ? { success: true,  message: "kill sent" }
          : { success: false, message: "send failed" });
      });
    },

    shutdownHub: function () {
      return new Promise(function (resolve) {
        if (!state.connected) {
          resolve({ success: false, message: "hub not connected" });
          return;
        }
        if (state.shuttingDown) {
          resolve({ success: false, message: "already shutting down" });
          return;
        }
        if (state.pendingShutdown) {
          resolve({ success: false, message: "shutdown already in flight" });
          return;
        }
        state.shuttingDown = true;
        var done = false;
        var timeoutId = null;
        function finish(result) {
          if (done) return;
          done = true;
          if (timeoutId) { clearTimeout(timeoutId); timeoutId = null; }
          state.pendingShutdown = null;
          if (!result.success) state.shuttingDown = false;
          resolve(result);
        }
        timeoutId = setTimeout(function () {
          finish({
            success: false,
            message: "no acknowledgment from hub within 5s",
          });
        }, 5000);
        state.pendingShutdown = { finish: finish };
        var ok = send({ type: "shutdown_hub" });
        if (!ok) finish({ success: false, message: "hub not connected" });
      });
    },

    close: function () {
      state.shuttingDown = true;
      if (state.reconnectTimer) {
        clearTimeout(state.reconnectTimer);
        state.reconnectTimer = null;
      }
      if (state.socket) {
        try { state.socket.close(); } catch (e) {}
        state.socket = null;
      }
    },
  };

  connect();
  return api;
}

/* ==========================================================================
   OPEN HELPERS — pure, no state
   ========================================================================== */

function openInWorkbench(pg, ctx) {
  ctx = ctx || {};
  if (!pg || !pg.name) return null;
  if (!ctx.hubBase) return null;
  var wb = null;
  var list = ctx.playgrounds || [];
  for (var i = 0; i < list.length; i++) {
    var p = list[i];
    if (p.role === "panel" && p.name === "workbench") { wb = p; break; }
  }
  if (!wb) return null;
  var wbHtml = wb.html_file || "workbench.html";
  var target = ctx.targetWindow || "workbench";
  var url = ctx.hubBase + "/workbench/" + wbHtml
          + "#" + encodeURIComponent(pg.name);
  try { return window.open(url, target); }
  catch (e) { return null; }
}

function openInNewTab(pg, ctx) {
  ctx = ctx || {};
  if (!pg || !pg.name) return null;
  if (!ctx.hubBase) return null;
  var html = pg.html_file || (pg.name + "_playground.html");
  var url = ctx.hubBase + "/" + pg.name + "/" + html;
  try { return window.open(url, "_blank"); }
  catch (e) { return null; }
}

function openPlayground(pg, ctx) {
  return openInWorkbench(pg, ctx) || openInNewTab(pg, ctx);
}

async function launchAndOpen(name, ctrl, opts) {
  opts = opts || {};
  var pg = ctrl.findPlayground(name);
  if (!pg) {
    pg = await ctrl.startPlayground(name, { timeoutMs: opts.timeoutMs });
  }
  var ctx = {
    playgrounds:  ctrl.getPlaygrounds(),
    hubBase:      ctrl.hubHttpBase(),
    targetWindow: opts.targetWindow,
  };
  var win = null;
  if (opts.preferWorkbench !== false) {
    win = openInWorkbench(pg, ctx);
  }
  if (!win) win = openInNewTab(pg, ctx);
  return { pg: pg, win: win };
}
"""