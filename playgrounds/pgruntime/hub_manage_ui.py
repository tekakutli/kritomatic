"""pgruntime.hub_manage_ui — shared Manage playgrounds modal.

The visual counterpart to `pgruntime/hub_control.py`.  Provides the
DOM, CSS, and JS for the "Manage playgrounds" modal that both
`remote_panel` and `workbench` use.

Every action the modal offers (Open, Stop, Kill, Force, Start &
Open, Shut down hub, Open workbench) is a call into the `hubCtrl`
object returned by `createHubControl`.  No state lives here that
isn't already derivable from `hubCtrl`.

WIRING
======
    from pgruntime.hub_control   import HUB_CONTROL_JS
    from pgruntime.hub_manage_ui import (
        HUB_MANAGE_UI_CSS, HUB_MANAGE_UI_BODY, HUB_MANAGE_UI_JS,
    )

    html = (
        "<style>\n"
        + HUB_MANAGE_UI_CSS + "\n"
        + PAGE_CSS + "\n"
        "</style>\n"
        ...
        "<body>\n"
        + PAGE_BODY
        + HUB_MANAGE_UI_BODY
        + "<script>\n"
        + HUB_CONTROL_JS  + "\n"
        + HUB_MANAGE_UI_JS + "\n"
        + PAGE_JS
        + "\n</script>\n"
        "</body>\n"
    )

    // In PAGE_JS, after `const hubCtrl = createHubControl(...)`:
    let _hubManageModal = null;
    (function initManage() {
      _hubManageModal = createHubManageUI(hubCtrl, {
        logFn: function (kind, text) { /* page's log function */ },
      });
      document.getElementById("yourManageButtonId")
        .addEventListener("click", _hubManageModal.open);
    })();

    // In your `onPlaygrounds` callback, refresh the visible list:
    if (_hubManageModal) _hubManageModal.render();

SHARED vs PAGE-SPECIFIC
=======================
Shared (this module): the modal container, its CSS, the list
rendering, every row builder, the shutdown flow, the "Open
workbench" handler, and the shutdown-in-flight state (which was
`_rp.shuttingDown` in the old remote_panel-only code).

Page-specific: the trigger button.  Each page names its own button
(`#rpMgmtBtn` in remote_panel, `#wbManageBtn` in workbench) because
the topbar layouts differ.  Wiring the button to `_hubManageModal.open`
is the page's responsibility.

Both pages include the frame CSS (`.rp-modal`, `.rp-modal-title`,
etc.).  Remote_panel uses it for its state-JSON and action-form
modals too, so it can drop its local copies.  Workbench gets the
frame from here and doesn't need to duplicate it.
"""


