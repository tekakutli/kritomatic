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
    rpRebuildManagementList() render rows from _rp.playgrounds

Plus the "State JSON" and "Manage" button bindings.

VISIBLE:FALSE FIELDS
====================
`rpBuildFieldRow` (in rp_client.py) returns null for any field whose
schema entry carries `visible: false` — the documented pgkit contract
for "API-only, no DOM" (see pgkit/schema.py, and pgkit/render.py's
matching behaviour).  `rpRebuildFields` below must check for that
null return before calling `body.appendChild`, or the entire panel
rebuild aborts at the first such field with a `TypeError` and every
section after it fails to render.  The two-line guard at the fix
site is what keeps that contract whole.

HUB DISCOVERY, NOT POLLING
==========================
The target dropdown is populated by `rpPopulateTargets` (defined in
rp_client.py), which runs from three places: (1) install time
(registry-only; the dropdown has only "custom" until the hub WS
connects), (2) every `playgrounds` event from the hub WS, and
(3) hub WS disconnect.  Nothing polls.  The management list is
rebuilt on the same events.  The management modal is a passive
viewer; opening it just re-renders the current `_rp.playgrounds`.

TARGET FIELD EDITORIAL STATE
============================
The `#rpTarget` text field tracks a single flag, `dataset.userEdited`,
so `rpPopulateTargets` knows whether to overwrite its value when the
preset selection changes. Set on input, cleared on dropdown change.
See rpPopulateTargets in rp_client.py for the consumer side.

HUB SHUTDOWN FLOW
=================
The "Shut down hub" button in the management modal calls
`rpShutdownHub()` (in rp_client.py) and renders its result into
`#rpMgmtStatus`.  The button is disabled while the request is in
flight and re-enabled only on failure; a successful ack leaves it
disabled, since the hub process is about to exit.

The status span cycles through:

    ""                              (nothing sent yet)
    "waiting for hub acknowledgment…"  (request in flight)
    "hub is shutting down…"            (ack received)
    "no acknowledgment from hub within 5s"  (timeout)
    "hub not connected" / "send failed: …"  (preconditions)

A successful shutdown leaves `_rp.shuttingDown` set, so reopening the
modal afterwards shows the same "hub is shutting down…" state; the
button stays disabled.  A failed one resets the flag, so reopening
shows the fresh "Shut down hub" button, ready to retry.

OPEN BUTTON URL
===============
The Open button builds its URL from the registration entry's own
`html_file`, which the playground supplies to its hub registration
(via `kit_serve(html_file=...)` → `ensure_running(html_file=...)` →
the `hello` frame → `Registration.html_file` → `to_json()`).  No
hardcoded filename map lives in the panel — a new playground is
opened correctly without touching this file.  The fallback
`<name>_playground.html` covers old hubs that don't yet carry the
field.

