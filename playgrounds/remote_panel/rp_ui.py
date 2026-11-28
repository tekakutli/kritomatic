"""
rp_ui.py — the remote panel's DOM wiring.

Adds:

    rpRenderStateJSON()       dump of everything the panel knows
    rpOpenStateModal()        opens the state-JSON modal
    rpCloseStateModal()       closes it
    rpCopyState()             copy to clipboard
    rpDownloadState()         download as remote_panel_state.json

    rpRebuildManagementList() thin wrapper around
                              _hubManageModal.render()

Plus the "State JSON" and "Manage" button bindings.

MANAGE PLAYGROUNDS MODAL
========================
The DOM, CSS, rendering, and action handlers for the "Manage
playgrounds" modal live in `pgruntime/hub_manage_ui.py` — shared with
`workbench/wb_client.py` so both pages show the same UI backed by the
same code.  This file only:

  · constructs the modal once via `createHubManageUI(hubCtrl, {...})`,
  · wires the panel's `#rpMgmtBtn` to open it,
  · refreshes it whenever the hub broadcasts a new `playgrounds`
    event, via `rpRebuildManagementList()` (called from
    `rp_client.py`'s `onPlaygrounds` callback).

Everything else about the modal is unchanged from the pre-extraction
implementation — row actions, shutdown flow, error mirroring, the
"Start & Open" buttons on available names.

The old standalone helpers — `_rpBuildOpenButton`,
`_rpBuildStopButton`, `_rpBuildKillButton`, `_rpBuildForceButton`,
`_rpBuildStartAndOpenButton`, `_rpRenderAvailableSection`,
`_rpLauncherManaging`, `_mgmtStatusBad`, `_hubHttpBase` — are gone
from this file and now live in `pgruntime/hub_manage_ui.py`.

VISIBLE:FALSE FIELDS
====================
`rpBuildFieldRow` returns null for any field whose schema entry
carries `visible: false`.  `rpRebuildFields` skips that null.

HUB SHUTDOWN FLOW
=================
The shared modal's "Shut down hub" button calls
`hubCtrl.shutdownHub()`, which resolves with `{success, message}` on
ack or 5s timeout.  Errors are surfaced through
`_mgmtStatusBad`-equivalent logic inside the shared module, which
also mirrors each message to the terminal via `console.error`
(routed to `POST ./__log` by the inline head script).
"""

RP_UI_JS = r"""
/* ==========================================================================
   MANAGE-MODAL HANDLE — populated in the install IIFE at the bottom
   ==========================================================================
   Declared here so the onPlaygrounds callback in rp_client.py can
   call rpRebuildManagementList() safely even before the IIFE runs.
   `null` until wire-up. */

let _hubManageModal = null;

/* ==========================================================================
   MANAGE-MODAL REFRESH — called from rp_client.py on every
   `playgrounds` broadcast
   ==========================================================================
   Thin wrapper so rp_client.js's onPlaygrounds callback doesn't
   need to know about the shared modal's shape. */

function rpRebuildManagementList() {
  if (_hubManageModal) _hubManageModal.render();
}

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
    container.innerHTML = '<div class="empty">not connected</div>';
    _rp.fields = {};
    return;
  }

  const openState = {};
  container.querySelectorAll("details.field-card").forEach((d) => {
    const s = d.querySelector("h4");
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
      const row = rpBuildFieldRow(name);
      if (row) body.appendChild(row);
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
   ========================================================================== */

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
    hub: {
      url:          hubCtrl.hubHttpBase(),
      connected:    hubCtrl.isConnected(),
      shuttingDown: _rp.shuttingDown,
      playgrounds:  _rp.playgrounds,
    },
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
  const mgmtBtn    = document.getElementById("rpMgmtBtn");

  rpPopulateTargets();

  let saved = null;
  try { saved = localStorage.getItem("rp.target"); } catch (e) {}
  if (targetEl && saved) targetEl.value = saved;

  if (presetEl) presetEl.addEventListener("change", () => {
    if (presetEl.value) {
      targetEl.value = presetEl.value;
      delete targetEl.dataset.userEdited;
    }
  });

  if (targetEl) targetEl.addEventListener("input", () => {
    if (presetEl && targetEl.value !== presetEl.value) presetEl.value = "";
    targetEl.dataset.userEdited = "1";
  });

  if (connectBtn) connectBtn.addEventListener("click", () => {
    const t = (targetEl.value || "").trim();
    if (!t) {
      rpLog("error", "no target URL — pick from the dropdown or type one");
      if (typeof flashStatus === "function") flashStatus("no target URL", "warn");
      return;
    }
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

  // ---- Manage playgrounds modal -------------------------------
  //
  // The modal's DOM, CSS, and rendering logic live in
  // pgruntime/hub_manage_ui.py (shared with workbench).  All this
  // file does is construct it and wire the trigger button.
  //
  // `logFn: rpLog` routes the modal's error mirroring through the
  // panel's own log — which in turn posts to `./__log` via the
  // console.error override installed in remote_panel_html.py's
  // inline <head> script.  Same behavior as before the extraction.
  //
  // `preferWorkbench: true` matches the old behavior: the "Open"
  // button delegates to `openInWorkbench` first, falling back to a
  // new tab only when no workbench is running.
  _hubManageModal = createHubManageUI(hubCtrl, {
    logFn:            rpLog,
    preferWorkbench:  true,
  });
  if (mgmtBtn) {
    mgmtBtn.addEventListener("click", () => _hubManageModal.open());
  }

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (_rp._pendingAction) return;
      if (stateModal && !stateModal.hidden) {
        e.preventDefault();
        rpCloseStateModal();
        return;
      }
      const manageModalEl = document.getElementById("hubManageModal");
      if (manageModalEl && !manageModalEl.hidden && _hubManageModal) {
        e.preventDefault();
        _hubManageModal.close();
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