HUB_MANAGE_UI_CSS = r"""
  /* ---- Shared modal frame ---------------------------------------- *
   * Used by the hub-manage modal here, and by remote_panel's
   * state-JSON and action-form modals.  Kept in this shared module
   * so both pages' modals look identical without duplicating rules.
   * All colours reference CSS custom properties with hardcoded
   * fallbacks, so a page that doesn't define the variables still
   * gets the right look.
   * --------------------------------------------------------------- */
  .hub-modal-backdrop {
    position: fixed; inset: 0; z-index: 100;
    background: rgba(0, 0, 0, 0.55);
    display: flex; align-items: center; justify-content: center;
  }
  .hub-modal-backdrop[hidden] { display: none; }

  .rp-modal {
    background: var(--bg-panel, #0b1018);
    border: 1px solid var(--border-2, #223040);
    border-radius: 4px;
    box-shadow: 0 12px 48px rgba(0, 0, 0, 0.7);
    min-width: 380px;
    max-width: 520px;
    padding: 16px 20px;
    display: flex; flex-direction: column; gap: 14px;
  }
  .rp-modal-title {
    font-size: 11px; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--accent, #00e5ff);
    border-bottom: 1px solid var(--border, #1e2836);
    padding-bottom: 8px;
  }
  .rp-modal-buttons {
    display: flex; justify-content: flex-end; gap: 8px;
    padding-top: 6px;
    border-top: 1px solid var(--border, #1e2836);
  }
  .rp-modal-buttons button {
    padding: 6px 14px; cursor: pointer;
    border: 1px solid var(--border-2, #223040);
    background: transparent;
    color: var(--text-dim, #a8b5c4);
    border-radius: 2px;
    font-family: var(--mono, monospace);
    font-size: 10px;
    letter-spacing: 0.10em; text-transform: uppercase;
    font-weight: 700;
  }
  .rp-modal-buttons button:hover:not(:disabled) {
    border-color: var(--border-3, #34445a);
    color: var(--text, #dce6f2);
    background: rgba(220,230,242,0.03);
  }
  .rp-modal-buttons button:disabled {
    color: var(--text-faint, #3d4756);
    border-color: var(--border, #1e2836);
    cursor: not-allowed;
  }

  .rp-modal-status {
    flex: 1 1 auto; align-self: center;
    font-size: 10px; color: var(--text-mute, #5a6774);
    letter-spacing: 0.02em;
    text-align: right; padding-right: 6px;
    white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .rp-modal-status.ok   { color: var(--accent, #00e5ff); }
  .rp-modal-status.warn { color: var(--warn, #ffe600); }
  .rp-modal-status.bad  { color: var(--danger, #d48590); }

  /* ---- Hub-manage modal specifics ------------------------------- */
  #hubManageModal .rp-modal { width: 80vw; max-width: 720px; }

  #hubManageList {
    max-height: 50vh;
    overflow: auto;
    border: 1px solid var(--border, #1e2836);
    border-radius: 2px;
  }
  #hubManageList .mgmt-row {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border, #1e2836);
    font-size: 11px;
  }
  #hubManageList .mgmt-row:last-child { border-bottom: none; }
  #hubManageList .mgmt-name {
    flex: 0 0 130px; font-weight: 700; color: var(--text, #dce6f2);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  #hubManageList .mgmt-role {
    flex: 0 0 78px;
    font-size: 9px; color: var(--text-mute, #5a6774);
    text-transform: uppercase; letter-spacing: 0.10em; font-weight: 700;
  }
  #hubManageList .mgmt-port {
    flex: 0 0 60px; font-size: 10px; color: var(--text-faint, #3d4756);
    font-variant-numeric: tabular-nums;
  }
  #hubManageList .mgmt-pid {
    flex: 0 0 90px; font-size: 10px; color: var(--text-faint, #3d4756);
    font-variant-numeric: tabular-nums;
  }
  #hubManageList .mgmt-actions {
    flex: 1 1 auto; display: flex; gap: 6px; justify-content: flex-end;
  }
  #hubManageList .mgmt-actions button {
    padding: 3px 10px; cursor: pointer;
    border: 1px solid var(--border-2, #223040);
    background: transparent;
    color: var(--text-dim, #a8b5c4);
    border-radius: 2px;
    font-family: var(--mono, monospace);
    font-size: 10px;
    letter-spacing: 0.08em; text-transform: uppercase; font-weight: 700;
  }
  #hubManageList .mgmt-actions button:hover {
    border-color: var(--border-3, #34445a);
    color: var(--text, #dce6f2);
    background: rgba(220,230,242,0.03);
  }
  #hubManageList .mgmt-actions button.danger {
    border-color: rgba(255,105,125,0.55);
    color: var(--danger-b, #ffb0bc);
  }
  #hubManageList .mgmt-actions button.danger:hover {
    border-color: var(--danger-a, #ff5a69);
    background: rgba(255,90,105,0.08);
    color: #ffd0d8;
  }
  #hubManageList .mgmt-empty {
    padding: 20px; text-align: center;
    font-size: 11px; color: var(--text-faint, #3d4756);
    font-style: italic;
  }

  #hubManageShutdownHub {
    border-color: rgba(255,105,125,0.55) !important;
    color: var(--danger-b, #ffb0bc) !important;
  }
  #hubManageShutdownHub:hover:not(:disabled) {
    border-color: var(--danger-a, #ff5a69) !important;
    background: rgba(255,90,105,0.08) !important;
    color: #ffd0d8 !important;
  }
"""


