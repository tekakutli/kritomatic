"""
rp_ui.py — the remote panel's DOM wiring.

Adds:

    rpRenderStateJSON()    dump of everything the panel knows
    rpOpenStateModal()     opens the modal, fills in the JSON
    rpCloseStateModal()    closes it
    rpCopyState()          copy to clipboard
    rpDownloadState()      download as remote_panel_state.json

Plus the "State JSON" button binding.
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

  const byLevel = new Map();
  const globalActions = [];
  for (const name in _rp.schema) {
    const meta = _rp.schema[name];
    if (!meta || meta.kind !== "action") continue;
    const scope = meta.scope || "global";
    const level = meta.level || null;

    if (scope === "global" || !level) {
      globalActions.push(name);
      continue;
    }
    if (!byLevel.has(level)) {
      byLevel.set(level, { all: [], selection: [], row: [] });
    }
    const bucket = byLevel.get(level);
    if (bucket[scope]) bucket[scope].push(name);
  }

  const anyActions = globalActions.length > 0 || byLevel.size > 0;
  if (!anyActions) {
    panel.classList.add("collapsed");
    if (btn) btn.textContent = "Show";
    return;
  }

  if (!_rp.actionsCollapsedByUser) {
    panel.classList.remove("collapsed");
    if (btn) btn.textContent = "Hide";
  }

  /* 1. Global actions. */
  if (globalActions.length > 0) {
    const group = document.createElement("div");
    group.className = "action-group";

    const h4 = document.createElement("h4");
    h4.textContent = "Global";
    group.appendChild(h4);

    const btns = document.createElement("div");
    btns.className = "btns";
    for (const name of globalActions.sort()) {
      btns.appendChild(rpBuildActionButton(name, false));
    }
    group.appendChild(btns);
    body.appendChild(group);
  }

  /* 2. Level sections. */
  if (!_rp.entities) return;
  for (const levelName of _rp.entityOrder) {
    const level = _rp.entities[levelName];
    if (!level) continue;
    const scopeInfo = byLevel.get(levelName)
      || { all: [], selection: [], row: [] };

    const hasHeaderButtons =
      scopeInfo.all.length > 0 || scopeInfo.selection.length > 0;

    if (level.items.length === 0 && !hasHeaderButtons) continue;

    const group = document.createElement("div");
    group.className = "action-group";

    const h4 = document.createElement("h4");
    h4.textContent = level.label || levelName;
    group.appendChild(h4);

    if (hasHeaderButtons) {
      const header = document.createElement("div");
      header.className = "btns";
      header.style.marginBottom = "6px";

      const hasSelection =
        level.selected !== null && level.selected !== undefined;

      for (const name of scopeInfo.all.sort()) {
        header.appendChild(rpBuildActionButton(name, false));
      }
      for (const name of scopeInfo.selection.sort()) {
        header.appendChild(rpBuildActionButton(name, !hasSelection));
      }
      group.appendChild(header);
    }

    if (level.items.length > 0) {
      const itemList = document.createElement("div");
      itemList.className = "action-items";

      for (const item of level.items) {
        const row = document.createElement("div");
        row.className = "action-item";
        if (item.name === level.selected) {
          row.classList.add("selected");
        }

        const lbl = document.createElement("span");
        lbl.className = "action-item-label";
        lbl.textContent = item.label || item.name;
        lbl.title = item.name;
        lbl.addEventListener("click", () => {
          rpSelectItem(levelName, item.name);
        });
        row.appendChild(lbl);

        for (const name of scopeInfo.row.slice().sort()) {
          const applies = item.applies && item.applies[name];
          if (!applies) continue;
          const btn = rpBuildRowActionButton(name, {
            level: levelName,
            name:  item.name,
          });
          row.appendChild(btn);
        }

        itemList.appendChild(row);
      }
      group.appendChild(itemList);
    }

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

    const h4 = document.createElement("h4");
    h4.textContent = _rpSectionLabel(section);
    h4.style.cursor = "pointer";
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
   STATE JSON MODAL
   ==========================================================================
   Renders the panel's current knowledge as pretty-printed JSON:
   connection, values, schema, entities, entity order.  Rebuilt on
   every open so the view is always fresh — no polling, no
   subscription.  Download and Copy operate on the same text. */

function rpBuildStateJSON() {
  return {
    connected:    _rp.connected,
    target:       _rp.target,
    clientId:     _rp.clientId,
    presence:     _rp.presence,
    entityOrder:  _rp.entityOrder,
    entities:     _rp.entities,
    schema:       _rp.schema,
    values:       _rp.values,
  };
}

function rpRenderStateJSON() {
  const el = document.getElementById("rpStateBody");
  if (!el) return;
  try {
    el.textContent = JSON.stringify(rpBuildStateJSON(), null, 2);
  } catch (e) {
    el.textContent = "// state JSON failed: " + e.message;
  }
}

function rpOpenStateModal() {
  const modal = document.getElementById("rpStateModal");
  if (!modal) return;
  rpRenderStateJSON();
  modal.hidden = false;
}

function rpCloseStateModal() {
  const modal = document.getElementById("rpStateModal");
  if (!modal) return;
  modal.hidden = true;
}

function rpCopyState() {
  const el = document.getElementById("rpStateBody");
  if (!el) return;
  const text = el.textContent || "";
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => rpLog("info", "state JSON copied"),
      (e) => rpLog("error", "copy failed: " + (e.message || e))
    );
  } else {
    rpLog("error", "clipboard API unavailable");
  }
}

function rpDownloadState() {
  const el = document.getElementById("rpStateBody");
  if (!el) return;
  const text = el.textContent || "{}";
  const blob = new Blob([text], { type: "application/json" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url;
  a.download = "remote_panel_state.json";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  rpLog("info", "state JSON downloaded");
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
  const stateBtn   = document.getElementById("rpStateBtn");

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

  /* ---- state modal bindings ------------------------------------ */

  if (stateBtn) stateBtn.addEventListener("click", rpOpenStateModal);

  const stateClose = document.getElementById("rpStateClose");
  const stateCopy  = document.getElementById("rpStateCopy");
  const stateDl    = document.getElementById("rpStateDownload");
  const stateModal = document.getElementById("rpStateModal");

  if (stateClose) stateClose.addEventListener("click", rpCloseStateModal);
  if (stateCopy)  stateCopy.addEventListener("click", rpCopyState);
  if (stateDl)    stateDl.addEventListener("click", rpDownloadState);
  if (stateModal) {
    stateModal.addEventListener("click", (e) => {
      if (e.target === stateModal) rpCloseStateModal();
    });
  }

  /* ---- action form modal bindings ------------------------------ */

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

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!_rp._pendingAction &&
          stateModal && !stateModal.hidden) {
        e.preventDefault();
        rpCloseStateModal();
        return;
      }
    }
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
