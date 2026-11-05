"""
rp_ui.py — the remote panel's DOM wiring.

Adds:

    rpRenderStateJSON()       dump of everything the panel knows
    rpOpenStateModal()        opens the state-JSON modal
    rpCloseStateModal()       closes it
    rpCopyState()             copy to clipboard
    rpDownloadState()         download as remote_panel_state.json

    rpOpenManagement()        opens the hub-management modal
    rpCloseManagement()       closes it
    rpRebuildManagementList() render rows from _rp.playgrounds and
                              the "Available to start" section

Plus the "State JSON" and "Manage" button bindings.

MANAGEMENT MODAL — TWO SECTIONS
===============================
`#rpMgmtList` holds one row per registered playground.  Below it,
`#rpMgmtAvailable` holds a "Start" button per available-but-not-running
playground.  Availability comes from each launcher's `available` list
(published by `pgkit/launcher.py` on its control channel and merged
into the `playgrounds` event by the hub).

Per-row action buttons, in order:

    Open     opens the playground's page in a new tab
    Stop     (only when a launcher in the same group manages it) stops
             just this playground; the launcher process and every
             other member keep running.  This is the counterpart to
             the REPL's `stop <name>`.
    Kill     existing behaviour: sends `shutdown` to the playground,
             which for a grouped member shuts down the whole launcher
             process.  Panel confirms the group semantics via the
             title.
    Force    existing behaviour: SIGTERM escalation of Kill.

VISIBLE:FALSE FIELDS
====================
`rpBuildFieldRow` returns null for any field whose schema entry
carries `visible: false`.  `rpRebuildFields` skips that null.

HUB SHUTDOWN FLOW
=================
Unchanged; see `rpShutdownHub()` in `rp_client.py`.
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
      url:          _rp.hubUrl,
      connected:    _rp.hubConnected,
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
   MANAGEMENT MODAL
   ==========================================================================
   Two sections:

   #rpMgmtList         running playgrounds (role="playground")
   #rpMgmtAvailable    available-to-start (from each launcher's
                       `available` field, minus currently running)

   The Stop button on a running row calls `rpLauncherStop`; the Start
   button on an available row calls `rpLauncherStart`.  Both go over
   the same hub control WS as Kill/Force; the hub forwards to the
   launcher's control channel, which handles start/stop in-process.

   When a group has no launcher (all standalone), the Stop button is
   omitted and no "Available to start" section is rendered for that
   group.  Kill and Force still work; they will only kill the one
   standalone playground, since its group has exactly one member. */

function rpOpenManagement() {
  const modal = document.getElementById("rpMgmtModal");
  if (!modal) return;
  rpRebuildManagementList();

  const btn    = document.getElementById("rpMgmtShutdownHub");
  const status = document.getElementById("rpMgmtStatus");

  if (!_rp.shuttingDown) {
    if (btn) { btn.disabled = false; btn.textContent = "Shut down hub"; }
    if (status) { status.textContent = ""; status.className = "rp-modal-status"; }
  } else {
    if (btn) { btn.disabled = true; btn.textContent = "Shutting down\u2026"; }
    if (status) {
      status.textContent = "hub is shutting down\u2026";
      status.className = "rp-modal-status ok";
    }
  }

  modal.hidden = false;
}

function rpCloseManagement() {
  const modal = document.getElementById("rpMgmtModal");
  if (!modal) return;
  modal.hidden = true;
}

/* ---- launcher helper lookups ------------------------------------- */

function _rpLaunchersForGroup(group) {
  return _rp.playgrounds.filter(
    p => p.role === "launcher" && p.group === group
  );
}

function _rpLauncherManaging(name) {
  for (const p of _rp.playgrounds) {
    if (p.role === "launcher" && (p.available || []).indexOf(name) >= 0) {
      return p;
    }
  }
  return null;
}

/* ---- per-row button builders ------------------------------------- */

function _rpBuildOpenButton(pg) {
  const btn = document.createElement("button");
  btn.textContent = "Open";
  btn.title = "Open " + pg.name + " in a new tab";
  btn.addEventListener("click", () => {
    const html = pg.html_file || (pg.name + "_playground.html");
    const base = _hubHttpBase();
    if (!base) return;
    window.open(base + "/" + pg.name + "/" + html, "_blank");
  });
  return btn;
}

function _rpBuildStopButton(pg) {
  const btn = document.createElement("button");
  btn.textContent = "Stop";
  btn.title = "Stop only " + pg.name
            + ".  The launcher process and its other members keep running.";
  btn.addEventListener("click", () => rpLauncherStop(pg.name));
  return btn;
}

function _rpBuildKillButton(pg, groupNote) {
  const btn = document.createElement("button");
  btn.className = "danger";
  btn.textContent = "Kill";
  btn.title = "Ask " + pg.name + " to shut down cooperatively" + groupNote;
  btn.addEventListener("click", () => rpKillPlayground(pg.name, false));
  return btn;
}

function _rpBuildForceButton(pg, groupNote) {
  const btn = document.createElement("button");
  btn.className = "danger";
  btn.textContent = "Force";
  btn.title = "Escalate to SIGTERM if cooperative shutdown "
            + "does not complete" + groupNote;
  btn.addEventListener("click", () => {
    let msg = "Force-kill " + pg.name
            + (pg.pid ? (" (pid " + pg.pid + ")") : "")
            + "?\n\nThis sends SIGTERM if cooperative shutdown "
            + "fails within five seconds.";
    if (pg.group) {
      msg += "\n\nThis playground is in group '" + pg.group + "'. "
           + "Killing it shuts down every playground in that group.";
    }
    if (confirm(msg)) rpKillPlayground(pg.name, true);
  });
  return btn;
}

function _rpBuildStartButton(name) {
  const btn = document.createElement("button");
  btn.textContent = "Start";
  btn.style.cssText = "padding:3px 10px;border:1px solid "
    + "rgba(140,225,160,0.72);background:transparent;color:#b8ecff;"
    + "border-radius:2px;font-family:inherit;font-size:10px;"
    + "letter-spacing:0.08em;text-transform:uppercase;font-weight:700;"
    + "cursor:pointer;";
  btn.title = "Start " + name + " inside the launcher";
  btn.addEventListener("click", () => rpLauncherStart(name));
  return btn;
}

/* ---- the two sections -------------------------------------------- */

function _rpRenderAvailableSection(launchers, runningNames) {
  let section = document.getElementById("rpMgmtAvailable");
  if (!section) {
    section = document.createElement("div");
    section.id = "rpMgmtAvailable";
    section.style.cssText =
      "margin-top:12px;border-top:1px solid #1e2836;padding-top:10px;";
    const listEl = document.getElementById("rpMgmtList");
    if (listEl && listEl.parentNode) {
      listEl.parentNode.insertBefore(section, listEl.nextSibling);
    }
  }
  section.innerHTML = "";

  for (const launcher of launchers) {
    const avail = (launcher.available || []).filter(
      n => !runningNames.has(n)
    );
    if (avail.length === 0) continue;

    const h = document.createElement("div");
    h.textContent = "Available to start (group '"
                  + (launcher.group || launcher.name) + "'):";
    h.style.cssText =
      "font-size:10px;color:#5a6774;margin-bottom:6px;"
      + "letter-spacing:0.04em;";
    section.appendChild(h);

    for (const name of avail) {
      const row = document.createElement("div");
      row.style.cssText =
        "display:flex;align-items:center;gap:10px;"
        + "padding:6px 12px;border-bottom:1px solid #1e2836;";

      const lbl = document.createElement("span");
      lbl.textContent = name;
      lbl.style.cssText = "flex:1;font-size:11px;color:#dce6f2;";
      row.appendChild(lbl);

      row.appendChild(_rpBuildStartButton(name));
      section.appendChild(row);
    }
  }
}

function rpRebuildManagementList() {
  const list = document.getElementById("rpMgmtList");
  const hint = document.getElementById("rpMgmtHint");
  if (!list) return;

  if (!_rp.hubConnected) {
    list.innerHTML = "";
    const empty = document.createElement("div");
    empty.className = "mgmt-empty";
    empty.textContent =
      "Not connected to a hub. Start any playground to spawn it.";
    list.appendChild(empty);
    if (hint) {
      hint.textContent =
        "The pgkit hub is spawned automatically by the first playground "
        + "you run. Once it is up, playgrounds register with it and "
        + "appear here. The hub outlives any individual playground's "
        + "terminal.";
    }
    return;
  }

  if (hint) {
    hint.textContent =
      "Running playgrounds below.  Stop shuts down one member; Kill "
      + "shuts down its whole group (a shared-process launcher dies as "
      + "a unit).  Force escalates Kill to SIGTERM if cooperative "
      + "shutdown does not complete within five seconds.";
  }

  list.innerHTML = "";
  const running = _rp.playgrounds.filter(p => p.role === "playground")
                                .sort((a, b) => (a.name || "")
                                .localeCompare(b.name || ""));

  const launchers = _rp.playgrounds.filter(p => p.role === "launcher");

  if (running.length === 0) {
    const empty = document.createElement("div");
    empty.className = "mgmt-empty";
    empty.textContent = "No playgrounds registered.";
    list.appendChild(empty);
    _rpRenderAvailableSection(launchers, new Set());
    return;
  }

  const base = _hubHttpBase();
  const runningNames = new Set(running.map(p => p.name));

  for (const pg of running) {
    const row = document.createElement("div");
    row.className = "mgmt-row";

    const name = document.createElement("span");
    name.className = "mgmt-name";
    name.textContent = pg.name;
    name.title = pg.name;
    row.appendChild(name);

    const role = document.createElement("span");
    role.className = "mgmt-role";
    role.textContent = pg.role || "?";
    row.appendChild(role);

    const port = document.createElement("span");
    port.className = "mgmt-port";
    port.textContent = ":" + (pg.port || "?");
    row.appendChild(port);

    const pid = document.createElement("span");
    pid.className = "mgmt-pid";
    pid.textContent = pg.pid ? ("pid " + pg.pid) : "\u2014";
    row.appendChild(pid);

    const actions = document.createElement("div");
    actions.className = "mgmt-actions";

    if (base) {
      actions.appendChild(_rpBuildOpenButton(pg));

      // Stop: offered only when a launcher manages this playground.
      // Standalone (no launcher) means only one member exists in the
      // group, and Kill already does the right thing.
      const manager = _rpLauncherManaging(pg.name);
      if (manager) actions.appendChild(_rpBuildStopButton(pg));

      const groupNote = pg.group
        ? " (group '" + pg.group + "'; killing this shuts down every "
          + "playground in that group)"
        : "";
      actions.appendChild(_rpBuildKillButton(pg, groupNote));
      actions.appendChild(_rpBuildForceButton(pg, groupNote));
    } else {
      const note = document.createElement("span");
      note.style.cssText = "font-size:10px;color:#5a6774;";
      note.textContent = "(no actions)";
      actions.appendChild(note);
    }

    row.appendChild(actions);
    list.appendChild(row);
  }

  _rpRenderAvailableSection(launchers, runningNames);
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

  if (mgmtBtn) mgmtBtn.addEventListener("click", rpOpenManagement);

  const mgmtClose    = document.getElementById("rpMgmtClose");
  const mgmtModal    = document.getElementById("rpMgmtModal");
  const mgmtShutdown = document.getElementById("rpMgmtShutdownHub");
  const mgmtStatus   = document.getElementById("rpMgmtStatus");

  if (mgmtClose) mgmtClose.addEventListener("click", rpCloseManagement);
  if (mgmtModal) {
    mgmtModal.addEventListener("click", (e) => {
      if (e.target === mgmtModal) rpCloseManagement();
    });
  }

  if (mgmtShutdown) {
    mgmtShutdown.addEventListener("click", async () => {
      if (!_rp.hubConnected) {
        if (mgmtStatus) {
          mgmtStatus.textContent = "hub not connected";
          mgmtStatus.className = "rp-modal-status bad";
        }
        return;
      }
      if (!confirm(
            "Shut down the hub AND every playground it manages?\n\n"
          + "This will close every playground process and the hub "
          + "itself. You'll need to start a playground again to "
          + "bring the hub back up."
      )) return;

      mgmtShutdown.disabled = true;
      mgmtShutdown.textContent = "Shutting down\u2026";
      if (mgmtStatus) {
        mgmtStatus.textContent = "waiting for hub acknowledgment\u2026";
        mgmtStatus.className = "rp-modal-status warn";
      }

      const result = await rpShutdownHub();

      if (result.ok) {
        if (mgmtStatus) {
          mgmtStatus.textContent = "hub is shutting down\u2026";
          mgmtStatus.className = "rp-modal-status ok";
        }
        setTimeout(() => {
          const m = document.getElementById("rpMgmtModal");
          if (m) m.hidden = true;
        }, 1500);
      } else {
        if (mgmtStatus) {
          mgmtStatus.textContent = result.error || "shutdown failed";
          mgmtStatus.className = "rp-modal-status bad";
        }
        mgmtShutdown.disabled = false;
        mgmtShutdown.textContent = "Shut down hub";
      }
    });
  }

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (_rp._pendingAction) return;
      if (stateModal && !stateModal.hidden) {
        e.preventDefault();
        rpCloseStateModal();
        return;
      }
      if (mgmtModal && !mgmtModal.hidden) {
        e.preventDefault();
        rpCloseManagement();
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