HUB_MANAGE_UI_BODY = r"""
<div id="hubManageModal" class="hub-modal-backdrop" hidden>
  <div class="rp-modal">
    <div class="rp-modal-title">Manage playgrounds</div>
    <div id="hubManageHint"
         style="font-size:10px;color:#5a6774;line-height:1.5;">
      Playgrounds are registered with the pgkit hub.  Click Kill to
      ask one to shut down.  Force escalates to SIGTERM if cooperative
      shutdown does not complete within five seconds.
    </div>
    <div id="hubManageList"></div>
    <div class="rp-modal-buttons">
      <span id="hubManageStatus" class="rp-modal-status"></span>
      <button id="hubManageShutdownHub"
              title="Ask the hub to shut down, and every playground
                     it manages with it.">
        Shut down hub
      </button>
      <button id="hubManageOpenWorkbench"
              title="Open (or focus) the workbench in a browser tab.">
        Open workbench
      </button>
      <button id="hubManageClose">Close</button>
    </div>
  </div>
</div>
"""


HUB_MANAGE_UI_JS = r"""
/* ==========================================================================
   HUB MANAGE UI — shared between remote_panel and workbench
   ==========================================================================
   See pgruntime/hub_manage_ui.py for the design rationale.

   Exports one function: `createHubManageUI(hubCtrl, opts)`.
   The returned object has { open, close, render }.

   Everything that mutates state calls into `hubCtrl` from
   pgruntime/hub_control.py.  Nothing here holds its own copy of the
   playground list; the caller must call `render()` whenever
   `hubCtrl`'s list changes — typically inside the `onPlaygrounds`
   callback passed to `createHubControl`.
   ========================================================================== */

function createHubManageUI(hubCtrl, opts) {
  opts = opts || {};
  if (!hubCtrl) throw new Error("createHubManageUI: hubCtrl required");

  const logFn = opts.logFn || function (kind, text) {
    if (kind === "error")     { try { console.error("[hub-mgmt]", text); } catch (_) {} }
    else if (kind === "warn") { try { console.warn ("[hub-mgmt]", text); } catch (_) {} }
  };
  const openPlaygroundFn = opts.openPlayground || openPlayground;
  const launchAndOpenFn  = opts.launchAndOpen  || launchAndOpen;

  let shuttingDown = false;

  function els() {
    return {
      modal:    document.getElementById("hubManageModal"),
      list:     document.getElementById("hubManageList"),
      hint:     document.getElementById("hubManageHint"),
      status:   document.getElementById("hubManageStatus"),
      shutdown: document.getElementById("hubManageShutdownHub"),
      wbOpen:   document.getElementById("hubManageOpenWorkbench"),
      close:    document.getElementById("hubManageClose"),
    };
  }

  function statusBad(text) {
    const el = document.getElementById("hubManageStatus");
    if (el) {
      el.textContent = text;
      el.className = "rp-modal-status bad";
    }
    logFn("error", text);
  }

  /* ---- row builders ---------------------------------------------- */

  function buildOpenButton(pg) {
    const btn = document.createElement("button");
    btn.textContent = "Open";
    const base = hubCtrl.hubHttpBase();
    if (!base) { btn.disabled = true; return btn; }
    btn.title = "Open " + pg.name + " in the workbench"
              + " (or a new tab if workbench isn't running).";
    btn.addEventListener("click", () => {
      openPlaygroundFn(pg, {
        playgrounds: hubCtrl.getPlaygrounds(),
        hubBase:     base,
      });
    });
    return btn;
  }

  function buildStopButton(pg) {
    const btn = document.createElement("button");
    btn.textContent = "Stop";
    btn.title = "Stop only " + pg.name
              + ".  The launcher process and its other members keep running.";
    btn.addEventListener("click", () => {
      hubCtrl.stopPlayground(pg.name).then((res) => {
        if (!res.success) statusBad("stop " + pg.name + ": " + (res.message || "failed"));
      });
    });
    return btn;
  }

  function buildKillButton(pg, groupNote) {
    const btn = document.createElement("button");
    btn.className = "danger";
    btn.textContent = "Kill";
    btn.title = "Ask " + pg.name + " to shut down cooperatively" + groupNote;
    btn.addEventListener("click", () => {
      hubCtrl.killPlayground(pg.name, false).then((res) => {
        if (!res.success) statusBad("kill " + pg.name + ": " + (res.message || "failed"));
      });
    });
    return btn;
  }

  function buildForceButton(pg, groupNote) {
    const btn = document.createElement("button");
    btn.className = "danger";
    btn.textContent = "Force";
    btn.title = "Escalate to SIGTERM if cooperative shutdown does not complete"
              + groupNote;
    btn.addEventListener("click", () => {
      let msg = "Force-kill " + pg.name
              + (pg.pid ? (" (pid " + pg.pid + ")") : "")
              + "?\n\nThis sends SIGTERM if cooperative shutdown fails"
              + " within five seconds.";
      if (pg.group) {
        msg += "\n\nThis playground is in group '" + pg.group + "'. "
             + "Killing it shuts down every playground in that group.";
      }
      if (!confirm(msg)) return;
      hubCtrl.killPlayground(pg.name, true).then((res) => {
        if (!res.success) statusBad("force " + pg.name + ": " + (res.message || "failed"));
      });
    });
    return btn;
  }

  function buildStartAndOpenButton(name) {
    const btn = document.createElement("button");
    btn.textContent = "Start & Open";
    btn.style.cssText = "padding:3px 10px;border:1px solid "
      + "rgba(140,225,160,0.72);background:transparent;color:#b8ecff;"
      + "border-radius:2px;font-family:inherit;font-size:10px;"
      + "letter-spacing:0.08em;text-transform:uppercase;font-weight:700;"
      + "cursor:pointer;";
    btn.title = "Start " + name + " and open it in the workbench"
              + " (or a new browser tab if workbench isn't running)";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await launchAndOpenFn(name, hubCtrl, { preferWorkbench: true });
      } catch (e) {
        statusBad("start " + name + ": " + (e.message || e));
      } finally {
        btn.disabled = false;
      }
    });
    return btn;
  }

  function renderAvailableSection(launchers, runningNames) {
    let section = document.getElementById("hubManageAvailable");
    if (!section) {
      section = document.createElement("div");
      section.id = "hubManageAvailable";
      section.style.cssText =
        "margin-top:12px;border-top:1px solid #1e2836;padding-top:10px;";
      const listEl = document.getElementById("hubManageList");
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
        row.appendChild(buildStartAndOpenButton(name));
        section.appendChild(row);
      }
    }
  }

  /* ---- main render ----------------------------------------------- */

  function render() {
    const e = els();
    if (!e.list) return;

    const isConnected = hubCtrl.isConnected();
    const playgrounds = hubCtrl.getPlaygrounds();

    if (!isConnected) {
      e.list.innerHTML = "";
      const empty = document.createElement("div");
      empty.className = "mgmt-empty";
      empty.textContent =
        "Not connected to a hub. Start any playground to spawn it.";
      e.list.appendChild(empty);
      if (e.hint) {
        e.hint.textContent =
          "The pgkit hub is spawned automatically by the first playground "
          + "you run. Once it is up, playgrounds register with it and "
          + "appear here.";
      }
      const avail = document.getElementById("hubManageAvailable");
      if (avail) avail.remove();
      return;
    }

    if (e.hint) {
      e.hint.textContent =
        "Running playgrounds below.  Stop shuts down one member; Kill "
        + "shuts down its whole group (a shared-process launcher dies as "
        + "a unit).  Force escalates Kill to SIGTERM if cooperative "
        + "shutdown does not complete within five seconds.";
    }

    e.list.innerHTML = "";

    const running = playgrounds
      .filter(p => p.role === "playground")
      .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    const launchers = playgrounds.filter(p => p.role === "launcher");

    const base = hubCtrl.hubHttpBase();

    if (running.length === 0) {
      const empty = document.createElement("div");
      empty.className = "mgmt-empty";
      empty.textContent = "No playgrounds registered.";
      e.list.appendChild(empty);
      renderAvailableSection(launchers, new Set());
      return;
    }

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
        actions.appendChild(buildOpenButton(pg));
        if (hubCtrl.findLauncherFor(pg.name)) {
          actions.appendChild(buildStopButton(pg));
        }
        const groupNote = pg.group
          ? " (group '" + pg.group + "'; killing this shuts down every "
            + "playground in that group)"
          : "";
        actions.appendChild(buildKillButton(pg, groupNote));
        actions.appendChild(buildForceButton(pg, groupNote));
      } else {
        const note = document.createElement("span");
        note.style.cssText = "font-size:10px;color:#5a6774;";
        note.textContent = "(no actions)";
        actions.appendChild(note);
      }

      row.appendChild(actions);
      e.list.appendChild(row);
    }

    renderAvailableSection(launchers, runningNames);
  }

  /* ---- open / close ---------------------------------------------- */

  function open() {
    const e = els();
    if (!e.modal) return;
    render();

    if (!shuttingDown) {
      if (e.shutdown) { e.shutdown.disabled = false; e.shutdown.textContent = "Shut down hub"; }
      if (e.status)   { e.status.textContent = ""; e.status.className = "rp-modal-status"; }
    } else {
      if (e.shutdown) { e.shutdown.disabled = true; e.shutdown.textContent = "Shutting down\u2026"; }
      if (e.status)   { e.status.textContent = "hub is shutting down\u2026"; e.status.className = "rp-modal-status ok"; }
    }

    e.modal.hidden = false;
  }

  function close() {
    const e = els();
    if (!e.modal) return;
    e.modal.hidden = true;
  }

  /* ---- shutdown flow --------------------------------------------- */

  async function handleShutdown() {
    const e = els();
    if (!hubCtrl.isConnected()) {
      statusBad("hub not connected");
      return;
    }
    if (!confirm(
          "Shut down the hub AND every playground it manages?\n\n"
        + "This will close every playground process and the hub itself. "
        + "You'll need to start a playground again to bring the hub back up."
    )) return;

    shuttingDown = true;
    if (e.shutdown) { e.shutdown.disabled = true; e.shutdown.textContent = "Shutting down\u2026"; }
    if (e.status)   { e.status.textContent = "waiting for hub acknowledgment\u2026"; e.status.className = "rp-modal-status warn"; }

    const result = await hubCtrl.shutdownHub();

    if (result.success) {
      if (e.status) { e.status.textContent = "hub is shutting down\u2026"; e.status.className = "rp-modal-status ok"; }
      setTimeout(() => { close(); }, 1500);
    } else {
      shuttingDown = false;
      statusBad(result.message || "shutdown failed");
      if (e.shutdown) { e.shutdown.disabled = false; e.shutdown.textContent = "Shut down hub"; }
    }
  }

  /* ---- open workbench -------------------------------------------- */

  function openWorkbench() {
    const base = hubCtrl.hubHttpBase();
    if (!base) { statusBad("hub not connected"); return; }
    const wb = hubCtrl.getPlaygrounds().find(
      p => p.role === "panel" && p.name === "workbench"
    );
    if (!wb) { statusBad("workbench not running"); return; }
    const wbHtml = wb.html_file || "workbench.html";
    window.open(base + "/workbench/" + wbHtml, "workbench");
  }

  /* ---- wire modal-internal buttons once -------------------------- */

  (function wire() {
    const e = els();
    if (e.close && !e.close.__hubManageWired) {
      e.close.__hubManageWired = true;
      e.close.addEventListener("click", close);
    }
    if (e.shutdown && !e.shutdown.__hubManageWired) {
      e.shutdown.__hubManageWired = true;
      e.shutdown.addEventListener("click", handleShutdown);
    }
    if (e.wbOpen && !e.wbOpen.__hubManageWired) {
      e.wbOpen.__hubManageWired = true;
      e.wbOpen.addEventListener("click", openWorkbench);
    }
    if (e.modal && !e.modal.__hubManageWired) {
      e.modal.__hubManageWired = true;
      e.modal.addEventListener("click", (ev) => {
        if (ev.target === e.modal) close();
      });
    }
  })();

  return { open, close, render };
}
"""