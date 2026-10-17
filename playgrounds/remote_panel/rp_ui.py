"""
rp_ui.py — the remote panel's DOM wiring.

Two jobs:

    · the top bar's Connect / Disconnect / preset dropdown, the
      two collapse toggles (actions, log), and the modal's
      Cancel / Send / Enter / Escape / backdrop bindings;

    · `rpRebuildPanel`, which re-renders the action groups and the
      field cards from the current `_rp.values` and `_rp.schema`,
      and `rpRenderLog`.

`rpRebuildPanel` splits schema entries by `kind`:

    kind === "action"        → an action button, grouped into the
                               action panel by `meta.section`

    anything else            → a field, rendered as an input /
                               select / checkbox, grouped into the
                               fields panel by prefix or
                               `meta.section`

The actions panel auto-shows on first content arrival unless the
user has explicitly hidden it; the fields panel preserves each
card's open/closed state across rebuilds.
"""

RP_UI_JS = r"""
/* ==========================================================================
   PANEL REBUILD
   ========================================================================== */

function rpRebuildPanel() {
  rpRebuildActions();
  rpRebuildFields();
}

function rpRebuildActions() {
  const body  = document.getElementById("rpActionsBody");
  const panel = document.getElementById("actionsPanel");
  const btn   = document.getElementById("rpActionsToggle");
  if (!body || !panel) return;
  body.innerHTML = "";
  _rp.actions = {};

  const bySection = new Map();
  for (const name in _rp.schema) {
    const meta = _rp.schema[name];
    if (!meta || meta.kind !== "action") continue;
    const section = meta.section || "actions";
    if (!bySection.has(section)) bySection.set(section, []);
    bySection.get(section).push(name);
  }

  if (bySection.size === 0) {
    panel.classList.add("collapsed");
    if (btn) btn.textContent = "Show";
    return;
  }

  if (!_rp.actionsCollapsedByUser) {
    panel.classList.remove("collapsed");
    if (btn) btn.textContent = "Hide";
  }

  const sections = Array.from(bySection.keys()).sort();
  for (const section of sections) {
    const group = document.createElement("div");
    group.className = "action-group";

    const h4 = document.createElement("h4");
    h4.textContent = _rpSectionLabel(section);
    group.appendChild(h4);

    const btns = document.createElement("div");
    btns.className = "btns";
    for (const name of bySection.get(section).sort()) {
      btns.appendChild(rpBuildActionButton(name));
    }
    group.appendChild(btns);
    body.appendChild(group);
  }
}

function rpRebuildFields() {
  const container = document.getElementById("fieldsPanel");
  if (!container) return;

  const bySection = new Map();
  for (const name in _rp.values) {
    const meta = _rp.schema[name] || {};
    if (meta.kind === "action") continue;
    const section = _rpSectionForField(name, meta);
    if (!bySection.has(section)) bySection.set(section, []);
    bySection.get(section).push(name);
  }

  if (bySection.size === 0) {
    container.innerHTML =
      '<div class="empty">not connected</div>';
    _rp.fields = {};
    return;
  }

  /* Preserve which field cards were open. */
  const openState = {};
  container.querySelectorAll("details.field-card").forEach((d) => {
    const s = d.querySelector("summary");
    if (s) openState[s.textContent] = d.open;
  });

  container.innerHTML = "";
  _rp.fields = {};

  const sections = Array.from(bySection.keys()).sort();
  for (const section of sections) {
    const card = document.createElement("details");
    card.className = "field-card";
    card.open = openState[_rpSectionLabel(section)] !== false;

    const summary = document.createElement("summary");
    summary.textContent = _rpSectionLabel(section);
    const h4 = document.createElement("h4");
    h4.textContent = _rpSectionLabel(section);
    h4.style.cursor = "pointer";
    summary.remove();
    card.appendChild(h4);

    const body = document.createElement("div");
    body.className = "field-body";
    for (const name of bySection.get(section).sort()) {
      body.appendChild(rpBuildFieldRow(name));
    }
    card.appendChild(body);
    container.appendChild(card);
  }
}

/* ==========================================================================
   LOG RENDERING
   ========================================================================== */

function rpRenderLog() {
  const el = document.getElementById("rpLogBody");
  if (!el) return;
  el.innerHTML = "";
  for (const entry of _rp.log) {
    const line = document.createElement("div");
    line.className = "log-line log-" + entry.kind;
    line.textContent = "[" + entry.ts + "] " + entry.text;
    el.appendChild(line);
  }
  el.scrollTop = el.scrollHeight;
}

/* ==========================================================================
   PANEL COLLAPSE TOGGLES
   ========================================================================== */

function _rpTogglePanel(panelId, btnId, shownLabel, hiddenLabel) {
  const panel = document.getElementById(panelId);
  const btn   = document.getElementById(btnId);
  if (!panel || !btn) return;
  const isCollapsed = panel.classList.toggle("collapsed");
  btn.textContent = isCollapsed ? shownLabel : hiddenLabel;
}

/* ==========================================================================
   INSTALL
   ========================================================================== */

(function installRemotePanel() {
  const targetEl   = document.getElementById("rpTarget");
  const presetEl   = document.getElementById("rpPreset");
  const connectBtn = document.getElementById("rpConnectBtn");
  const discBtn    = document.getElementById("rpDisconnectBtn");
  const actionsBtn = document.getElementById("rpActionsToggle");
  const logBtn     = document.getElementById("rpLogToggle");
  const logClear   = document.getElementById("rpLogClear");

  let saved = null;
  try { saved = localStorage.getItem("rp.target"); } catch (e) {}
  const initial = saved || "ws://127.0.0.1:8767/remote";
  if (targetEl) targetEl.value = initial;
  if (presetEl) {
    let matched = false;
    for (const opt of presetEl.options) {
      if (opt.value && opt.value === initial) {
        opt.selected = true;
        matched = true;
        break;
      }
    }
    if (!matched) presetEl.value = "";
  }

  if (presetEl) presetEl.addEventListener("change", () => {
    if (presetEl.value) targetEl.value = presetEl.value;
  });

  if (targetEl) targetEl.addEventListener("input", () => {
    if (presetEl && targetEl.value !== presetEl.value) presetEl.value = "";
  });

  if (connectBtn) connectBtn.addEventListener("click", () => {
    const t = (targetEl.value || "").trim();
    if (!t) return;
    rpConnect(t);
  });

  if (discBtn) discBtn.addEventListener("click", rpDisconnect);

  if (actionsBtn) actionsBtn.addEventListener("click", () => {
    const panel = document.getElementById("actionsPanel");
    const isCollapsed = panel.classList.toggle("collapsed");
    _rp.actionsCollapsedByUser = isCollapsed;
    actionsBtn.textContent = isCollapsed ? "Show" : "Hide";
  });

  if (logBtn) logBtn.addEventListener("click", () => {
    _rpTogglePanel("logPanel", "rpLogToggle", "Show", "Hide");
  });

  if (logClear) logClear.addEventListener("click", () => {
    _rp.log = [];
    rpRenderLog();
  });

  /* ---- modal bindings ------------------------------------------ */

  const modalCancel = document.getElementById("rpModalCancel");
  const modalSend   = document.getElementById("rpModalSend");
  const modalBkdrp  = document.getElementById("rpModalBackdrop");

  if (modalCancel) modalCancel.addEventListener("click", rpCancelActionForm);
  if (modalSend)   modalSend.addEventListener("click", rpCommitActionForm);
  if (modalBkdrp) {
    modalBkdrp.addEventListener("click", (e) => {
      if (e.target === modalBkdrp) rpCancelActionForm();
    });
  }

  /* Enter sends, Escape cancels — only while a pending action form
     is open, so intercepting these globally is safe. */
  window.addEventListener("keydown", (e) => {
    if (!_rp._pendingAction) return;
    if (e.key === "Enter") {
      e.preventDefault();
      const sendBtn = document.getElementById("rpModalSend");
      if (sendBtn && !sendBtn.disabled) rpCommitActionForm();
    } else if (e.key === "Escape") {
      e.preventDefault();
      rpCancelActionForm();
    }
  });

  rpSetStatus("disconnected");
})();
"""