GROUPED PLAYGROUNDS
===================
A registration may carry a `group` string (see `pgkit/hub.py`'s
GROUPS section and `pgkit/hub_client.py`).  Groups are metadata, not
routing — the target dropdown still lists each playground
individually, and the CLI still targets each by name.  The only
visible consequence here is that the Kill/Force buttons' `title` and
the Force confirm dialog mention the group when the field is
non-None: a user who is about to kill "board" should know that the
other five members of the shared-process group will go down with
it.  `pg.group` is read from the same registration entry every
other management-list field comes from; no new fetch, no new
state.
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
    container.innerHTML =
      '<div class="empty">not connected</div>';
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
      // `rpBuildFieldRow` returns null for fields whose schema entry
      // carries `visible: false` (the documented "API-only, no DOM"
      // contract).  A null return must be skipped, not appended:
      // `body.appendChild(null)` throws TypeError and aborts the
      // entire rebuild, leaving every section after the first such
      // field unrendered.
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
      url:         _rp.hubUrl,
      connected:   _rp.hubConnected,
      shuttingDown: _rp.shuttingDown,
      playgrounds: _rp.playgrounds,
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
   Rows are rebuilt from `_rp.playgrounds`.  `rpPopulateTargets` in
   rp_client.py triggers `rpRebuildManagementList()` on every hub
   event, so this list is always current without polling.

   The Open button's URL is built from `pg.html_file`, which the
   playground carries in its registration (see hub_client.py and
   hub.py).  The `<name>_playground.html` fallback covers old hubs
   that don't yet send the field.

   The Kill and Force buttons consult `pg.group` (optional metadata
   on each registration; see pgkit/hub.py's GROUPS section).  When
   the field is non-None, the buttons' `title` and the Force confirm
   dialog say so — a user who is about to kill "board" should know
   that every other member of the shared-process group will go down
   with it.  A null group (standalone) leaves the titles and the
   confirm dialog exactly as they were before this field existed.

   `#rpMgmtStatus` reports the hub-shutdown flow's state.  It is
   reset on open only when `_rp.shuttingDown` is false: a successful
   shutdown leaves the flag set and the modal shows the same
   "hub is shutting down…" state on reopening.  A failed one resets
   the flag so reopening shows the fresh button. */

function rpOpenManagement() {
  const modal = document.getElementById("rpMgmtModal");
  if (!modal) return;
  rpRebuildManagementList();

  const btn    = document.getElementById("rpMgmtShutdownHub");
  const status = document.getElementById("rpMgmtStatus");

  if (!_rp.shuttingDown) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Shut down hub";
    }
    if (status) {
      status.textContent = "";
      status.className = "rp-modal-status";
    }
  } else {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Shutting down\u2026";
    }
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
        "The pgkit hub is spawned automatically by the first playground " +
        "you run. Once it is up, playgrounds register with it and appear " +
        "here. The hub outlives any individual playground's terminal.";
    }
    return;
  }

  if (hint) {
    hint.textContent =
      "Playgrounds are registered with the pgkit hub. Click Kill to " +
      "ask one to shut down. Force escalates to SIGTERM if cooperative " +
      "shutdown does not complete within five seconds.";
  }

  list.innerHTML = "";
  const pgs = _rp.playgrounds.slice().sort(
    (a, b) => (a.name || "").localeCompare(b.name || "")
  );
  if (pgs.length === 0) {
    const empty = document.createElement("div");
    empty.className = "mgmt-empty";
    empty.textContent = "No playgrounds registered.";
    list.appendChild(empty);
    return;
  }

  const base = _hubHttpBase();

  for (const pg of pgs) {
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

    if (pg.role === "playground" && base) {
      const openBtn = document.createElement("button");
      openBtn.textContent = "Open";
      openBtn.title = "Open " + pg.name + " in a new tab";
      openBtn.addEventListener("click", () => {
        // `pg.html_file` comes from the playground's own
        // `kit_serve(html_file=...)` registration. The fallback
        // only fires against old hubs that don't carry the field.
        const html = pg.html_file || (pg.name + "_playground.html");
        const url  = base + "/" + pg.name + "/" + html;
        window.open(url, "_blank");
      });
      actions.appendChild(openBtn);

      // Grouped playgrounds: killing one shuts down every
      // registration in the same group (they share a Python
      // process).  Surface that in the button titles and in the
      // Force confirm dialog so a user does not discover the
      // coupling by accident.  A null group leaves the strings
      // exactly as they were before this field existed.
      const groupNote = pg.group
        ? " (group '" + pg.group + "'; killing this shuts down every "
          + "playground in that group)"
        : "";

      const killBtn = document.createElement("button");
      killBtn.className = "danger";
      killBtn.textContent = "Kill";
      killBtn.title = "Ask " + pg.name + " to shut down cooperatively"
                    + groupNote;
      killBtn.addEventListener("click", () => {
        rpKillPlayground(pg.name, false);
      });
      actions.appendChild(killBtn);

      const hardBtn = document.createElement("button");
      hardBtn.className = "danger";
      hardBtn.textContent = "Force";
      hardBtn.title = "Escalate to SIGTERM if cooperative shutdown "
                    + "does not complete" + groupNote;
      hardBtn.addEventListener("click", () => {
        let msg = "Force-kill " + pg.name
                + (pg.pid ? (" (pid " + pg.pid + ")") : "")
                + "?\n\nThis sends SIGTERM if cooperative shutdown "
                + "fails within five seconds.";
        if (pg.group) {
          msg += "\n\nThis playground is in group '" + pg.group + "'. "
               + "Killing it shuts down every playground in that group.";
        }
        if (confirm(msg)) {
          rpKillPlayground(pg.name, true);
        }
      });
      actions.appendChild(hardBtn);
    } else {
      const note = document.createElement("span");
      note.style.cssText = "font-size:10px;color:#5a6774;";
      note.textContent = "(no actions)";
      actions.appendChild(note);
    }

    row.appendChild(actions);
    list.appendChild(row);
  }
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

  // Populate the target dropdown before touching its selection.
  // At install time the hub WS has not connected yet, so the
  // dropdown contains only "custom"; the first `playgrounds` event
  // from the hub replaces it with real entries. See rpPopulateTargets
  // in rp_client.py.
  rpPopulateTargets();

  let saved = null;
  try { saved = localStorage.getItem("rp.target"); } catch (e) {}
  if (targetEl && saved) targetEl.value = saved;
  // No fallback URL: if the hub never comes up, the user types a URL
  // (custom) or starts a playground to spawn the hub. The first
  // `playgrounds` event will sync the target field to the first real
  // option when one exists and the user hasn't edited the field.

  if (presetEl) presetEl.addEventListener("change", () => {
    if (presetEl.value) {
      targetEl.value = presetEl.value;
      // Dropdown choice is authoritative — clear the "user edited"
      // flag so hub-driven preset updates can overwrite this field
      // again.
      delete targetEl.dataset.userEdited;
    }
  });

  if (targetEl) targetEl.addEventListener("input", () => {
    if (presetEl && targetEl.value !== presetEl.value) presetEl.value = "";
    // The user has typed something; rpPopulateTargets must no
    // longer overwrite this field on hub events.
    targetEl.dataset.userEdited = "1";
  });

  if (connectBtn) connectBtn.addEventListener("click", () => {
    const t = (targetEl.value || "").trim();
    if (!t) {
      rpLog("error", "no target URL — pick from the dropdown or type one");
      if (typeof flashStatus === "function") {
        flashStatus("no target URL", "warn");
      }
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

  /* ---- management modal bindings ------------------------------- */

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

  /* ---- hub shutdown button ------------------------------------ */
  //
  // Flow (see rp_client.py::rpShutdownHub's docstring for the
  // full lifecycle):
  //
  //   1. Native confirm — the action is destructive and cannot
  //      be undone (the hub and every playground exit).
  //   2. Disable the button, mark it "Shutting down…", and put the
  //      status span into a "waiting for ack" state.
  //   3. Await rpShutdownHub().  It resolves when the ack arrives,
  //      or fails after 5s.
  //   4. On success: flip the status to "hub is shutting down…",
  //      close the modal after a brief pause so the user reads it.
  //   5. On failure: restore the button and status so the user can
  //      retry.

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
      )) {
        return;
      }

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
        // Leave the modal open briefly so the user sees the
        // confirmation; after that the underlying WS is dead and
        // the modal is no longer useful.
        setTimeout(() => {
          const m = document.getElementById("rpMgmtModal");
          if (m) m.hidden = true;
        }, 1500);
      } else {
        if (mgmtStatus) {
          mgmtStatus.textContent =
            result.error || "shutdown failed";
          mgmtStatus.className = "rp-modal-status bad";
        }
        mgmtShutdown.disabled = false;
        mgmtShutdown.textContent = "Shut down hub";
      }
    });
  }

  /* ---- keyboard -------------------------------------------------- */

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (_rp._pendingAction) return;  // action modal handles it
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
