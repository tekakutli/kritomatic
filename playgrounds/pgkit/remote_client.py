"""
pgkit.remote_client — the browser's WebSocket client, as an ES module.

The module exports its register/notify functions.  Three things that
used to rely on globals are now injected at boot:

    setRemoteDraw(fn)            — called from pgkitRemoteApplyPatch
    setRemoteSelect(fn)          — dispatches `select` commands
    setRemoteDeselect(fn)        — dispatches `deselect` commands
    setRemoteExtraCommand(fn)    — dispatches playground-specific
                                   command kinds

The module's boot IIFE at the bottom still runs at module load,
opening the WebSocket.  All cross-module injectors are set by the
boot module *after* this one is imported — but before any command
arrives over the socket, since the WebSocket handshake takes at
least one round trip.
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
  levels:          {},
  protocolVersion: 1,
};

let _pgkitApplyingRemote = false;

let _pgkitNotifyPending   = {};
let _pgkitFlushScheduled  = false;
let _pgkitEntitiesPending = false;
let _pgkitLastEntitiesSig = null;

let _pgkitLastSent = {};

/* Injectable handlers.  Set at boot by the project's boot module. */
let _drawFn            = null;
let _selectFn          = null;
let _deselectFn        = null;
let _extraCommandFn    = null;

export function setRemoteDraw(fn)         { _drawFn = fn; }
export function setRemoteSelect(fn)       { _selectFn = fn; }
export function setRemoteDeselect(fn)     { _deselectFn = fn; }
export function setRemoteExtraCommand(fn) { _extraCommandFn = fn; }

/* ==========================================================================
   VALUE EQUALITY
   ========================================================================== */

function _pgkitValuesEqual(a, b) {
  if (a === b) return true;
  if (a == null || b == null) return a === b;
  if (typeof a !== "object" || typeof b !== "object") return false;
  try { return JSON.stringify(a) === JSON.stringify(b); }
  catch (e) { return false; }
}

/* ==========================================================================
   REGISTRY — FIELDS
   ========================================================================== */

export function pgkitRegisterField(name, opts) {
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

/* ==========================================================================
   REGISTRY — ACTIONS
   ========================================================================== */

export function pgkitRegisterAction(name, opts) {
  if (!name) throw new Error("pgkitRegisterAction: name required");
  if (_pgkitRemote.registry[name]) {
    throw new Error("pgkitRegisterAction: duplicate " + name);
  }

  const scope = opts.scope || "global";
  const level = (scope === "global") ? null : (opts.level || null);
  if (scope !== "global" && !level) {
    throw new Error("pgkitRegisterAction: " + name
                    + " needs a level for scope '" + scope + "'");
  }
  if (scope === "row" && typeof opts.appliesTo !== "function") {
    throw new Error("pgkitRegisterAction: " + name
                    + " (row scope) needs appliesTo(item)");
  }

  const meta = Object.assign(
    { kind: "action", label: opts.label || name },
    opts.meta || {}
  );
  meta.scope = scope;
  meta.level = level;

  _pgkitRemote.registry[name] = {
    isAction:  true,
    run:       opts.run,
    appliesTo: opts.appliesTo || null,
    meta:      meta,
  };
}

/* ==========================================================================
   REGISTRY — LEVELS
   ========================================================================== */

export function pgkitRegisterLevel(name, opts) {
  if (!name) throw new Error("pgkitRegisterLevel: name required");
  if (_pgkitRemote.levels[name]) {
    throw new Error("pgkitRegisterLevel: duplicate " + name);
  }
  _pgkitRemote.levels[name] = {
    label:    opts.label || name,
    items:    opts.items    || (() => []),
    selected: opts.selected || (() => null),
    parent:   opts.parent   || (() => null),
  };
}

/* ==========================================================================
   DUMPS
   ========================================================================== */

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

function pgkitDumpEntities() {
  const levels = {};
  const order  = [];

  for (const levelName in _pgkitRemote.levels) {
    const lvl = _pgkitRemote.levels[levelName];

    let rawItems = [];
    try { rawItems = lvl.items() || []; } catch (e) { rawItems = []; }

    let selected = null;
    try { selected = lvl.selected(); } catch (e) { selected = null; }

    let parent = null;
    try { parent = lvl.parent(); } catch (e) { parent = null; }

    const items = rawItems.map(it => {
      const applies = {};
      for (const aname in _pgkitRemote.registry) {
        const a = _pgkitRemote.registry[aname];
        if (!a.isAction) continue;
        if (a.meta.level !== levelName) continue;
        if (a.meta.scope !== "row") continue;
        if (!a.appliesTo) continue;
        try { applies[aname] = !!a.appliesTo(it); }
        catch (e) { applies[aname] = false; }
      }
      return {
        name:    it.name,
        label:   it.label,
        extra:   it.extra || null,
        applies: applies,
      };
    });

    levels[levelName] = {
      label:    lvl.label,
      selected: selected,
      parent:   parent,
      items:    items,
    };
    order.push(levelName);
  }

  return { levels, order };
}

/* ==========================================================================
   NOTIFICATION
   ========================================================================== */

export function pgkitNotifyLocal(name) {
  if (_pgkitApplyingRemote) return;
  if (!_pgkitRemote.connected) return;
  if (!_pgkitRemote.clientId) return;
  const f = _pgkitRemote.registry[name];
  if (!f || f.isAction) return;
  let v;
  try { v = f.get(); } catch (e) { return; }
  _pgkitNotifyPending[name] = v;
  _pgkitScheduleFlush();
}

export function pgkitNotifyAll() {
  if (_pgkitApplyingRemote) return;
  if (!_pgkitRemote.connected) return;
  if (!_pgkitRemote.clientId) return;
  for (const name in _pgkitRemote.registry) {
    const r = _pgkitRemote.registry[name];
    if (r.isAction) continue;
    let v;
    try { v = r.get(); } catch (e) { continue; }
    _pgkitNotifyPending[name] = v;
  }
  _pgkitEntitiesPending = true;
  _pgkitScheduleFlush();
}

export function pgkitNotifyEntities() {
  if (_pgkitApplyingRemote) return;
  if (!_pgkitRemote.connected) return;
  if (!_pgkitRemote.clientId) return;
  _pgkitEntitiesPending = true;
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

  const pending = _pgkitNotifyPending;
  _pgkitNotifyPending = {};

  const toSend = {};
  let any = false;
  for (const k in pending) {
    if (!(k in _pgkitLastSent)
        || !_pgkitValuesEqual(_pgkitLastSent[k], pending[k])) {
      toSend[k] = pending[k];
      any = true;
    }
  }

  if (any) {
    for (const k in toSend) _pgkitLastSent[k] = toSend[k];
    _pgkitRemote.seq++;
    _pgkitRemoteSend({
      type:   "patch",
      origin: _pgkitRemote.clientId,
      seq:    _pgkitRemote.seq,
      values: toSend,
    });
  }

  if (_pgkitEntitiesPending) {
    _pgkitEntitiesPending = false;
    const dump = pgkitDumpEntities();
    const sig  = JSON.stringify(dump);
    if (sig !== _pgkitLastEntitiesSig) {
      _pgkitLastEntitiesSig = sig;
      _pgkitRemoteSend({
        type:   "entities",
        levels: dump.levels,
        order:  dump.order,
      });
    }
  }
}

/* ==========================================================================
   WIRE
   ========================================================================== */

function _pgkitRemoteSend(msg) {
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
    _pgkitRemoteSend({
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
    _pgkitRemote.clientId  = null;
    _pgkitRemote.remoteCount  = 0;
    _pgkitRemote.browserCount = 0;
    _pgkitNotifyPending   = {};
    _pgkitFlushScheduled  = false;
    _pgkitEntitiesPending = false;
    _pgkitLastEntitiesSig = null;
    _pgkitLastSent        = {};
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

    case "state_request": {
      const values = pgkitDumpState();
      _pgkitRemoteSend({
        type:   "state",
        seq:    _pgkitRemote.seq,
        values: values,
      });
      _pgkitRemoteSend({
        type:   "schema",
        fields: pgkitDumpSchema(),
      });
      const dump = pgkitDumpEntities();
      _pgkitLastEntitiesSig = JSON.stringify(dump);
      _pgkitRemoteSend({
        type:   "entities",
        levels: dump.levels,
        order:  dump.order,
      });
      for (const name in values) _pgkitLastSent[name] = values[name];
      break;
    }

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

      _pgkitLastSent[name] = values[name];

      let cur;
      try { cur = f.get(); } catch (e) { cur = undefined; }
      if (_pgkitValuesEqual(cur, values[name])) continue;

      try { f.set(values[name]); } catch (e) {}
    }
  } finally {
    _pgkitApplyingRemote = false;
  }
  if (_drawFn) _drawFn();
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
      try { result = entry.run(msg.payload || {}); }
      catch (e) { ok = false; error = String(e); }
    } else if (kind === "select") {
      const payload = msg.payload || {};
      if (_selectFn) {
        const selOk = _selectFn(payload.level, payload.name);
        if (!selOk) { ok = false; error = "select failed"; }
        else { pgkitNotifyAll(); }
      } else {
        ok = false;
        error = "no select handler";
      }
    } else if (kind === "deselect") {
      if (_deselectFn) _deselectFn();
      if (_drawFn) _drawFn();
      pgkitNotifyAll();
    } else if (kind === "reset_view" && typeof resetView === "function") {
      resetView();
    } else if (_extraCommandFn) {
      const handled = _extraCommandFn(kind, msg.payload || {});
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
  _pgkitRemoteSend(reply);
}

/* ==========================================================================
   PRESENCE → PANEL VISIBILITY
   ========================================================================== */

function _pgkitUpdatePanelVisibility() {
  const ui         = document.getElementById("ui");
  const banner     = document.getElementById("remoteBanner");
  const restoreBtn = document.getElementById("restoreBtn");
  if (!ui) return;

  const hide = _pgkitRemote.connected && _pgkitRemote.remoteCount > 0;

  if (hide) {
    ui.classList.add("collapsed");
    if (banner) banner.classList.add("visible");
    if (restoreBtn) restoreBtn.classList.remove("visible");
  } else {
    ui.classList.remove("collapsed");
    if (banner) banner.classList.remove("visible");
  }
}

/* ==========================================================================
   BOOT — opens the WebSocket at module load
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
