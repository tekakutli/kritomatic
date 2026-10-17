"""
rp_ui.py — the remote panel's DOM wiring.

Two jobs:

    · the header's Connect / Disconnect / preset dropdown, and the
      Commands block;
    · `rpRebuildPanel` and `rpRenderLog`, which the client calls
      after receiving state / schema / log updates.

`rpRebuildPanel` groups fields by their top-level prefix
(`pair`, `ui`, `kra`, ...) into one collapsible `<details>` per
group, and calls `rpBuildFieldRow` (in rp_client.py) for each
field.  Rebuilds are debounced by `rpScheduleRebuild` so a `state`
and `schema` arriving one tick apart produce one rebuild, not two.

The panel is not rebuilt on every state update — only when the set
of field *names* changes.  Ordinary state refreshes update each
widget in place via `rpUpdateWidget`, preserving focus and
avoiding flicker.
"""

RP_UI_JS = r"""
/* ==========================================================================
   PANEL REBUILD
   ========================================================================== */

function rpRebuildPanel() {
  const container = document.getElementById("rpFields");
  if (!container) return;

  if (Object.keys(_rp.values).length === 0) {
    container.innerHTML =
      '<div class="empty">waiting for state\u2026</div>';
    _rp.widgets = {};
    return;
  }

  /* Group fields by prefix.  Fields without a dot form a "general"
     group.  Groups are ordered by first appearance in the sorted
     field list. */
  const groups = new Map();
  const names = Object.keys(_rp.values).sort();
  for (const name of names) {
    const dot = name.indexOf(".");
    const prefix = dot >= 0 ? name.slice(0, dot) : "general";
    if (!groups.has(prefix)) groups.set(prefix, []);
    groups.get(prefix).push(name);
  }

  /* Preserve each section's open/closed state across the rebuild. */
  const openState = {};
  container.querySelectorAll("details.section").forEach((d) => {
    const s = d.querySelector("summary");
    if (s) openState[s.textContent] = d.open;
  });

  container.innerHTML = "";
  _rp.widgets = {};

  for (const [prefix, fieldNames] of groups) {
    const details = document.createElement("details");
    details.className = "section";
    details.open = openState[prefix] !== false;

    const summary = document.createElement("summary");
    summary.textContent = prefix;
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "section-body";
    details.appendChild(body);

    for (const name of fieldNames) {
      body.appendChild(rpBuildFieldRow(name));
    }
    container.appendChild(details);
  }
}

/* ==========================================================================
   LOG RENDERING
   ========================================================================== */

function rpRenderLog() {
  const el = document.getElementById("rpLog");
  if (!el) return;
  el.innerHTML = "";
  for (const entry of _rp.log) {
    const line = document.createElement("div");
    line.className = "rp-log-line rp-log-" + entry.kind;
    line.textContent = "[" + entry.ts + "] " + entry.text;
    el.appendChild(line);
  }
  el.scrollTop = el.scrollHeight;
}

/* ==========================================================================
   INSTALL
   ========================================================================== */

(function installRemotePanel() {
  const targetEl   = document.getElementById("rpTarget");
  const presetEl   = document.getElementById("rpPreset");
  const connectBtn = document.getElementById("rpConnectBtn");
  const discBtn    = document.getElementById("rpDisconnectBtn");
  const cmdKindEl  = document.getElementById("rpCmdKind");
  const cmdBodyEl  = document.getElementById("rpCmdPayload");
  const cmdBtn     = document.getElementById("rpCmdBtn");

  /* Restore last target from localStorage; default to pair if none. */
  let saved = null;
  try { saved = localStorage.getItem("rp.target"); } catch (e) {}
  const initial = saved || "ws://127.0.0.1:8767/remote";
  if (targetEl) targetEl.value = initial;
  if (presetEl) {
    /* If the initial value matches a preset, select it.  Otherwise
       leave the "custom" option selected. */
    let matched = false;
    for (const opt of presetEl.options) {
      if (opt.value && opt.value === initial) { opt.selected = true; matched = true; break; }
    }
    if (!matched) presetEl.value = "";
  }

  if (presetEl) presetEl.addEventListener("change", () => {
    if (presetEl.value) targetEl.value = presetEl.value;
  });

  if (targetEl) targetEl.addEventListener("input", () => {
    /* Deselect the preset if the user typed something else. */
    if (presetEl && targetEl.value !== presetEl.value) presetEl.value = "";
  });

  if (connectBtn) connectBtn.addEventListener("click", () => {
    const t = (targetEl.value || "").trim();
    if (!t) return;
    rpConnect(t);
  });

  if (discBtn) discBtn.addEventListener("click", rpDisconnect);

  if (cmdBtn) cmdBtn.addEventListener("click", () => {
    const kind = (cmdKindEl.value || "").trim();
    if (!kind) return;
    let payload = {};
    const raw = (cmdBodyEl.value || "").trim();
    if (raw) {
      try { payload = JSON.parse(raw); }
      catch (e) {
        rpLog("error", "bad command payload JSON: " + e.message);
        return;
      }
    }
    const id = rpSendCommand(kind, payload);
    rpLog("cmd", "sent  " + kind + "  (" + id + ")");
  });

  /* Don't auto-connect.  A fresh page shows the target and lets the
     user click Connect once they know a playground is running.  If
     they've connected before, the target is restored so it's one
     click away. */
  rpSetStatus("disconnected", false);
})();
"""
