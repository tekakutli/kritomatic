"""
rp_client.py — the remote panel's WebSocket client.

The panel has two WS channels to the hub:

  Control   role="panel", the anonymous listener socket.  Receives
            `playgrounds` broadcasts and `launcher_result`; sends
            `kill`, `shutdown_hub`, `launcher_start`, `launcher_stop`,
            `launcher_restart`.

  Playground  a direct WS to the playground's /remote endpoint (via
            the hub's proxy).  Drives a selected playground's values
            and actions.  Unchanged by this file's extension.

ENTITY AND TARGET FILTERS
=========================
`rpPopulateTargets` builds the target dropdown from
`_rp.playgrounds.filter(role === "playground")`.  Launcher and panel
entries in the same array are filtered out automatically.

`rpRebuildManagementList` uses the same array to render two sections:
the running-playgrounds list (role="playground") and the "Available
to start" list (derived from each launcher's `available` field).

LAUNCHER COMMANDS
=================
`rpLauncherStart(name)` and `rpLauncherStop(name)` send
`launcher_start` / `launcher_stop` over the control WS.  The hub
forwards to the launcher, which replies with `launcher_result`; the
reply is handled in `_rpHandleHubMessage` and surfaced via
`flashStatus`.

GROUPED PLAYGROUNDS
===================
A registration may carry a `group` string.  Groups are metadata, not
routing; the target dropdown still lists each playground
individually.  The visible consequence: the Kill/Force buttons'
`title` and the Force confirm dialog mention the group when the
field is non-None, and the Stop button on a grouped playground is
offered as the "stop only this one" alternative to Kill (which shuts
down the group).

HUB SHUTDOWN FLOW
=================
Unchanged.  `rpShutdownHub()` promises a `shutdown_ack`; see the
previous version's docstring for the full lifecycle.
"""

