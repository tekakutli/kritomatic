"""
rp_client.py — the remote panel's WebSocket client.

BROWSER-SIDE, connecting TO a playground.  This is distinct from
pgkit's remote_client, which runs INSIDE a playground and connects
to that playground's own server.  Here, the page that runs this
code is itself the remote client.

STATE
=====
The client holds three maps:

    _rp.values    {name: current_value}      from the last `state`
    _rp.schema    {name: meta}               from the last `schema`
    _rp.widgets   {name: DOM entry}          built by rp_ui.js

The schema message is optional (older playgrounds don't send it);
when absent, rp_ui infers a widget type from the value's JS type.

MESSAGES SENT
=============
    hello
    state_request          on connect; debounced after edits
    patch                  on every widget change
    command                from the Commands section

MESSAGES RECEIVED
=================
    hello_ack              {clientId}
    presence               {browser, remote}
    state                  {values}
    schema                 {fields}
    patch                  {values}   (from the browser's local user,
                                       not our own — see note below)
    command_result         {id, ok, error}

ECHO BEHAVIOUR
==============
The playground suppresses re-broadcast during a remote patch's
application, so the meta-panel does not receive a `patch` echo of
its own writes.  What it does receive is `patch` messages triggered
by the playground's local user.  Those are applied to `_rp.values`
and to any widget not currently focused.

STATE REFRESH
=============
After sending a patch, the client schedules a `state_request` 500ms
later.  That lets the panel discover whether the playground actually
accepted the value (some fields require a selected object and reject
the write silently).  On a scrub-like edit with many writes, the
debounce collapses them into one confirmation at the end.
"""

