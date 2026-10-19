"""
pgkit.remote_client — the browser's WebSocket client.

On page load it opens a WebSocket to the playground's server.  Every
value field registered via `pgkitRegisterField`, every action
registered via `pgkitRegisterAction`, and every level registered via
`pgkitRegisterLevel` is published under its dotted name and can be
driven from any other connected client.

STATE PUBLICATION — NOTIFICATION, NOT POLLING
=============================================
The project tells the kit when a value changed:

    pgkitNotifyLocal(name)    queue one field's current value
    pgkitNotifyAll()          queue every registered field's current
                              value
    pgkitNotifyEntities()     queue the entity tree (levels, items,
                              selection, applies)

There is no timer, no polling.  Actions are not included in state.

CHANGE DEDUPLICATION — FLUSH TIME
=================================
`_pgkitLastSent` holds, per field, the last value the wire actually
carried.  It is updated in exactly three places:

    _pgkitFlush              every value we just sent
    pgkitRemoteApplyPatch    every value a peer just told us they
                             believe we hold
    state_request handler    every value in the state dump — the hub
                             broadcasts that dump to all peers, so
                             after it every peer holds these values

`_pgkitFlush` compares each queued value against `_pgkitLastSent`
and skips matches.

The *notify* functions do NOT consult `_pgkitLastSent`.  An earlier
revision did, and it was a bug: a notify A→B→A sequence that fires
inside one animation frame would leave a stale B in
`_pgkitNotifyPending` that the notify-time check could not see (it
compared against `_pgkitLastSent`, not against the pending queue).
The stale value then went on the wire on the next flush even
though the current state was A.  Moving the check to flush time
fixes this with no extra state — the pending queue is drained
completely, and only its post-dedup contents reach the wire.

`pgkitRemoteApplyPatch` records `_pgkitLastSent[name]` *before* the
equality check — the incoming value is what the peers believe we
hold, whether or not it differs from our current value.  That way
a skipped `set` still advances the dedup state.

On socket close, `_pgkitLastSent` is cleared so the next connect's
state_request handler performs a full refresh of the map.

SELECT COMMAND — FIELDS REFRESH TOO
===================================
A `select` command changes `board.selectedIdx` / `selectedPair` /
`selectedQuad`, which in turn changes the value of every
selection-scoped getter on the kit (`item.*` in text_board,
`pair.*` in pair, `card.*` in board).  `select` therefore calls
`pgkitNotifyAll()` — not `pgkitNotifyEntities()` — so the remote
panel's field widgets update as well.  `pgkitNotifyAll` sets
`_pgkitEntitiesPending` itself, so entities still refresh; the
dedup at flush time means only fields whose values actually
changed reach the wire.

SCHEMA PUBLICATION — FIELDS, ACTIONS, LEVELS
============================================
On a `state_request` the kit answers with `state`, `schema`, and
`entities`:

    state       {seq, values} — current value of every field
    schema      {fields} — metadata: kind, label, options, plus
                action meta (scope, level, requires, variant) and
                level meta (label)
    entities    {levels, order} — per-level items, selection, and
                per-item `applies` maps for row-scope actions

LEVELS
======
A level is one kind of entity in the model.  `pgkitRegisterLevel`
takes {label, items, selected, parent}.  `items()` returns
[{name, label, extra?}]; `selected()` returns the currently-
selected item's name or null; `parent()` returns {level, name} for
nested levels or null.  `pgkitDumpEntities` walks every level and
populates `applies` for each row-scope action by running its
`appliesTo(item)` predicate once per item.

ACTION PAYLOADS
===============
An action may declare `requires` in its meta: an array of
`{name, kind, label, default?, accept?}` entries describing what
the user must supply.  `kind` is `"text"` or `"file"`.  The kit
itself does not gather those values — a remote client does, and
sends them in the `command` message's `payload`.

ECHO SUPPRESSION
================
Each patch carries a monotonic `seq` and the sender's `origin`
client id.  The receiving side ignores any patch whose `origin`
matches its own id.  `_pgkitApplyingRemote` is a second guard,
active only during `pgkitRemoteApplyPatch`.

PANEL VISIBILITY
================
Presence-driven.  When a remote client is connected, the panel is
hidden AND the local restore button is hidden.  Without this,
the restore button (shown when the local user collapsed the
panel) and the "Controlled remotely" banner both sit at
`top:12px; left:12px` and overlap.
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

/* Field name → last value the wire carried.  Populated by
   _pgkitFlush, pgkitRemoteApplyPatch, and the state_request handler;
   cleared on socket close.  See the module docstring,
   CHANGE DEDUPLICATION — FLUSH TIME. */
let _pgkitLastSent = {};

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
   REGISTRY — ACTIONS
   ========================================================================== */

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
   REGISTRY — LEVELS
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
   ==========================================================================
   The notify functions always queue.  Deduplication happens at
   flush time, against `_pgkitLastSent`. */

function pgkitNotifyLocal(name) {
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

function pgkitNotifyAll() {
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
    pgkitRemoteSend({
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
      pgkitRemoteSend({
        type:   "state",
        seq:    _pgkitRemote.seq,
        values: values,
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
        else { pgkitNotifyAll(); }
      } else {
        ok = false;
        error = "no select handler";
      }
    } else if (kind === "reset_view" && typeof resetView === "function") {
      resetView();
    } else if (kind === "deselect") {
      if (typeof selectedPair    !== "undefined") selectedPair = -1;
      if (typeof selectedSegment !== "undefined") selectedSegment = null;
      if (typeof selectedQuad    !== "undefined") selectedQuad = -1;
      if (typeof selectedSquare  !== "undefined") selectedSquare = -1;
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
   ==========================================================================
   Two elements compete for `top:12px; left:12px`: #restoreBtn (shown
   when the local user collapses the panel) and #remoteBanner (shown
   when a remote client is connected).  Without coordination both can
   be visible at once — the restore button stays visible from the
   user's earlier collapse, the banner appears when the remote
   connects, and they overlap.

   Resolution: presence wins.  When a remote client is connected the
   panel is hidden (as before) and #restoreBtn is also hidden — the
   local user should not be re-opening the panel while a remote is
   driving.  When the remote disconnects, the panel comes back and
   the restore button stays hidden (it is only needed when the panel
   is collapsed). */

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