RP_CLIENT_JS = r"""
/* ==========================================================================
   REMOTE PANEL — STATE
   ========================================================================== */

const _rp = {
  socket:          null,
  actionsCollapsedByUser: false,
  clientId:        null,
  connected:       false,
  everConnected:   false,
  target:          "",
  seq:             0,
  values:          {},
  schema:          {},
  entities:        null,
  entityOrder:     [],
  fields:          {},
  actions:         {},
  presence:        null,
  reconnectTimer:  null,
  reconnectMs:     1000,
  stateTimer:      null,
  rebuildTimer:    null,
  log:             [],
  _pendingAction:  null,

  hubSocket:         null,
  hubUrl:            null,
  hubConnected:      false,
  playgrounds:       [],
  hubReconnectMs:    1000,
  hubReconnectTimer: null,

  shuttingDown:      false,
  shutdownTimeoutId: null,
  shutdownResolve:   null,
};

const RP_PROTOCOL_VERSION = 1;
const RP_MAX_LOG           = 300;
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
   CONNECT / DISCONNECT  (playground WS — the control channel)
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
    rpSetStatus("disconnected");
    return;
  }
  _rp.socket = sock;

  sock.addEventListener("open", () => {
    _rp.connected     = true;
    _rp.everConnected = true;
    _rp.reconnectMs   = 1000;
    rpSetStatus("connecting");
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
    rpSetStatus("disconnected");
    rpLog("info", "disconnected");
    if (!_rp.shuttingDown) rpScheduleReconnect();
  });

  sock.addEventListener("error", () => {});
}

function rpDisconnect() {
  if (_rp.reconnectTimer) { clearTimeout(_rp.reconnectTimer); _rp.reconnectTimer = null; }
  if (_rp.stateTimer)     { clearTimeout(_rp.stateTimer);     _rp.stateTimer     = null; }
  if (_rp.socket) { try { _rp.socket.close(); } catch (e) {} _rp.socket = null; }
  _rp.connected = false;
  _rp.clientId  = null;
}

function rpScheduleReconnect() {
  if (_rp.shuttingDown)   return;
  if (!_rp.everConnected) return;
  if (!_rp.target)        return;
  if (_rp.reconnectTimer) return;
  _rp.reconnectTimer = setTimeout(() => {
    _rp.reconnectTimer = null;
    if (!_rp.connected && _rp.target && !_rp.shuttingDown) {
      rpLog("info", "reconnecting to " + _rp.target + "\u2026");
      rpConnect(_rp.target);
    }
  }, _rp.reconnectMs);
  _rp.reconnectMs = Math.min(_rp.reconnectMs * 2, 5000);
}

/* ==========================================================================
   WIRE  (playground channel)
   ========================================================================== */

function rpSend(msg) {
  const s = _rp.socket;
  if (!s || s.readyState !== WebSocket.OPEN) return;
  s.send(JSON.stringify(msg));
}

function rpRequestState() { rpSend({ type: "state_request" }); }

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
   MESSAGE HANDLING  (playground channel)
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
      rpSetStatus(_rp.presence.browser > 0 ? "connected" : "no-browser");
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

    case "entities":
      rpOnEntities(msg.levels || {}, msg.order || []);
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
  rpLog("schema", newNames.length + " entr"
                  + (newNames.length === 1 ? "y" : "ies"));
  if (changed) rpScheduleRebuild();
}

function rpOnEntities(levels, order) {
  _rp.entities    = levels;
  _rp.entityOrder = order;
  rpLog("entities", order.length + " level(s)");
  rpScheduleRebuild();
}

function _rpNamesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
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
   SECTION HELPERS
   ========================================================================== */

function _rpSectionForField(name, meta) {
  if (meta && meta.section) return meta.section;
  const dot = name.indexOf(".");
  return dot >= 0 ? name.slice(0, dot) : "general";
}

function _rpSectionLabel(section) {
  if (!section) return "General";
  return section.charAt(0).toUpperCase() + section.slice(1);
}

/* ==========================================================================
   FIELD WIDGETS
   ========================================================================== */

function rpBuildFieldRow(name) {
  const meta  = _rp.schema[name] || {};
  if (meta.visible === false) return null;

  const value = _rp.values[name];
  const short = name.indexOf(".") >= 0
    ? name.slice(name.indexOf(".") + 1)
    : name;
  const label = meta.label || short;

  const row = document.createElement("div");
  row.className = "field-row";

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
    widget.addEventListener("change", () => rpSendPatch(name, !!widget.checked));
  } else if (kind === "enum" && Array.isArray(meta.options)) {
    widget = document.createElement("select");
    for (const opt of meta.options) {
      const o = document.createElement("option");
      o.value = String(opt);
      o.textContent = String(opt);
      widget.appendChild(o);
    }
    widget.value = String(value == null ? "" : value);
    widget.addEventListener("change", () => rpSendPatch(name, widget.value));
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
      if (!isFinite(v)) { widget.value = String(_rp.values[name] == null ? "" : _rp.values[name]); return; }
      const lo = widget.dataset.min !== undefined ? parseFloat(widget.dataset.min) : null;
      const hi = widget.dataset.max !== undefined ? parseFloat(widget.dataset.max) : null;
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
  _rp.fields[name] = { row, widget, kind };
  return row;
}

function rpUpdateWidget(name) {
  const entry = _rp.fields[name];
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
   ACTION BUTTONS
   ========================================================================== */

function rpBuildActionButton(name, disabled) {
  const meta  = _rp.schema[name] || {};
  const label = meta.label || name;
  const btn   = document.createElement("button");
  btn.type = "button";
  btn.className = "action-btn";
  if (meta.variant === "primary") btn.classList.add("primary");
  else if (meta.variant === "danger") btn.classList.add("danger");
  btn.textContent = label;
  btn.title = name;
  if (disabled) btn.disabled = true;
  btn.addEventListener("click", () => rpActionClick(name, null));
  _rp.actions[name] = { btn, meta };
  return btn;
}

function rpBuildRowActionButton(name, target) {
  const meta  = _rp.schema[name] || {};
  const label = meta.label || name;
  const btn   = document.createElement("button");
  btn.type = "button";
  btn.className = "action-btn small";
  if (meta.variant === "primary") btn.classList.add("primary");
  else if (meta.variant === "danger") btn.classList.add("danger");
  btn.textContent = label;
  btn.title = name;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    rpActionClick(name, target);
  });
  return btn;
}

/* ==========================================================================
   ACTION INVOCATION
   ========================================================================== */

function rpActionClick(name, target) {
  const meta = _rp.schema[name] || {};
  const requires = Array.isArray(meta.requires) ? meta.requires : [];
  const initialPayload = target ? { target } : {};

  if (requires.length === 0) {
    const id = rpSendCommand(name, initialPayload);
    rpLog("cmd", "\u25B6 " + (meta.label || name) + "  (" + id + ")");
    return;
  }
  rpShowActionForm(name, requires, initialPayload);
}

function rpShowActionForm(name, requires, initialPayload) {
  const meta     = _rp.schema[name] || {};
  const backdrop = document.getElementById("rpModalBackdrop");
  const title    = document.getElementById("rpModalTitle");
  const body     = document.getElementById("rpModalBody");
  if (!backdrop || !title || !body) return;

  title.textContent = meta.label || name;
  body.innerHTML = "";

  const values = {};

  for (const req of requires) {
    const row = document.createElement("div");
    row.className = "rp-modal-field";

    const lbl = document.createElement("label");
    lbl.textContent = req.label || req.name;
    row.appendChild(lbl);

    if (req.kind === "file") {
      const wrap = document.createElement("div");
      wrap.className = "rp-modal-file-wrap";

      const fileBtn = document.createElement("button");
      fileBtn.type = "button";
      fileBtn.className = "rp-modal-file-btn";
      fileBtn.textContent = "Choose file\u2026";

      const status = document.createElement("span");
      status.className = "rp-modal-file-status";
      status.textContent = "(none)";

      const hidden = document.createElement("input");
      hidden.type = "file";
      hidden.style.display = "none";
      if (req.accept) hidden.accept = req.accept;

      hidden.addEventListener("change", () => {
        const f = hidden.files && hidden.files[0];
        if (!f) return;
        status.textContent = f.name;
        const reader = new FileReader();
        reader.onload = () => {
          values[req.name] = String(reader.result || "");
          fileBtn.classList.add("chosen");
          rpValidateModal();
        };
        reader.onerror = () => {
          status.textContent = "(read failed)";
          delete values[req.name];
          rpValidateModal();
        };
        reader.readAsText(f);
      });
      fileBtn.addEventListener("click", () => hidden.click());

      wrap.appendChild(fileBtn);
      wrap.appendChild(status);
      wrap.appendChild(hidden);
      row.appendChild(wrap);
    } else {
      const input = document.createElement("input");
      input.type = "text";
      input.value = req.default != null ? String(req.default) : "";
      input.autocomplete = "off";
      input.spellcheck = false;
      input.addEventListener("input", () => {
        values[req.name] = input.value;
        rpValidateModal();
      });
      values[req.name] = input.value;
      row.appendChild(input);
    }

    body.appendChild(row);
  }

  _rp._pendingAction = {
    name, requires, values,
    initialPayload: initialPayload || {},
  };
  backdrop.hidden = false;
  rpValidateModal();
  const firstInput = body.querySelector("input[type=text]");
  if (firstInput) firstInput.focus();
}

function rpValidateModal() {
  const sendBtn = document.getElementById("rpModalSend");
  if (!sendBtn) return;
  if (!_rp._pendingAction) { sendBtn.disabled = true; return; }
  const { requires, values } = _rp._pendingAction;
  let ok = true;
  for (const req of requires) {
    if (req.kind === "file") {
      if (typeof values[req.name] !== "string") { ok = false; break; }
    } else {
      if (typeof values[req.name] !== "string"
          || values[req.name].trim() === "") {
        ok = false; break;
      }
    }
  }
  sendBtn.disabled = !ok;
}

function rpCommitActionForm() {
  if (!_rp._pendingAction) return;
  const { name, values, initialPayload } = _rp._pendingAction;
  const meta = _rp.schema[name] || {};
  const payload = Object.assign({}, initialPayload || {}, values);
  _rp._pendingAction = null;
  const backdrop = document.getElementById("rpModalBackdrop");
  if (backdrop) backdrop.hidden = true;
  const id = rpSendCommand(name, payload);
  rpLog("cmd", "\u25B6 " + (meta.label || name) + "  (" + id + ")");
}

function rpCancelActionForm() {
  _rp._pendingAction = null;
  const backdrop = document.getElementById("rpModalBackdrop");
  if (backdrop) backdrop.hidden = true;
}

/* ==========================================================================
   SELECT
   ========================================================================== */

function rpSelectItem(level, name) {
  if (!level || !name) return;
  rpSendCommand("select", { level, name });
  rpLog("info", "select " + level + "." + name);
}

/* ==========================================================================
   STATUS
   ========================================================================== */

function rpSetStatus(state) {
  const el = document.getElementById("rpStatus");
  if (!el) return;
  let text, cls;
  if (state === "connecting")     { text = "connecting\u2026";       cls = ""; }
  else if (state === "connected") { text = "connected";              cls = "ok"; }
  else if (state === "no-browser"){ text = "connected (no browser)"; cls = "warn"; }
  else                             { text = "disconnected";          cls = "bad"; }
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

/* ==========================================================================
   HUB MANAGEMENT
   ========================================================================== */

function rpDiscoverHub() {
  try {
    const u = new URL("/__hub_ws", document.baseURI);
    u.protocol = (u.protocol === "https:") ? "wss:" : "ws:";
    _rp.hubUrl = u.toString();
  } catch (e) {
    _rp.hubUrl = null;
    return;
  }
  _rpConnectHub();
}

function _rpConnectHub() {
  if (_rp.hubReconnectTimer) { clearTimeout(_rp.hubReconnectTimer); _rp.hubReconnectTimer = null; }
  if (_rp.hubSocket) { try { _rp.hubSocket.close(); } catch (e) {} _rp.hubSocket = null; }
  if (!_rp.hubUrl) return;
  if (_rp.shuttingDown) return;

  let sock;
  try { sock = new WebSocket(_rp.hubUrl); }
  catch (e) { _rpScheduleHubReconnect(); return; }
  _rp.hubSocket = sock;

  sock.addEventListener("open", () => {
    _rp.hubConnected = true;
    _rp.hubReconnectMs = 1000;
    try { sock.send(JSON.stringify({ type: "hello", role: "panel" })); } catch (e) {}
    rpLog("info", "hub management connected");
  });

  sock.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    _rpHandleHubMessage(msg);
  });

  sock.addEventListener("close", () => {
    _rp.hubConnected = false;
    _rp.playgrounds = [];
    rpLog("info", "hub management disconnected");
    rpPopulateTargets();
    if (typeof rpRebuildManagementList === "function") {
      rpRebuildManagementList();
    }
    if (!_rp.shuttingDown) _rpScheduleHubReconnect();
  });

  sock.addEventListener("error", () => {});
}

function _rpScheduleHubReconnect() {
  if (_rp.shuttingDown) return;
  if (_rp.hubReconnectTimer) return;
  _rp.hubReconnectTimer = setTimeout(() => {
    _rp.hubReconnectTimer = null;
    _rpConnectHub();
  }, _rp.hubReconnectMs);
  _rp.hubReconnectMs = Math.min(_rp.hubReconnectMs * 2, 10000);
}

function _rpHandleHubMessage(msg) {
  if (msg.type === "playgrounds") {
    _rp.playgrounds = msg.playgrounds || [];
    rpLog("info", "hub: " + _rp.playgrounds.length + " registration(s)");
    rpPopulateTargets();
    if (typeof rpRebuildManagementList === "function") {
      rpRebuildManagementList();
    }
    return;
  }
  if (msg.type === "kill_result") {
    rpLog(msg.success ? "info" : "error",
          "kill " + msg.name + ": " + (msg.message || ""));
    if (typeof flashStatus === "function") {
      flashStatus(
        "kill " + msg.name + ": " + (msg.success ? "ok" : (msg.message || "failed")),
        msg.success ? "ok" : "bad"
      );
    }
    return;
  }
  if (msg.type === "launcher_result") {
    rpLog(msg.success ? "info" : "error",
          "launcher " + (msg.name || "?") + ": " + (msg.message || ""));
    if (typeof flashStatus === "function") {
      flashStatus(
        "launcher " + (msg.name || "?") + ": "
          + (msg.message || (msg.success ? "ok" : "failed")),
        msg.success ? "ok" : "bad"
      );
    }
    return;
  }
  if (msg.type === "shutdown_ack") {
    rpLog("info", "hub shutdown acknowledged");
    if (_rp.shutdownResolve) _rp.shutdownResolve({ ok: true });
    if (_rp.socket) { try { _rp.socket.close(); } catch (e) {} _rp.socket = null; }
    _rp.connected     = false;
    _rp.clientId      = null;
    _rp.hubConnected  = false;
    _rp.playgrounds   = [];
    rpSetStatus("disconnected");
    return;
  }
  if (msg.type === "shutdown") {
    rpLog("info", "hub is shutting down (received shutdown notice)");
    _rp.shuttingDown = true;
    if (_rp.shutdownResolve) _rp.shutdownResolve({ ok: true });
    if (_rp.socket) { try { _rp.socket.close(); } catch (e) {} _rp.socket = null; }
    _rp.connected     = false;
    _rp.clientId      = null;
    _rp.hubConnected  = false;
    _rp.playgrounds   = [];
    rpSetStatus("disconnected");
    return;
  }
}

/* ==========================================================================
   LAUNCHER COMMANDS
   ========================================================================== */

function rpLauncherStart(name) {
  if (!_rp.hubSocket || !_rp.hubConnected) {
    if (typeof flashStatus === "function") flashStatus("hub not connected", "warn");
    return;
  }
  const request_id = "lreq-" + Math.random().toString(36).slice(2, 10);
  try {
    _rp.hubSocket.send(JSON.stringify({
      type: "launcher_start", name, request_id,
    }));
    if (typeof flashStatus === "function") flashStatus("starting " + name + "\u2026", "warn");
  } catch (e) {
    if (typeof flashStatus === "function") flashStatus("send failed: " + e.message, "bad");
  }
}

function rpLauncherStop(name) {
  if (!_rp.hubSocket || !_rp.hubConnected) {
    if (typeof flashStatus === "function") flashStatus("hub not connected", "warn");
    return;
  }
  const request_id = "lreq-" + Math.random().toString(36).slice(2, 10);
  try {
    _rp.hubSocket.send(JSON.stringify({
      type: "launcher_stop", name, request_id,
    }));
    if (typeof flashStatus === "function") flashStatus("stopping " + name + "\u2026", "warn");
  } catch (e) {
    if (typeof flashStatus === "function") flashStatus("send failed: " + e.message, "bad");
  }
}

/* ==========================================================================
   HUB SHUTDOWN
   ========================================================================== */

function rpShutdownHub() {
  return new Promise((resolve) => {
    if (!_rp.hubSocket || !_rp.hubConnected) {
      resolve({ ok: false, error: "hub not connected" });
      return;
    }
    if (_rp.shuttingDown) {
      resolve({ ok: false, error: "already shutting down" });
      return;
    }
    if (_rp.shutdownTimeoutId) {
      clearTimeout(_rp.shutdownTimeoutId);
      _rp.shutdownTimeoutId = null;
    }
    _rp.shuttingDown = true;

    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      if (_rp.shutdownTimeoutId) {
        clearTimeout(_rp.shutdownTimeoutId);
        _rp.shutdownTimeoutId = null;
      }
      if (!result.ok) _rp.shuttingDown = false;
      _rp.shutdownResolve = null;
      resolve(result);
    };

    _rp.shutdownResolve = finish;

    _rp.shutdownTimeoutId = setTimeout(() => {
      finish({ ok: false, error: "no acknowledgment from hub within 5s" });
    }, 5000);

    try {
      _rp.hubSocket.send(JSON.stringify({ type: "shutdown_hub" }));
      rpLog("info", "sent shutdown_hub; waiting for ack\u2026");
    } catch (e) {
      finish({ ok: false, error: "send failed: " + e.message });
    }
  });
}

function rpKillPlayground(name, hard) {
  if (!_rp.hubSocket || !_rp.hubConnected) {
    rpLog("error", "kill: hub not connected");
    return;
  }
  try {
    _rp.hubSocket.send(JSON.stringify({
      type: "kill", name, hard: !!hard,
    }));
    rpLog("info", "kill requested: " + name + (hard ? " (hard)" : ""));
  } catch (e) {
    rpLog("error", "kill send failed: " + e.message);
  }
}

/* ==========================================================================
   TARGET DROPDOWN
   ========================================================================== */

function _hubHttpBase() {
  if (!_rp.hubUrl) return null;
  try {
    const u = new URL(_rp.hubUrl);
    const proto = (u.protocol === "wss:") ? "https:" : "http:";
    return proto + "//" + u.host;
  } catch (e) {
    return null;
  }
}

function rpPopulateTargets() {
  const presetEl = document.getElementById("rpPreset");
  if (!presetEl) return;

  let prevName = null;
  for (const opt of presetEl.options) {
    if (opt.selected && opt.value) { prevName = opt.textContent; break; }
    if (opt.selected && opt.value === "") { prevName = "custom"; break; }
  }

  presetEl.innerHTML = "";

  const base = _hubHttpBase();
  const playgrounds = (_rp.hubConnected && base)
    ? _rp.playgrounds.filter(p => p.role === "playground")
    : [];
  const wsBase = base ? base.replace(/^http/, "ws") : null;

  const names = playgrounds.map(p => p.name).filter(Boolean).sort();

  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = wsBase + "/" + name + "/remote";
    opt.textContent = name;
    presetEl.appendChild(opt);
  }

  const custom = document.createElement("option");
  custom.value = "";
  custom.textContent = "custom";
  presetEl.appendChild(custom);

  const targetEl = document.getElementById("rpTarget");
  const currentValue = targetEl ? targetEl.value : "";

  let matched = false;
  if (currentValue) {
    for (const opt of presetEl.options) {
      if (opt.value && opt.value === currentValue) { opt.selected = true; matched = true; break; }
    }
  }
  if (!matched && prevName && prevName !== "custom") {
    for (const opt of presetEl.options) {
      if (opt.textContent === prevName) { opt.selected = true; matched = true; break; }
    }
  }
  if (!matched) {
    if (currentValue) presetEl.value = "";
    else if (presetEl.options.length > 1) presetEl.selectedIndex = 0;
    else presetEl.value = "";
  }

  if (targetEl && !targetEl.dataset.userEdited && presetEl.value) {
    targetEl.value = presetEl.value;
  }
}

/* ==========================================================================
   BOOT
   ========================================================================== */

(function rpHubBoot() {
  function start() { setTimeout(rpDiscoverHub, 200); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
"""