RP_CLIENT_JS = r"""
/* ==========================================================================
   REMOTE PANEL — STATE
   ========================================================================== */

const _rp = {
  socket:          null,
  clientId:        null,
  connected:       false,
  everConnected:   false,
  target:          "",
  seq:             0,
  values:          {},
  schema:          {},
  widgets:         {},
  presence:        null,
  reconnectTimer:  null,
  reconnectMs:     1000,
  stateTimer:      null,
  rebuildTimer:    null,
  log:             [],
};

const RP_PROTOCOL_VERSION = 1;
const RP_MAX_LOG           = 200;
const RP_STATE_DEBOUNCE_MS = 500;
const RP_REBUILD_DEBOUNCE_MS = 50;

/* ==========================================================================
   LOG
   ========================================================================== */

function rpLog(kind, text) {
  const ts = new Date().toLocaleTimeString();
  _rp.log.push({ ts, kind, text });
  if (_rp.log.length > RP_MAX_LOG) _rp.log.shift();
  rpRenderLog();
}

/* ==========================================================================
   CONNECT / DISCONNECT
   ========================================================================== */

function rpConnect(target) {
  rpDisconnect();
  _rp.target        = target;
  _rp.everConnected = false;
  try { localStorage.setItem("rp.target", target); } catch (e) {}

  let sock;
  try { sock = new WebSocket(target); }
  catch (e) {
    rpLog("error", "invalid target: " + e.message);
    rpSetStatus("disconnected", false);
    return;
  }
  _rp.socket = sock;

  sock.addEventListener("open", () => {
    _rp.connected     = true;
    _rp.everConnected = true;
    _rp.reconnectMs   = 1000;
    rpSetStatus("connecting", false);
    rpSend({
      type:     "hello",
      role:     "remote",
      protocol: RP_PROTOCOL_VERSION,
    });
    rpLog("info", "connected to " + target);
  });

  sock.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    rpHandle(msg);
  });

  sock.addEventListener("close", () => {
    _rp.connected = false;
    _rp.clientId  = null;
    rpSetStatus("disconnected", false);
    rpLog("info", "disconnected");
    rpScheduleReconnect();
  });

  sock.addEventListener("error", () => {});
}

function rpDisconnect() {
  if (_rp.reconnectTimer) {
    clearTimeout(_rp.reconnectTimer);
    _rp.reconnectTimer = null;
  }
  if (_rp.stateTimer) {
    clearTimeout(_rp.stateTimer);
    _rp.stateTimer = null;
  }
  if (_rp.socket) {
    try { _rp.socket.close(); } catch (e) {}
    _rp.socket = null;
  }
  _rp.connected = false;
  _rp.clientId  = null;
}

function rpScheduleReconnect() {
  /* Reconnect only if we were previously connected — a network blip
     recovers, but a failed first attempt stays failed until the user
     acts. */
  if (!_rp.everConnected) return;
  if (!_rp.target) return;
  if (_rp.reconnectTimer) return;
  _rp.reconnectTimer = setTimeout(() => {
    _rp.reconnectTimer = null;
    if (!_rp.connected && _rp.target) {
      rpLog("info", "reconnecting to " + _rp.target + "…");
      rpConnect(_rp.target);
    }
  }, _rp.reconnectMs);
  _rp.reconnectMs = Math.min(_rp.reconnectMs * 2, 5000);
}

/* ==========================================================================
   WIRE
   ========================================================================== */

function rpSend(msg) {
  const s = _rp.socket;
  if (!s || s.readyState !== WebSocket.OPEN) return;
  s.send(JSON.stringify(msg));
}

function rpRequestState() {
  rpSend({ type: "state_request" });
}

function rpScheduleStateRefresh() {
  if (_rp.stateTimer) clearTimeout(_rp.stateTimer);
  _rp.stateTimer = setTimeout(() => {
    _rp.stateTimer = null;
    rpRequestState();
  }, RP_STATE_DEBOUNCE_MS);
}

function rpSendPatch(name, value) {
  _rp.values[name] = value;
  _rp.seq++;
  rpSend({
    type:   "patch",
    origin: _rp.clientId || "remote-panel",
    seq:    _rp.seq,
    values: { [name]: value },
  });
  rpScheduleStateRefresh();
}

function rpSendCommand(kind, payload) {
  const id = "rp-" + Math.random().toString(36).slice(2, 10);
  rpSend({ type: "command", id, kind, payload: payload || {} });
  return id;
}

/* ==========================================================================
   MESSAGE HANDLING
   ========================================================================== */

function rpHandle(msg) {
  switch (msg.type) {
    case "hello_ack":
      _rp.clientId = msg.clientId;
      rpLog("info", "client id " + msg.clientId);
      rpRequestState();
      break;

    case "presence":
      _rp.presence = {
        browser: msg.browser || 0,
        remote:  msg.remote  || 0,
      };
      rpSetStatus(
        _rp.presence.browser > 0 ? "connected" : "no-browser",
        _rp.presence.browser > 0
      );
      rpRenderPresence();
      rpLog("info", "presence: browser=" + _rp.presence.browser
                    + " remote=" + _rp.presence.remote);
      break;

    case "state":
      rpOnState(msg.values || {});
      break;

    case "schema":
      rpOnSchema(msg.fields || {});
      break;

    case "patch": {
      const vals = msg.values || {};
      for (const k in vals) {
        _rp.values[k] = vals[k];
        rpUpdateWidget(k);
      }
      rpLog("patch", Object.keys(vals).join(", "));
      break;
    }

    case "command_result":
      rpLog("cmd", (msg.ok ? "ok" : "FAIL") + "  " + (msg.id || ""));
      if (!msg.ok && msg.error) rpLog("error", msg.error);
      break;

    default:
      rpLog("info", "unhandled message type: " + msg.type);
  }
}

function rpOnState(newValues) {
  const newNames = Object.keys(newValues).sort();
  const oldNames = Object.keys(_rp.values).sort();
  const changed  = !_rpNamesEqual(newNames, oldNames);
  _rp.values = newValues;
  rpLog("state", newNames.length + " field(s)");
  if (changed) rpScheduleRebuild();
  else         for (const n of newNames) rpUpdateWidget(n);
}

function rpOnSchema(newSchema) {
  const newNames = Object.keys(newSchema).sort();
  const oldNames = Object.keys(_rp.schema).sort();
  const changed  = !_rpNamesEqual(newNames, oldNames);
  _rp.schema = newSchema;
  rpLog("schema", newNames.length + " entr" + (newNames.length === 1 ? "y" : "ies"));
  if (changed) rpScheduleRebuild();
}

function _rpNamesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function rpScheduleRebuild() {
  if (_rp.rebuildTimer) clearTimeout(_rp.rebuildTimer);
  _rp.rebuildTimer = setTimeout(() => {
    _rp.rebuildTimer = null;
    rpRebuildPanel();
  }, RP_REBUILD_DEBOUNCE_MS);
}

/* ==========================================================================
   FIELD WIDGETS
   ========================================================================== */

function rpBuildFieldRow(name) {
  const meta  = _rp.schema[name] || {};
  const value = _rp.values[name];
  const short = name.indexOf(".") >= 0
    ? name.slice(name.indexOf(".") + 1)
    : name;
  const label = meta.label || short;

  const row = document.createElement("div");
  row.className = "field";

  const lbl = document.createElement("label");
  lbl.textContent = label;
  lbl.title = name;
  row.appendChild(lbl);

  let kind = meta.kind;
  if (!kind) kind = _rpInferKind(value);

  let widget;

  if (kind === "bool") {
    widget = document.createElement("input");
    widget.type = "checkbox";
    widget.checked = !!value;
    widget.addEventListener("change", () => {
      rpSendPatch(name, !!widget.checked);
    });
  } else if (kind === "enum" && Array.isArray(meta.options)) {
    widget = document.createElement("select");
    for (const opt of meta.options) {
      const o = document.createElement("option");
      o.value = String(opt);
      o.textContent = String(opt);
      widget.appendChild(o);
    }
    widget.value = String(value == null ? "" : value);
    widget.addEventListener("change", () => {
      rpSendPatch(name, widget.value);
    });
  } else if (kind === "number") {
    widget = document.createElement("input");
    widget.type = "text";
    widget.value = String(value == null ? "" : value);
    widget.autocomplete = "off";
    widget.spellcheck = false;
    if (typeof meta.min === "number") widget.dataset.min = String(meta.min);
    if (typeof meta.max === "number") widget.dataset.max = String(meta.max);
    const commit = () => {
      let v = parseFloat(widget.value);
      if (!isFinite(v)) {
        widget.value = String(_rp.values[name] == null ? "" : _rp.values[name]);
        return;
      }
      const lo = widget.dataset.min !== undefined
        ? parseFloat(widget.dataset.min) : null;
      const hi = widget.dataset.max !== undefined
        ? parseFloat(widget.dataset.max) : null;
      if (lo !== null && v < lo) v = lo;
      if (hi !== null && v > hi) v = hi;
      widget.value = String(v);
      rpSendPatch(name, v);
    };
    widget.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { commit(); widget.blur(); }
      else if (e.key === "Escape") {
        widget.value = String(_rp.values[name] == null ? "" : _rp.values[name]);
        widget.blur();
      }
    });
    widget.addEventListener("blur", commit);
  } else {
    /* String / unknown.  Free-form text input. */
    widget = document.createElement("input");
    widget.type = "text";
    widget.value = String(value == null ? "" : value);
    widget.autocomplete = "off";
    widget.spellcheck = false;
    const commit = () => {
      const v = widget.value;
      const cur = _rp.values[name];
      if (v !== String(cur == null ? "" : cur)) rpSendPatch(name, v);
    };
    widget.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { commit(); widget.blur(); }
      else if (e.key === "Escape") {
        widget.value = String(_rp.values[name] == null ? "" : _rp.values[name]);
        widget.blur();
      }
    });
    widget.addEventListener("blur", commit);
  }

  widget.dataset.rpField = name;
  row.appendChild(widget);
  _rp.widgets[name] = { row, widget, kind };
  return row;
}

function rpUpdateWidget(name) {
  const entry = _rp.widgets[name];
  if (!entry) return;
  const { widget, kind } = entry;
  if (document.activeElement === widget) return;
  const value = _rp.values[name];
  if (kind === "bool") {
    widget.checked = !!value;
  } else if (widget.tagName === "SELECT") {
    widget.value = String(value == null ? "" : value);
  } else {
    widget.value = String(value == null ? "" : value);
  }
}

function _rpInferKind(value) {
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number")  return "number";
  return "string";
}

/* ==========================================================================
   STATUS
   ========================================================================== */

function rpSetStatus(state, hasBrowser) {
  const el = document.getElementById("rpStatus");
  if (!el) return;
  let text, cls;
  if (state === "connecting")    { text = "connecting\u2026";        cls = ""; }
  else if (state === "connected") { text = "connected";                cls = "ok"; }
  else if (state === "no-browser"){ text = "connected (no browser)";   cls = "warn"; }
  else                            { text = "disconnected";             cls = "bad"; }
  el.textContent = text;
  el.className   = cls;

  const connBtn = document.getElementById("rpConnectBtn");
  const discBtn = document.getElementById("rpDisconnectBtn");
  const dis     = (state === "disconnected");
  if (connBtn) connBtn.disabled = !dis;
  if (discBtn) discBtn.disabled = dis;
}

function rpRenderPresence() {
  const el = document.getElementById("rpPresence");
  if (!el) return;
  if (!_rp.presence) { el.textContent = ""; return; }
  el.textContent = "browser=" + _rp.presence.browser
                 + "  remote=" + _rp.presence.remote;
}
"""
