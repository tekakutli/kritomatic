"""
rp_client.py — the remote panel's WebSocket client.

BROWSER-SIDE, connecting TO a playground.  Distinct from pgkit's
remote_client (which runs inside a playground and connects to that
playground's own server).  Here, this page is the remote client.

STATE
=====
    _rp.values       {name: current_value}   from `state` messages
    _rp.schema       {name: meta}            from `schema` messages
    _rp.fields       {name: DOM entry}       one per non-action field
    _rp.actions      {name: DOM entry}       one per action
    _rp._pendingAction   the in-flight action form, if any

The `schema` message carries both fields (kind is `"number"`,
`"bool"`, or `"enum"`) and actions (kind is `"action"`).  Actions
may additionally carry a `requires` array describing values the
user must supply; see ACTION INVOCATION below.

MESSAGES SENT
=============
    hello, state_request
    patch             on every field edit
    command           on every action-button click

MESSAGES RECEIVED
=================
    hello_ack, presence, state, schema, patch, command_result

ECHO BEHAVIOUR
==============
The playground suppresses re-broadcast during a remote patch's
application, so this page does not receive a `patch` echo of its
own field writes.  Patch messages from the playground's *local*
user do arrive; they update `_rp.values` and each widget not
currently focused.

STATE REFRESH
=============
After sending a patch, the client schedules a `state_request` 500ms
later.  That lets the panel discover whether the playground
accepted the write (some fields reject silently when no object is
selected).  On a scrub-like edit with many writes, the debounce
collapses them into one confirmation.

ACTION INVOCATION
=================
An action whose meta carries a non-empty `requires` array needs
user-supplied values before it can run.  The values are gathered
on this page — where the click gesture is present, so the browser
will open a file picker or focus a text input without complaint —
and sent with the command payload.

An action without `requires` is dispatched immediately: this page
sends `{type:"command", kind: name, payload: {}}` and the
playground's registered action runs with no arguments.
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
  fields:          {},   /* name -> { row, widget, kind } */
  actions:         {},   /* name -> { btn, meta } */
  presence:        null,
  reconnectTimer:  null,
  reconnectMs:     1000,
  stateTimer:      null,
  rebuildTimer:    null,
  log:             [],
  _pendingAction:  null,
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
  if (!_rp.everConnected) return;
  if (!_rp.target) return;
  if (_rp.reconnectTimer) return;
  _rp.reconnectTimer = setTimeout(() => {
    _rp.reconnectTimer = null;
    if (!_rp.connected && _rp.target) {
      rpLog("info", "reconnecting to " + _rp.target + "\u2026");
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
        _rp.presence.browser > 0 ? "connected" : "no-browser"
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
  rpLog("schema", newNames.length + " entr"
                  + (newNames.length === 1 ? "y" : "ies"));
  if (changed) rpScheduleRebuild();
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

function rpBuildActionButton(name) {
  const meta  = _rp.schema[name] || {};
  const label = meta.label || name;
  const btn   = document.createElement("button");
  btn.type = "button";
  btn.className = "action-btn";
  if (meta.variant === "primary") btn.classList.add("primary");
  else if (meta.variant === "danger") btn.classList.add("danger");
  btn.textContent = label;
  btn.title = name;
  btn.addEventListener("click", () => rpActionClick(name));
  _rp.actions[name] = { btn, meta };
  return btn;
}

/* ==========================================================================
   ACTION INVOCATION — with payload gathering
   ==========================================================================
   An action whose meta carries a non-empty `requires` array needs
   user-supplied values before it can run.  The values are gathered
   on this page (where the click gesture is present) and sent with
   the command payload.

   An action without `requires` is dispatched immediately. */

function rpActionClick(name) {
  const meta = _rp.schema[name] || {};
  const requires = Array.isArray(meta.requires) ? meta.requires : [];
  if (requires.length === 0) {
    const id = rpSendCommand(name, {});
    rpLog("cmd", "\u25B6 " + (meta.label || name) + "  (" + id + ")");
    return;
  }
  rpShowActionForm(name, requires);
}

function rpShowActionForm(name, requires) {
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

  _rp._pendingAction = { name, requires, values };
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
        ok = false;
        break;
      }
    }
  }
  sendBtn.disabled = !ok;
}

function rpCommitActionForm() {
  if (!_rp._pendingAction) return;
  const { name, values } = _rp._pendingAction;
  const meta = _rp.schema[name] || {};
  _rp._pendingAction = null;
  const backdrop = document.getElementById("rpModalBackdrop");
  if (backdrop) backdrop.hidden = true;
  const id = rpSendCommand(name, values);
  rpLog("cmd", "\u25B6 " + (meta.label || name) + "  (" + id + ")");
}

function rpCancelActionForm() {
  _rp._pendingAction = null;
  const backdrop = document.getElementById("rpModalBackdrop");
  if (backdrop) backdrop.hidden = true;
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
"""
