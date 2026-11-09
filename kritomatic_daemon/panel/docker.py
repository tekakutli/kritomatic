"""
The Kritomatic panel docker.

A QDockWidget that lives in Krita's main window and renders the panel
description pushed by the daemon. The docker is a *client* of the
daemon: it connects to the event socket and the command socket over
loopback TCP, exactly like the browser panel and the Emacs client.

The docker is on reload_daemon's keep-list. Do not move it under a
module that reload drops.

Config auto-apply
=================
Config fields (resources, params, crop, paste) are not auto-committed
per-field — the daemon's `comfy_apply_config` validates a whole config
at once, so a per-field command would either have to be a merge-into-
active-config command the daemon doesn't have, or a re-send of the
whole config on every keystroke. Instead:

  * Every config field edit starts a 150 ms single-shot debounce timer
    (`_pending_apply_timer`). Rapid edits across multiple fields
    collapse into one apply.
  * When the timer fires, `_apply_config_from_widgets` calls
    `PanelRenderer.build_config_from_widgets()` to assemble the four
    config sections from the current widget state and enqueues one
    `comfy_apply_config` command. The daemon validates, stores the
    result in `_active_config`, and re-publishes state.
  * `_on_panel` also auto-applies when the panel's `structure_id`
    changes and a workflow is loaded — this is what populates
    `_active_config` with the workflow's baked-in defaults right after
    `comfy_load`, so the playground (which reads `_active_config`)
    sees them without the user having to touch a field or click Run.

The effect: `_active_config` on the daemon is the canonical source of
truth for panel values, kept current by whichever client last edited
a config field. The docker's Run button still uses `comfy_panel_run`
(apply-then-generate) as a belt-and-braces path, but on the happy
path auto-apply has already pushed the same values, so the Run apply
is a redundant no-op. The one case where it matters is a Run click
inside the debounce window: the widget state is a hair ahead of
`_active_config`, and `comfy_panel_run`'s own apply catches up.

The alternative — no auto-apply, config only applied at Run — is what
the previous design had. It broke the "set values once, have them
respected everywhere" expectation: editing a field in the docker
updated only the docker's local widget state, `_active_config` stayed
at whatever the last Run applied (often the workflow defaults), and
any other client reading `_active_config` (the inpaint playground,
the CLI) saw stale values until the user clicked Run. Auto-apply
fixes that by making edits propagate immediately, debounced so rapid
typing doesn't flood the command queue with redundant applies.

Why `no_sync` came off config fields
====================================
An earlier design marked config fields `no_sync: true` so a daemon
re-publish could not stomp the user's in-progress edits. That design
assumed the daemon's `_active_config` and the docker's widget state
would only meet at Run time — a re-publish during editing had to be
suppressed because the daemon's view was always one apply behind.

With auto-apply, the daemon's view is at most one debounce window
behind, and usually identical to the widget state. Sync-ing config
fields from `_active_config` is safe (the renderer's focus-check
protects the field the user is typing in) and useful: when another
client (CLI `panel run`, a second docker, the playground's own
`comfy_apply_config`) applies a config, the docker's widgets reflect
it on the next re-publish instead of silently drifting. The previous
"docker overwrites everyone else with its stale widget state" bug is
gone — with sync restored, the docker's widgets and `_active_config`
agree, so a docker Run is a re-apply of the current truth, not an
overwrite of someone else's.

Run button and the v2 config
============================
The Run action builds its payload from two sources:

  * **Collected run-option fields** (timeout, count, seed) — via the
    item's `collect` mapping.

  * **The config widgets** — via `PanelRenderer.build_config_from_
    widgets()`, which walks every field carrying a `config_path` and
    assembles the four-section v2 config (resources, params, crop,
    paste).

The command the docker sends is `comfy_panel_run`, which the daemon
handles by running `comfy_connect` (auto-starting the proxy), then
`comfy_apply_config` (validating the assembled config against the
loaded graph's slot tables), then `comfy_generate`. The whole thing is
atomic: if connect or apply fails, generate is not attempted.

Run button enablement
=====================
Run is disabled until a workflow is loaded. The docker tracks
`_workflow_loaded` from the `state` event's `workflow` field. Clicking
Run with no workflow loaded would fail at `comfy_apply_config` with
"No workflow loaded; run comfy_load first", which is a clear message
but a worse UX than just not offering the button.

The workflow picker's dropdown (from `WorkflowPickerProvider`)
commits `comfy_load --name <value>` on selection. Once the daemon
publishes the post-load state, `_workflow_loaded` becomes True and Run
enables. The docker also auto-applies config on the same event, so
`_active_config` is populated with the workflow's baked-in defaults
before the user can click Run.

Cancel and the command queue
============================
The command worker in DaemonConnection is single-threaded by design:
it serialises state-mutating commands so a field edit is committed
before the next command reaches the daemon. That is what makes "edit
a parameter then click Run" reliable.

`comfy_generate` (and therefore `comfy_panel_run`, which wraps it)
holds the worker for the entire duration of the run, though — its
`batch_complete` message does not arrive until the run finishes. A
queued follow-up (the cancel request) would sit behind it and reach
the daemon only after the run completed, at which point the cancel
has nothing to target.

Cancel therefore uses `DaemonConnection.send_cancel_now`, which opens
its own socket on a fresh thread and sends `comfy_cancel` directly.
The daemon receives it while the run is still in flight, sets the
batch's cancel flag, and the run winds itself down.

The same path is used by the thin "ALL" companion next to Cancel,
but with a `None` batch id, which the daemon interprets as
"cancel every active batch". That includes batches started from
another client (the CLI, the inpaint playground's registered external
jobs). That is exactly the `panel cancel --all` behaviour from the
CLI, exposed here as a click.

Cancel-All availability (cross-client, no polling)
==================================================
Cancel-All's precondition is not "this docker has a run in flight"
— it is "any client has a batch in flight". The daemon publishes a
`state` event at every batch boundary:

  * at batch *start* (see `handlers/comfyui.py` —
    `_publish_state_change` is called when the batch is registered),
  * at batch *end* (via `execute()`'s wrapper when `comfy_generate`
    returns),
  * and on connect (the event server sends a `state` snapshot as its
    first write to every new subscriber).

Each of those pushes carries `active_batches: [...]`, the live list
of running batches across every client. The docker's `_on_state`
reads that field and stores its length in `_active_batches_count`.
`_update_run_buttons` OR's it with `self._running` and calls
`set_action_enabled("cancel_all", …)` accordingly.

External jobs (the inpaint playground) are registered with the daemon
via `comfy_register_external_job`, so they appear in `active_batches`
and can be cancelled through the same Cancel-All button. See
`handlers/comfyui_generation.comfy_cancel` for the forwarding to
`ProxyV2.cancel_job`.

No `comfy_batches` fetch, no timer, no interval. The state event
stream is the sole source of truth.

Layer list freshness
====================
`LayersWatcher` triggers `request_layers` on: notifier image events,
panel events, docker focus, combo popup, and reconnect. All collapse
into one debounced `layersChanged`. No polling. In the v2 panel no
built-in provider emits `_LayerComboBox` fields, so the layer list
mostly sits dormant, but the plumbing stays for any future provider
that wants alias dropdowns.

Workflow hint text
==================
`_update_workflow_hint` renders a "No workflow loaded" hint when the
panel description carries no `workflow` metadata section. The hint
names the two CLI entry points that load a workflow:

    kritomatic comfyui comfy_load --name <slug>
    kritomatic comfyui panel run <slug>

...and also mentions the workflow picker text field, which is the
in-docker path.
"""

