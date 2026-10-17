"""
pgkit.remote_client — the browser's WebSocket client.

On page load it opens a WebSocket to the playground's server.  Every
value field registered via `pgkitRegisterField` and every action
registered via `pgkitRegisterAction` is published under its dotted
name and can be driven from any other connected client.

STATE PUBLICATION — NOTIFICATION, NOT POLLING
=============================================
The project tells the kit when a value changed:

    pgkitNotifyLocal(name)    queue one field's current value
    pgkitNotifyAll()          queue every registered field's current
                              value

There is no timer, no polling.  Actions are not included in state.

SCHEMA PUBLICATION — FIELDS AND ACTIONS
=======================================
On a `state_request` the kit answers with `state` and `schema`.
Field entries have `kind` of `"number"`, `"bool"`, or `"enum"`.
Action entries have `kind: "action"`.

ACTION PAYLOADS
===============
An action may declare `requires` in its meta: an array of
`{name, kind, label, default?, accept?}` entries describing what
the user must supply.  `kind` is `"text"` or `"file"`.  The kit
itself does not gather those values — a remote client does, and
sends them in the `command` message's `payload`.  The action's
`run(payload)` receives the payload.  An action that can be
invoked with no arguments (a purely local operation, or one that
can prompt via a browser dialog on the playground side) declares
no `requires` and simply receives `{}`.

The `command_result` echoes back the value returned by `run`, if
any, in its `result` field.  A synchronous return is available to
the remote client; an async action's eventual result is not (the
handler returns before the promise settles).

ECHO SUPPRESSION
================
Each patch carries a monotonic `seq` and the sender's `origin`
client id.  The receiving side ignores any patch whose `origin`
matches its own id.

PANEL VISIBILITY
================
Presence-driven.
"""

