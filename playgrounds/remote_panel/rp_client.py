"""
rp_client.py — the remote panel's WebSocket client.

BROWSER-SIDE, connecting TO a playground.  Receives `state`,
`schema`, and `entities`.  Sends `patch` and `command`.

ENTITIES
========
The `entities` message carries every level the playground declares,
with its items, its selected item, and (for each row-scope action)
whether the action applies to that item.  The client stores the
message in `_rp.entities` and `_rp.entityOrder`, then triggers a
panel rebuild — the layout is derived from the entities, not from
the schema alone.

SELECT
======
Clicking an item in the remote panel sends a `select` command with
`{level, name}`.  The playground's `pgkitHandleSelect` translates
that to its own selection.  A subsequent entities refresh shows the
new selection.

HUB DISCOVERY (EVENTS, NOT POLLING)
===================================
The panel learns which playgrounds are alive from the pgkit hub
(`pgkit/hub.py`), via a WebSocket on `ws://<hub>/__hub_ws` with
`role=panel`.  The hub pushes a `playgrounds` event on every
connect and on every registration or deregistration change — no
polling.  Kill requests travel the same socket.

TARGET DROPDOWN — REGISTRY-ONLY
===============================
The dropdown is populated exclusively from the hub registry.  There
is no static fallback: a panel that has never seen the hub cannot
know which playgrounds (or ports) are actually serving, and
inventing names/URLs would be wrong by construction.  The "custom"
option is the escape hatch — a user who knows a URL types it, and
`localStorage` remembers it across reloads.

The registry-only dropdown also handles the panel-first startup
correctly: while the hub is up but no playground has registered, the
dropdown contains only "custom" and the target field stays at
whatever `localStorage` held.  The moment a playground joins, the
next `playgrounds` event populates it.

TARGET INPUT SYNC
=================
`rpPopulateTargets` also mirrors the current preset selection into
the `#rpTarget` text field, but only when the user has not
manually edited that field.  The "user has edited" flag is
`#rpTarget.dataset.userEdited`; `rp_ui.py` sets it on `input` and
clears it on dropdown `change`.

The selection priority, applied when the options change:

    1. An option whose value equals the current target field's
       value.  This is what makes hub discovery seamless on a
       running panel: the target field held a direct URL; after
       the first `playgrounds` event the dropdown has hub URLs, and
       the value match never fires, but the target field is then
       rewritten by the sync step below when the dropdown default
       changed.  Once the user has connected via a hub URL, the
       field holds the hub URL, and this branch preserves it across
       later events.
    2. An option whose text equals the previously-selected option's
       text.  This is what makes a later-joining playground preserve
       the user's earlier choice by name across a rebuild.
    3. The first real option — **only if the target field is empty**.
       Fresh page + hub up + playgrounds present: the field is
       empty, so default to the first hub option.  Fresh page +
       hub up + no playgrounds: only "custom" exists, so the field
       is empty and stays empty.  Fresh page + saved direct URL:
       no option matches, but the field is non-empty, so "custom"
       stays selected and the field is preserved — the user keeps
       the URL they last used, and the dropdown honestly reports
       that it cannot map it to a registered playground.

Branch 3 used to fire whenever branches 1 and 2 didn't match, so
a saved direct URL was silently replaced by whichever hub
playground happened to sort first.  The "target field is empty"
guard on branch 3 is what preserves the user's saved preference.

VISIBLE:FALSE FIELDS
====================
`pgkit/schema.py` documents `visible: false` as "API-only, no DOM";
`pgkit/render.py` (the in-Krita panel renderer) already honours it.
`rpBuildFieldRow` honours it too: a `visible: false` field produces
no DOM row, and `rpRebuildFields` skips the `null` return.
`rpUpdateWidget` and `rpOnState` need no change — they already
no-op for names with no `_rp.fields` entry.
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

  // ---- hub management --------------------------------------------
  hubSocket:         null,   // ws to /__hub_ws with role=panel
  hubUrl:            null,   // e.g. "ws://127.0.0.1:8780/__hub_ws"
  hubConnected:      false,
  playgrounds:       [],     // [{ name, port, role, pid, html_file,
                             //    connected_at }]
  hubReconnectMs:    1000,
  hubReconnectTimer: null,
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
   WIRE  (playground channel)
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
   ==========================================================================
   A field whose schema entry has `visible: false` is API-only —
   see `pgkit/schema.py`.  The playground's `pgkit/render.py` honours
   that flag; so does this renderer.  A null return propagates up to
   `rpRebuildFields`, which drops the row.  `rpUpdateWidget` finds no
   `_rp.fields[name]` entry for such a field and no-ops, so live
   patches for it do not touch the DOM either. */

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
   ==========================================================================
   A row action receives `target = {level, name}`.  A header or
   global action receives null.  If the action declares `requires`,
   the modal gathers the payload, merging in the target if any. */

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
        ok = false;
        break;
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
   HUB MANAGEMENT  (ws to /__hub_ws with role=panel)
   ==========================================================================
   The panel discovers the registered playground set from the hub
   over a WebSocket, not by polling.  Kill requests travel the same
   channel and return as `kill_result` events.  All of this is
   disabled in standalone mode (no hub reachable); see rpDiscoverHub. */

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
  if (_rp.hubReconnectTimer) {
    clearTimeout(_rp.hubReconnectTimer);
    _rp.hubReconnectTimer = null;
  }
  if (_rp.hubSocket) {
    try { _rp.hubSocket.close(); } catch (e) {}
    _rp.hubSocket = null;
  }
  if (!_rp.hubUrl) return;

  let sock;
  try { sock = new WebSocket(_rp.hubUrl); }
  catch (e) { _rpScheduleHubReconnect(); return; }
  _rp.hubSocket = sock;

  sock.addEventListener("open", () => {
    _rp.hubConnected = true;
    _rp.hubReconnectMs = 1000;
    try {
      sock.send(JSON.stringify({ type: "hello", role: "panel" }));
    } catch (e) {}
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
    rpScheduleHubReconnect();
  });

  sock.addEventListener("error", () => {});
}

function _rpScheduleHubReconnect() {
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
    rpLog("info", "hub: " + _rp.playgrounds.length + " playground(s)");
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
        "kill " + msg.name + ": "
          + (msg.success ? "ok" : (msg.message || "failed")),
        msg.success ? "ok" : "bad"
      );
    }
    return;
  }
  if (msg.type === "shutdown_ack") {
    rpLog("info", "hub shutdown acknowledged");
    return;
  }
}

function rpKillPlayground(name, hard) {
  if (!_rp.hubSocket || !_rp.hubConnected) {
    rpLog("error", "kill: hub not connected");
    return;
  }
  try {
    _rp.hubSocket.send(JSON.stringify({
      type: "kill",
      name: name,
      hard: !!hard,
    }));
    rpLog("info",
          "kill requested: " + name + (hard ? " (hard)" : ""));
  } catch (e) {
    rpLog("error", "kill send failed: " + e.message);
  }
}

/* ==========================================================================
   TARGET DROPDOWN — REGISTRY-ONLY
   ==========================================================================
   Populated exclusively from the hub registry.  There is no static
   fallback: a panel that has never seen the hub cannot know which
   playgrounds (or ports) are actually serving, so it must not
   invent names/URLs.  "custom" is the escape hatch — a user who
   knows a URL types it into the target field, and `localStorage`
   remembers it across reloads.

   Selection priority after a rebuild (see module docstring for the
   reasoning behind each branch):

     1. An option whose value equals the current target field value.
     2. An option whose text matches the previously-selected option.
     3. The first real option — only when the target field is empty.
        A non-empty target field that matches no option means the
        user has a saved URL (typically a direct URL from a session
        before the hub was running); "custom" stays selected and
        the field is preserved.

   Then, if the target field has not been edited by the user, it is
   overwritten with the new selection's value.  `rp_ui.py` sets
   `dataset.userEdited` on input and clears it on dropdown change. */

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

  // Remember the previous selection *by text* (name) so a hub
  // transition (empty → populated, or a later-joining playground)
  // preserves the user's choice.
  let prevName = null;
  for (const opt of presetEl.options) {
    if (opt.selected && opt.value) { prevName = opt.textContent; break; }
    if (opt.selected && opt.value === "") { prevName = "custom"; break; }
  }

  presetEl.innerHTML = "";

  // Everything comes from the hub registry.  There is no static
  // fallback: without the hub, the panel has no way to know which
  // playgrounds (and ports) are actually serving.
  const base = _hubHttpBase();
  const playgrounds = (_rp.hubConnected && base)
    ? _rp.playgrounds.filter(p => p.role === "playground")
    : [];
  const wsBase = base ? base.replace(/^http/, "ws") : null;

  const names = playgrounds
    .map(p => p.name)
    .filter(Boolean)
    .sort();

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

  // ---- Selection: value → name → first real (guarded) ------------
  const targetEl = document.getElementById("rpTarget");
  const currentValue = targetEl ? targetEl.value : "";

  let matched = false;
  if (currentValue) {
    for (const opt of presetEl.options) {
      if (opt.value && opt.value === currentValue) {
        opt.selected = true;
        matched = true;
        break;
      }
    }
  }
  if (!matched && prevName && prevName !== "custom") {
    for (const opt of presetEl.options) {
      if (opt.textContent === prevName) {
        opt.selected = true;
        matched = true;
        break;
      }
    }
  }
  if (!matched) {
    // If the target field carries a value that matches no current
    // option — typically a saved direct URL from a session where
    // the hub wasn't running, or a hub URL for a playground that
    // has since deregistered — keep "custom" selected. The target
    // field stays put; the sync step below is skipped because
    // `presetEl.value` is empty.
    //
    // The alternative — defaulting to the first real option — would
    // silently replace the user's saved preference with whichever
    // playground happens to sort first, which is wrong whenever the
    // saved URL names a specific playground.
    if (currentValue) {
      presetEl.value = "";
    } else if (presetEl.options.length > 1) {
      presetEl.selectedIndex = 0;
    } else {
      presetEl.value = "";
    }
  }

  // Mirror the current preset selection into the target text field,
  // but only if the user hasn't manually edited it.  On first load
  // (before the hub WS connects) the dropdown has only "custom";
  // the first `playgrounds` event from the hub triggers this
  // function again with real entries, and the field is updated to
  // reflect the hub URL — so clicking Connect without touching the
  // dropdown connects through the hub, as intended.
  if (targetEl && !targetEl.dataset.userEdited && presetEl.value) {
    targetEl.value = presetEl.value;
  }
}

/* ==========================================================================
   BOOT — start hub discovery in parallel with the playground channel
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