from __future__ import annotations

import json
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QScrollArea, QLabel,
    QPlainTextEdit, QFrame,
)

from .connection import DaemonConnection
from .layers_watcher import LayersWatcher
from .renderer import PanelRenderer


# Debounce interval for config auto-apply. Long enough to collapse a
# burst of edits across multiple fields (the user tabbing through the
# form, or a paste that fires several `editingFinished` events in
# quick succession) into one `comfy_apply_config` command, short enough
# that a single deliberate edit reaches the daemon well before the
# user could plausibly reach for the Run button.
_CONFIG_APPLY_DEBOUNCE_MS = 150


class KritomaticPanel(QDockWidget):

    def __init__(self, parent=None):
        super().__init__("Kritomatic", parent)
        self.setObjectName("KritomaticPanel")
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )

        self._running = False
        self._active_run_request_id = None
        self._active_batches_count = 0
        self._workflow_loaded = False

        # Config auto-apply: see the module docstring. Every config
        # field edit restarts this timer; when it fires, the docker
        # assembles the current widget state and sends one
        # `comfy_apply_config`. The timer is single-shot so a rapid
        # series of edits produces exactly one apply.
        self._pending_apply_timer = QTimer(self)
        self._pending_apply_timer.setSingleShot(True)
        self._pending_apply_timer.setInterval(_CONFIG_APPLY_DEBOUNCE_MS)
        self._pending_apply_timer.timeout.connect(self._flush_pending_apply)
        self._pending_apply_source = ""

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        self._status = QLabel("Connecting to daemon…")
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.RichText)
        root_layout.addWidget(self._status)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setTextFormat(Qt.TextFormat.RichText)
        self._hint.setStyleSheet(
            "color:#a05a00;font-style:italic;font-size:0.85em;"
            "padding:2px 0;"
        )
        self._hint.hide()
        root_layout.addWidget(self._hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._form_container = QWidget()
        self._form_layout = QVBoxLayout(self._form_container)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setSpacing(6)
        self._form_layout.addStretch(1)
        self._scroll.setWidget(self._form_container)
        root_layout.addWidget(self._scroll, 1)

        log_label = QLabel("Log")
        log_label.setStyleSheet(
            "color:#888;font-size:0.72rem;"
            "text-transform:uppercase;letter-spacing:0.05em;"
        )
        root_layout.addWidget(log_label)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)
        self._log.setFixedHeight(140)
        root_layout.addWidget(self._log)

        self.setWidget(root)

        self._renderer = PanelRenderer(
            self._form_layout,
            self._on_field_commit,
            self._on_action,
        )

        self._conn = DaemonConnection()
        self._conn.stateReceived.connect(self._on_state)
        self._conn.panelReceived.connect(self._on_panel)
        self._conn.runEventReceived.connect(self._on_run_event)
        self._conn.connectionStateChanged.connect(self._on_conn_state)
        self._conn.commandCompleted.connect(self._on_command_completed)
        self._conn.layersFetched.connect(self._on_layers_fetched)
        self._conn.start()

        self._layers_watcher = LayersWatcher(self)
        self._layers_watcher.layersChanged.connect(self._on_layers_changed)
        self._renderer.layerRefreshRequested.connect(
            self._layers_watcher.schedule)
        self._conn.request_layers()
        self._update_run_buttons()

    # ---- lifecycle ----

    def closeEvent(self, event):
        try:
            self._pending_apply_timer.stop()
        except Exception:
            pass
        try:
            self._layers_watcher.stop()
        except Exception:
            pass
        try:
            self._conn.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        try:
            self._layers_watcher.schedule()
        except Exception:
            pass

    # ---- connection signals ----

    def _on_conn_state(self, connected: bool) -> None:
        if connected:
            self._status.setText("Connected to daemon.")
            try:
                self._layers_watcher.schedule()
            except Exception:
                pass
        else:
            self._status.setText("Waiting for daemon event stream…")
            self._active_batches_count = 0
            self._workflow_loaded = False
        self._update_run_buttons()

    def _on_state(self, data: dict) -> None:
        try:
            batches = data.get("active_batches") or []
            self._active_batches_count = len(batches)
            self._workflow_loaded = bool(data.get("workflow"))
        except Exception:
            pass
        self._update_run_buttons()

    def _on_panel(self, description: dict) -> None:
        old_structure = self._renderer._structure_id
        try:
            self._renderer.render(description)
        except Exception as e:
            self._append_log("err", f"render error: {e}")
        new_structure = self._renderer._structure_id
        self._update_workflow_hint(description)
        self._update_run_buttons()
        try:
            self._layers_watcher.schedule()
        except Exception:
            pass

        # Structure change + workflow loaded → this is a fresh load
        # (or the docker's first panel after booting with a workflow
        # already in `_active_config`). Auto-apply so `_active_config`
        # is populated with whatever defaults the freshly-built widgets
        # show. This is what makes the playground see the workflow's
        # baked-in param values without the user having to touch a
        # field or click Run first.
        if old_structure != new_structure and self._workflow_loaded:
            self._apply_config_from_widgets("auto_load")

    def _on_layers_changed(self) -> None:
        self._conn.request_layers()

    def _on_layers_fetched(self, layers: dict) -> None:
        try:
            self._renderer.set_layers(layers)
        except Exception as e:
            self._append_log("err", f"layers update error: {e}")

    def _update_workflow_hint(self, description) -> None:
        has_workflow = False
        if isinstance(description, dict):
            sections = description.get("sections")
            if isinstance(sections, list):
                for s in sections:
                    if isinstance(s, dict) and s.get("id") == "workflow":
                        has_workflow = True
                        break
        if has_workflow:
            self._hint.setText("")
            self._hint.hide()
        else:
            self._hint.setText(
                "No workflow loaded. Type a name in the Workflow field "
                "above and press Enter, or run "
                "<code>kritomatic comfyui comfy_load --name "
                "&lt;name&gt;</code> / <code>kritomatic comfyui panel "
                "run &lt;name&gt;</code> from a terminal."
            )
            self._hint.show()

    def _on_run_event(self, event: dict) -> None:
        if event.get("kind") != "progress":
            return
        result = event.get("result") or {}
        idx = event.get("index", 0)
        total = event.get("total", 0)
        cmd = result.get("command", "?")
        status = result.get("status", "?")
        message = result.get("message", "")
        cls = "ok" if status == "success" else "err"
        self._append_log(cls, f"[{idx + 1}/{total}] {cmd}: {message}")

    def _on_command_completed(self, request_id: str, ok: bool,
                              summary: dict) -> None:
        if request_id.startswith(("cancel-", "cancel_all-")):
            msg = summary.get("message", "")
            if ok:
                self._append_log("warn", f"Cancel sent: {msg}")
            else:
                self._append_log("err", f"Cancel failed: {msg}")
            return

        if request_id.startswith("apply-"):
            # Auto-apply result. Success: silent (the widget state
            # already reflects what was applied). Failure: log so the
            # user knows their config edit was rejected (typically an
            # intermediate invalid state, e.g. source=layer without a
            # layer_name — the widget stays where they left it and the
            # next valid edit re-applies).
            if not ok:
                msg = summary.get("message", "")
                self._append_log("err", f"config rejected: {msg}")
            return

        if request_id != self._active_run_request_id:
            return

        self._active_run_request_id = None
        self._running = False

        results = summary.get("results", []) or []
        cancelled_count = sum(
            1 for r in results
            if r.get("status") == "error"
            and "cancelled" in (r.get("message") or "").lower()
        )
        succ = summary.get("successful", 0)
        fail = summary.get("failed", 0)

        if cancelled_count > 0 and succ == 0:
            self._append_log(
                "warn",
                f"Run cancelled. {cancelled_count} run(s) not completed.",
            )
        elif summary.get("status") == "batch_complete":
            self._append_log(
                "hdr", f"Run done: {succ} ok, {fail} failed."
            )
        else:
            self._append_log("hdr", "Run done.")
        self._update_run_buttons()

    # ---- command submission ----

    def _on_field_commit(self, key: str, command: dict) -> None:
        if command.get("type") == "comfy_apply_config_from_widgets":
            # Config field edit: debounce, then apply the whole config.
            # Rationale and debounce interval are documented in the
            # module docstring, "Config auto-apply".
            self._pending_apply_source = key
            self._pending_apply_timer.start()
            return

        request_id = f"field-{key}"
        bundle = {"id": request_id, "commands": [command]}
        self._conn.enqueue(request_id, bundle)

    def _flush_pending_apply(self) -> None:
        """Debounce timeout: one apply for the accumulated edit burst."""
        source = self._pending_apply_source or "auto"
        self._pending_apply_source = ""
        self._apply_config_from_widgets(source)

    def _apply_config_from_widgets(self, source_key: str) -> None:
        """Assemble the current widget state and send comfy_apply_config.

        Called from two places:
          * `_flush_pending_apply`, after the debounce timer fires
            following a config field edit.
          * `_on_panel`, when the panel structure changes and a
            workflow is loaded (this populates `_active_config` with
            the workflow's baked-in defaults right after comfy_load).

        Both paths send the same command shape: the four config
        sections serialized as JSON, one command, one batch.
        """
        try:
            cfg = self._renderer.build_config_from_widgets()
        except Exception as e:
            self._append_log("err", f"config build failed: {e}")
            return
        command = {
            "type": "comfy_apply_config",
            "resources": json.dumps(cfg["resources"]),
            "params": json.dumps(cfg["params"]),
            "crop": json.dumps(cfg["crop"]),
            "paste": json.dumps(cfg["paste"]),
        }
        request_id = f"apply-{source_key}-{int(time.time() * 1000)}"
        bundle = {"id": request_id, "commands": [command]}
        self._conn.enqueue(request_id, bundle)

    def _on_action(self, item: dict, values: dict) -> None:
        key = item.get("key") or ""
        command = dict(item.get("command") or {})
        for fk, argname in (item.get("collect") or {}).items():
            if fk in values:
                v = values[fk]
                if argname == "seed":
                    if v not in (None, ""):
                        command[argname] = v
                    continue
                if argname == "output_name":
                    if v not in (None, ""):
                        command[argname] = v
                    continue
                if argname == "count":
                    try:
                        iv = int(v)
                    except (TypeError, ValueError):
                        iv = 1
                    if iv > 1:
                        command[argname] = iv
                    continue
                if argname == "timeout":
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    command[argname] = fv
                    continue
                command[argname] = v

        # comfy_panel_run needs the four v2 config sections, which the
        # docker assembles from its config widgets at click time. The
        # module docstring, "Run button and the v2 config", explains
        # why this is a redundant confirmation on the happy path and a
        # necessary catch-up when Run is clicked inside the debounce
        # window.
        if command.get("type") == "comfy_panel_run":
            cfg = self._renderer.build_config_from_widgets()
            command["resources"] = json.dumps(cfg["resources"])
            command["params"] = json.dumps(cfg["params"])
            command["crop"] = json.dumps(cfg["crop"])
            command["paste"] = json.dumps(cfg["paste"])

        if key == "run":
            # Cancel any pending auto-apply so we don't double-send the
            # same config just because the user typed and then clicked
            # Run inside the debounce window. The Run command's own
            # apply covers the widget state; the pending timer would
            # have fired with identical values.
            self._pending_apply_timer.stop()
            self._pending_apply_source = ""

            request_id = f"run-{int(time.time() * 1000)}"
            self._active_run_request_id = request_id
            self._running = True
            bundle = {"id": request_id, "commands": [command]}
            self._conn.enqueue(request_id, bundle)
            self._append_log("hdr", "Run started.")
            self._update_run_buttons()

        elif key == "cancel":
            if not self._active_run_request_id:
                self._append_log(
                    "warn", "No run in progress to cancel.",
                )
                return
            request_id = f"cancel-{int(time.time() * 1000)}"
            self._conn.send_cancel_now(
                self._active_run_request_id, request_id,
            )

        elif key == "cancel_all":
            request_id = f"cancel_all-{int(time.time() * 1000)}"
            self._conn.send_cancel_now(None, request_id)

        else:
            request_id = f"action-{key}-{int(time.time() * 1000)}"
            bundle = {"id": request_id, "commands": [command]}
            self._conn.enqueue(request_id, bundle)
            self._append_log("hdr", f"Action {key} sent.")

    def _update_run_buttons(self) -> None:
        # Run: enabled only when this docker is idle AND a workflow is
        # loaded. Clicking Run with no workflow loaded would fail at
        # comfy_apply_config; disabling it is cleaner UX.
        self._renderer.set_action_enabled(
            "run", not self._running and self._workflow_loaded)
        # Cancel: targets this docker's own batch. Enabled strictly
        # when this docker has one in flight — it has nothing to do
        # otherwise.
        self._renderer.set_action_enabled("cancel", self._running)
        # Cancel-All: targets every batch on the daemon, including
        # external jobs registered by the playground. Enabled when
        # either this docker has a local run (covers the brief window
        # before the first state push arrives) or the daemon reports
        # any active batch from any client.
        any_batch = self._running or self._active_batches_count > 0
        self._renderer.set_action_enabled("cancel_all", any_batch)

    def _append_log(self, kind: str, text: str) -> None:
        color = {
            "ok": "#0d652d",
            "err": "#c5221f",
            "warn": "#a05a00",
            "hdr": "#333",
        }.get(kind, "#333")
        weight = "bold" if kind == "hdr" else "normal"
        safe = (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
        self._log.appendHtml(
            f'<span style="color:{color};font-weight:{weight};'
            f'font-family:monospace;">{safe}</span>'
        )
