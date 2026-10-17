"""
The Kritomatic panel docker.

A QDockWidget that lives in Krita's main window and renders the panel
description pushed by the daemon. The docker is a *client* of the
daemon: it connects to the event socket and the command socket over
loopback TCP, exactly like the browser panel and the Emacs client.

The docker is on reload_daemon's keep-list. Do not move it under a
module that reload drops.

Cancel and the command queue
============================
The command worker in DaemonConnection is single-threaded by design:
it serialises state-mutating commands so a field edit is committed
before the next command reaches the daemon. That is what makes "edit
a parameter then click Run" reliable.

`comfy_generate` holds the worker for the entire duration of the run,
though — its `batch_complete` message does not arrive until the run
finishes. A queued follow-up (the cancel request) would sit behind it
and reach the daemon only after the run completed, at which point the
cancel has nothing to target.

Cancel therefore uses `DaemonConnection.send_cancel_now`, which opens
its own socket on a fresh thread and sends `comfy_cancel` directly.
The daemon receives it while the run is still in flight, sets the
batch's cancel flag, and the run winds itself down.

The same path is used by the thin "ALL" companion next to Cancel,
but with a `None` batch id, which the daemon interprets as
"cancel every active batch". That is exactly the `panel cancel
--all` behaviour from the CLI, exposed here as a click.

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

No `comfy_batches` fetch, no timer, no interval. The state event
stream is the sole source of truth. This matches the web panel's
approach exactly: both clients derive the button's enabled state
from the same channel the daemon already uses to publish workflow
state.

Ordering note: a local batch's `batch_complete` message (arriving on
the command socket) and the end-of-batch `state` push (arriving on
the event socket) are on two different sockets and may land in
either order. Both orderings are safe:

  * state first: `_on_state` sees `active_batches=[]`, but
    `_running` is still true, so both Cancel and Cancel-All remain
    enabled until `_on_command_completed` clears `_running`. No
    flicker.

  * completion first: `_on_command_completed` clears `_running`, so
    Cancel disables. Cancel-All stays enabled on the stale count
    from the previous state push until the new one arrives
    (milliseconds later). If the user clicks in that window, the
    daemon's `comfy_cancel` (no `batch_id`) returns
    "Cancel requested (nothing was running)" — a harmless no-op.

Layer list freshness
====================
The alias combos need a current layer list. Krita provides no signal
for layer-tree changes (see layers_watcher.py for the full account),
so `LayersWatcher` uses five event-driven triggers:

  1. Notifier imageCreated/imageClosed (inside LayersWatcher).
  2. Panel events — this file schedules a refresh from `_on_panel`.
  3. Docker focusInEvent — this file schedules a refresh directly.
  4. Combo showPopup — `PanelRenderer.layerRefreshRequested` routes
     to `LayersWatcher.schedule`.
  5. Event socket connected — this file schedules a refresh from
     `_on_conn_state(True)`.

All five collapse into one debounced `layersChanged`, which routes to
`DaemonConnection.request_layers`. There is no polling.

The one gap, named in layers_watcher.py, is a native-Krita layer add
performed while no trigger fires and no other interaction happens;
the next focus or popup open refreshes.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QScrollArea, QLabel,
    QPlainTextEdit, QFrame,
)

from .connection import DaemonConnection
from .layers_watcher import LayersWatcher
from .renderer import PanelRenderer


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
            # No connection, no batches known to us. Resetting to 0
            # disables Cancel-All honestly rather than leaving it lit
            # against stale data. The event server re-sends a full
            # `state` snapshot on reconnect, so `_on_state` restores
            # the correct value automatically.
            self._active_batches_count = 0
        self._update_run_buttons()

    def _on_state(self, data: dict) -> None:
        # `active_batches` is the daemon's live list of running
        # batches, across all clients. The daemon publishes a state
        # event at every batch boundary (see the module docstring's
        # "Cancel-All availability" section), so this is always
        # current — no polling, no fetch.
        try:
            batches = data.get("active_batches") or []
            self._active_batches_count = len(batches)
        except Exception:
            pass
        self._update_run_buttons()

    def _on_panel(self, description: dict) -> None:
        try:
            self._renderer.render(description)
        except Exception as e:
            self._append_log("err", f"render error: {e}")
        self._update_workflow_hint(description)
        self._update_run_buttons()
        try:
            self._layers_watcher.schedule()
        except Exception:
            pass

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
                "No workflow loaded. Run "
                "<code>kritomatic comfyui panel run &lt;name&gt;</code> "
                "or publish from ComfyUI&rsquo;s browser."
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
            # No fetch here. If the cancel actually stopped a batch,
            # the daemon's end-of-batch state push will update
            # `_active_batches_count` when it fires. If it was a
            # cancel-all against another client's batch, the same
            # applies — the state push fires from `execute()`'s
            # wrapper when that batch returns.
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
        request_id = f"field-{key}"
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

        if key == "run":
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
            # Same out-of-band path as `cancel`, but with no batch id,
            # so the daemon cancels every active batch — including
            # ones launched from the CLI or another panel — and any
            # async generates.
            request_id = f"cancel_all-{int(time.time() * 1000)}"
            self._conn.send_cancel_now(None, request_id)

        else:
            request_id = f"action-{key}-{int(time.time() * 1000)}"
            bundle = {"id": request_id, "commands": [command]}
            self._conn.enqueue(request_id, bundle)
            self._append_log("hdr", f"Action {key} sent.")

    def _update_run_buttons(self) -> None:
        # Run: enabled when this docker is idle.
        self._renderer.set_action_enabled("run", not self._running)
        # Cancel: targets this docker's own batch. Enabled strictly
        # when this docker has one in flight — it has nothing to do
        # otherwise.
        self._renderer.set_action_enabled("cancel", self._running)
        # Cancel-All: targets every batch on the daemon. Enabled when
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
