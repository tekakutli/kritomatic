"""
pgkit.remote_client — the browser's WebSocket client.

On page load it opens a WebSocket to the playground's server.  Fields
(`pgkitRegisterField`), actions (`pgkitRegisterAction`), and levels
(`pgkitRegisterLevel`) are all published over the wire.

STATE PUBLICATION — NOTIFICATION, NOT POLLING
=============================================
The project calls `pgkitNotifyLocal(name)` or `pgkitNotifyAll()` when
a value or a selection changes.  Notifications coalesce to one patch
per animation frame.

ENTITIES — THE HIERARCHY OF ITEMS
=================================
A playground registers one level per entity kind: `pairs`, `squares`,
`files`, `documents`, `items`.  Each level declares:

    label      display name (e.g. "Pairs")
    items()    → [{name, label, extra?}]
    selected() → the name of the currently-selected item, or null
    parent()   → {level, name} or null (for nested levels like
                 segments-under-a-selected-pair)

`pgkitDumpEntities()` walks every level and produces

    {levels: {name: {label, selected, parent, items}}, order: [...]}

The `applies` map on each item is populated by running every row-
scope action's `appliesTo(item)` predicate for that level.  The map
is what a remote client uses to decide whether to render the row
button.  `pgkitNotifyEntities()` schedules an entities refresh;
`pgkitNotifyAll()` triggers it as a side effect.

ACTION SCOPES
=============
Every action declares `scope`:

    "global"     shown at the root; no level; no selection.
    "all"        shown in a level's header; always enabled.
    "selection"  shown in a level's header; disabled when the level's
                 selected is null.
    "row"        shown on every item of its level for which
                 `appliesTo(item)` returns true.  `run(payload)` gets
                 `payload.target = {level, name}` when invoked from a
                 row button, and can also use the level's current
                 selection if no target is provided.

SELECTION
=========
A remote client can select a level's item by sending a `select`
command with `{level, name}`.  The playground's `pgkitHandleSelect`
translates this to its model.  Pair uses it to select a pair or a
segment without needing the panel's list.

ECHO SUPPRESSION
================
Each patch carries a monotonic `seq` and the sender's `origin`
client id.  `_pgkitApplyingRemote` is a second guard, active only
during `pgkitRemoteApplyPatch`.

PANEL VISIBILITY
================
Presence-driven.  When at least one non-browser client is connected,
`#ui` is hidden and `#remoteBanner` is shown.
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

let _pgkitApplyingRemote   = false;
let _pgkitNotifyPending    = {};
let _pgkitFlushScheduled   = false;
let _pgkitEntitiesPending  = false;
let _pgkitLastEntitiesSig  = null;

/* ==========================================================================
   REGISTRY — fields
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

/* ==========================================================================
   REGISTRY — actions
   ==========================================================================
   `scope` is one of "global" | "all" | "selection" | "row".
   `level` is required for every scope except "global".
   `appliesTo(item)` is required only for "row" scope; it must be
   cheap (it runs once per item per entities refresh). */

function pgkitRegisterAction(name, opts) {
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
   REGISTRY — levels
   ========================================================================== */

function pgkitRegisterLevel(name, opts) {
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
  _pgkitEntitiesPending = true;
  _pgkitScheduleFlush();
}

function pgkitNotifyEntities() {
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

  const n = Object.keys(_pgkitNotifyPending).length;
  if (n > 0) {
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

  if (_pgkitEntitiesPending) {
    _pgkitEntitiesPending = false;
    const dump = pgkitDumpEntities();
    const sig  = JSON.stringify(dump);
    if (sig !== _pgkitLastEntitiesSig) {
      _pgkitLastEntitiesSig = sig;
      pgkitRemoteSend({
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
    _pgkitRemote.connected     = true;
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
    _pgkitRemote.connected    = false;
    _pgkitRemote.clientId     = null;
    _pgkitRemote.remoteCount  = 0;
    _pgkitRemote.browserCount = 0;
    _pgkitNotifyPending       = {};
    _pgkitFlushScheduled      = false;
    _pgkitEntitiesPending     = false;
    _pgkitLastEntitiesSig     = null;
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
      pgkitRemoteSend({
        type:   "state",
        seq:    _pgkitRemote.seq,
        values: pgkitDumpState(),
      });
      pgkitRemoteSend({
        type:   "schema",
        fields: pgkitDumpSchema(),
      });
      const dump = pgkitDumpEntities();
      _pgkitLastEntitiesSig = JSON.stringify(dump);
      pgkitRemoteSend({
        type:   "entities",
        levels: dump.levels,
        order:  dump.order,
      });
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
      try { result = entry.run(msg.payload || {}); }
      catch (e) { ok = false; error = String(e); }
    } else if (kind === "select") {
      const payload = msg.payload || {};
      if (typeof pgkitHandleSelect === "function") {
        const selOk = pgkitHandleSelect(payload.level, payload.name);
        if (!selOk) { ok = false; error = "select failed"; }
        else { pgkitNotifyEntities(); }
      } else {
        ok = false;
        error = "no select handler";
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