REMOTE_CLIENT_JS = r"""
/* ==========================================================================
   REMOTE CLIENT — STATE
   ========================================================================== */

const _pgkitRemote = {
  socket:          null,
  clientId:        null,
  connected:       false,
  reconnectDelay:  500,
  seq:             0,
  remoteCount:     0,
  browserCount:    0,
  registry:        {},
  protocolVersion: 1,
};

let _pgkitApplyingRemote = false;

let _pgkitNotifyPending = {};
let _pgkitFlushScheduled = false;

/* ==========================================================================
   REGISTRY
   ========================================================================== */

function pgkitRegisterField(name, opts) {
  if (!name) throw new Error("pgkitRegisterField: name required");
  if (_pgkitRemote.registry[name]) {
    throw new Error("pgkitRegisterField: duplicate " + name);
  }
  _pgkitRemote.registry[name] = {
    isAction: false,
    get: opts.get,
    set: opts.set,
    meta: opts.meta || {},
  };
}

function pgkitRegisterAction(name, opts) {
  if (!name) throw new Error("pgkitRegisterAction: name required");
  if (_pgkitRemote.registry[name]) {
    throw new Error("pgkitRegisterAction: duplicate " + name);
  }
  _pgkitRemote.registry[name] = {
    isAction: true,
    run: opts.run,
    meta: Object.assign(
      { kind: "action", label: opts.label || name },
      opts.meta || {}
    ),
  };
}

function pgkitDumpState() {
  const out = {};
  for (const name in _pgkitRemote.registry) {
    const r = _pgkitRemote.registry[name];
    if (r.isAction) continue;
    try { out[name] = r.get(); }
    catch (e) {}
  }
  return out;
}

function pgkitDumpSchema() {
  const out = {};
  for (const name in _pgkitRemote.registry) {
    out[name] = _pgkitRemote.registry[name].meta || {};
  }
  return out;
}

/* ==========================================================================
   NOTIFICATION
   ========================================================================== */

function pgkitNotifyLocal(name) {
  if (_pgkitApplyingRemote) return;
  if (!_pgkitRemote.connected) return;
  if (!_pgkitRemote.clientId) return;
  const f = _pgkitRemote.registry[name];
  if (!f || f.isAction) return;
  try { _pgkitNotifyPending[name] = f.get(); }
  catch (e) { return; }
  _pgkitScheduleFlush();
}

function pgkitNotifyAll() {
  if (_pgkitApplyingRemote) return;
  if (!_pgkitRemote.connected) return;
  if (!_pgkitRemote.clientId) return;
  for (const name in _pgkitRemote.registry) {
    const r = _pgkitRemote.registry[name];
    if (r.isAction) continue;
    try { _pgkitNotifyPending[name] = r.get(); }
    catch (e) {}
  }
  _pgkitScheduleFlush();
}

function _pgkitScheduleFlush() {
  if (_pgkitFlushScheduled) return;
  _pgkitFlushScheduled = true;
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(_pgkitFlush);
  } else {
    setTimeout(_pgkitFlush, 16);
  }
}

function _pgkitFlush() {
  _pgkitFlushScheduled = false;
  const n = Object.keys(_pgkitNotifyPending).length;
  if (n === 0) return;
  const values = _pgkitNotifyPending;
  _pgkitNotifyPending = {};
  _pgkitRemote.seq++;
  pgkitRemoteSend({
    type:   "patch",
    origin: _pgkitRemote.clientId,
    seq:    _pgkitRemote.seq,
    values: values,
  });
}

/* ==========================================================================
   WIRE
   ========================================================================== */

function pgkitRemoteSend(msg) {
  const s = _pgkitRemote.socket;
  if (!s || s.readyState !== WebSocket.OPEN) return;
  s.send(JSON.stringify(msg));
}

function pgkitRemoteConnect() {
  const url = (location.protocol === "https:" ? "wss:" : "ws:")
            + "//" + location.host + "/remote";

  let socket;
  try { socket = new WebSocket(url); }
  catch (e) { return; }
  _pgkitRemote.socket = socket;

  socket.addEventListener("open", () => {
    _pgkitRemote.connected = true;
    _pgkitRemote.reconnectDelay = 500;
    pgkitRemoteSend({
      type:     "hello",
      role:     "browser",
      protocol: _pgkitRemote.protocolVersion,
    });
  });

  socket.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    pgkitRemoteHandle(msg);
  });

  socket.addEventListener("close", () => {
    _pgkitRemote.connected = false;
    _pgkitRemote.clientId = null;
    _pgkitRemote.remoteCount = 0;
    _pgkitRemote.browserCount = 0;
    _pgkitNotifyPending = {};
    _pgkitFlushScheduled = false;
    _pgkitUpdatePanelVisibility();
    setTimeout(pgkitRemoteConnect, _pgkitRemote.reconnectDelay);
    _pgkitRemote.reconnectDelay = Math.min(
      _pgkitRemote.reconnectDelay * 2, 5000);
  });

  socket.addEventListener("error", () => {});
}

function pgkitRemoteHandle(msg) {
  switch (msg.type) {
    case "hello_ack":
      _pgkitRemote.clientId = msg.clientId;
      break;

    case "state_request":
      pgkitRemoteSend({
        type:   "state",
        seq:    _pgkitRemote.seq,
        values: pgkitDumpState(),
      });
      pgkitRemoteSend({
        type:   "schema",
        fields: pgkitDumpSchema(),
      });
      break;

    case "patch":
      if (msg.origin === _pgkitRemote.clientId) break;
      pgkitRemoteApplyPatch(msg.values || {});
      break;

    case "command":
      pgkitRemoteHandleCommand(msg);
      break;

    case "presence":
      _pgkitRemote.remoteCount  = msg.remote  || 0;
      _pgkitRemote.browserCount = msg.browser || 0;
      _pgkitUpdatePanelVisibility();
      break;
  }
}

function pgkitRemoteApplyPatch(values) {
  _pgkitApplyingRemote = true;
  try {
    for (const name in values) {
      const f = _pgkitRemote.registry[name];
      if (!f || f.isAction) continue;
      try { f.set(values[name]); } catch (e) {}
    }
  } finally {
    _pgkitApplyingRemote = false;
  }
  if (typeof draw === "function") draw();
  if (typeof pgkitSyncSchemaDom === "function") pgkitSyncSchemaDom();
}

/* ==========================================================================
   COMMANDS
   ========================================================================== */

function pgkitRemoteHandleCommand(msg) {
  let ok = true;
  let error = null;
  let result;

  try {
    const kind  = msg.kind;
    const entry = _pgkitRemote.registry[kind];

    if (entry && entry.isAction) {
      try {
        result = entry.run(msg.payload || {});
      } catch (e) {
        ok = false;
        error = String(e);
      }
    } else if (kind === "reset_view" && typeof resetView === "function") {
      resetView();
    } else if (kind === "deselect") {
      if (typeof selectedPair   !== "undefined") selectedPair = -1;
      if (typeof selectedSegment !== "undefined") selectedSegment = null;
      if (typeof selectedQuad   !== "undefined") selectedQuad = -1;
      if (typeof selectedSquare !== "undefined") selectedSquare = -1;
      if (typeof syncPairList === "function") syncPairList();
      if (typeof syncQuadList === "function") syncQuadList();
      if (typeof draw === "function") draw();
      pgkitNotifyAll();
    } else if (typeof pgkitHandleExtraCommand === "function") {
      const handled = pgkitHandleExtraCommand(kind, msg.payload || {});
      if (!handled) { ok = false; error = "unknown command"; }
    } else {
      ok = false;
      error = "unknown command";
    }
  } catch (e) {
    ok = false;
    error = String(e);
  }

  const reply = {
    type:  "command_result",
    id:    msg.id,
    ok:    ok,
    error: error,
  };
  if (result !== undefined) reply.result = result;
  pgkitRemoteSend(reply);
}

/* ==========================================================================
   PRESENCE → PANEL VISIBILITY
   ========================================================================== */

function _pgkitUpdatePanelVisibility() {
  const ui     = document.getElementById("ui");
  const banner = document.getElementById("remoteBanner");
  if (!ui) return;

  const hide = _pgkitRemote.connected && _pgkitRemote.remoteCount > 0;

  if (hide) {
    ui.classList.add("collapsed");
    if (banner) banner.classList.add("visible");
  } else {
    ui.classList.remove("collapsed");
    if (banner) banner.classList.remove("visible");
  }
}

/* ==========================================================================
   BOOT
   ========================================================================== */

(function pgkitRemoteBoot() {
  function start() { setTimeout(pgkitRemoteConnect, 100); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
"""
