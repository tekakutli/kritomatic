"""
The Kritomatic panel docker.

A QDockWidget that lives in Krita's main window and renders the panel
description pushed by the daemon. The docker is a *client* of the
daemon: it connects to the event socket and the command socket over
loopback TCP, exactly like the browser panel and the Emacs client.

Config auto-apply
=================
Config fields are not committed per-field — the daemon's
`comfy_apply_config` validates a whole config at once. Every config
field edit starts a 150 ms single-shot debounce timer; when the timer
fires, `_apply_config_from_widgets` assembles the four config sections
from the current widget state and enqueues one `comfy_apply_config`.

`comfy_panel_run` and `comfy_save_config` receive the same four
sections as additional command fields; the docker enriches them from
`build_config_from_widgets()` at click time.

Workflow bar
============
The `workflow-bar` section kind (picker + Load + Save-config) is
handled by the renderer as a single row. The docker's action handler
enriches `comfy_save_config` with config sections, same as
`comfy_panel_run`.

Pinned action row
=================
The panel's `run-actions` section (Run / Cancel / Cancel-All) is a
`kind: "actions"` section.  The docker places such sections in a
dedicated layout that sits between the scrollable form area and the
Log section, both of which are non-scrolling children of the dock's
root layout.  The effect: the run controls stay visible at all times,
regardless of how far the user has scrolled the Resources /
Parameters / Crop / Paste fields.

Mechanism: the renderer accepts an optional `actions_layout` and
routes `kind == "actions"` sections to it instead of into the
scrollable form.  The docker builds that layout (`_actions_layout`),
places it in the root layout, and passes it to the renderer.  See
"Pinned actions" in `renderer.py`'s module docstring for the full
contract.

Cancel semantics
================
Cancel targets this docker's own batch via `send_cancel_now`.
Cancel-All targets every batch on the daemon (including external
jobs registered by the inpaint playground).

Workflow hint text
==================
`_update_workflow_hint` renders a "No workflow loaded" hint when the
panel description carries no `workflow` metadata section.

Layer cache invalidation on structure change
============================================
When a panel description push carries a different `structure_id`
from the previous one, the renderer tears down its widget tree and
rebuilds it. Every `_LayerComboBox` in the new tree is created
empty — its text is set from the spec, but its item list is not
populated until `set_layers` runs with a fresh `layersFetched`.

`request_layers` deduplicates by layer signature (see
`connection.py`'s module docstring, "Layer cache invalidation").
Without intervention, a rebuild that lands between fetches would
leave the new combos empty until the daemon's layer set happened to
change — which, for the common case of workflow loading, it does
not.

`_on_panel` therefore calls `DaemonConnection.invalidate_layers_cache`
whenever `structure_id` changed, forcing the next `request_layers`
to emit. The watcher (already scheduled on every panel push) then
fires, the fetch emits, and `set_layers` repopulates the new combos.
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

        # Pinned action row container.  The renderer routes
        # `kind == "actions"` sections here instead of into the
        # scrollable form layout, so Run / Cancel / Cancel-All stay
        # visible at all times (see the module docstring, "Pinned
        # action row").  When the layout is empty, the container has
        # zero natural height and simply does not take space.
        self._actions_container = QWidget()
        self._actions_layout = QVBoxLayout(self._actions_container)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(0)
        root_layout.addWidget(self._actions_container)

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
            actions_layout=self._actions_layout,
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
        structure_changed = old_structure != new_structure

        self._update_workflow_hint(description)
        self._update_run_buttons()

        if structure_changed:
            # The renderer just tore down and rebuilt its widget tree.
            # Every _LayerComboBox in the new tree was constructed
            # empty — its *text* was set from the spec, but the item
            # list is only populated by set_layers, which only runs on
            # a layersFetched emit. If the daemon's layer set is
            # unchanged since the last emit, request_layers's
            # signature dedup would skip the emit and leave the new
            # combos empty. Invalidating forces the next fetch to
            # emit regardless of signature, so set_layers repopulates
            # them. See this module's docstring, "Layer cache
            # invalidation on structure change".
            try:
                self._conn.invalidate_layers_cache()
            except Exception:
                pass

        try:
            self._layers_watcher.schedule()
        except Exception:
            pass

        if structure_changed and self._workflow_loaded:
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
            if not ok:
                msg = summary.get("message", "")
                self._append_log("err", f"config rejected: {msg}")
            return

        if request_id.startswith("save_config-"):
            if ok:
                msg = summary.get("message", "") or "saved"
                self._append_log("ok", msg)
            else:
                msg = summary.get("message", "")
                self._append_log("err", f"Save failed: {msg}")
            return

        if request_id.startswith("action-load-"):
            if ok:
                msg = summary.get("message", "") or "workflow loaded"
                self._append_log("ok", msg)
            else:
                msg = summary.get("message", "")
                self._append_log("err", f"Load failed: {msg}")
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
            self._pending_apply_source = key
            self._pending_apply_timer.start()
            return

        request_id = f"field-{key}"
        bundle = {"id": request_id, "commands": [command]}
        self._conn.enqueue(request_id, bundle)

    def _flush_pending_apply(self) -> None:
        source = self._pending_apply_source or "auto"
        self._pending_apply_source = ""
        self._apply_config_from_widgets(source)

    def _apply_config_from_widgets(self, source_key: str) -> None:
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

        # comfy_panel_run and comfy_save_config both need the four
        # v2 config sections, assembled from config widgets at click
        # time. See the module docstring.
        if command.get("type") in ("comfy_panel_run", "comfy_save_config"):
            cfg = self._renderer.build_config_from_widgets()
            command["resources"] = json.dumps(cfg["resources"])
            command["params"] = json.dumps(cfg["params"])
            command["crop"] = json.dumps(cfg["crop"])
            command["paste"] = json.dumps(cfg["paste"])

        if key == "run":
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

        elif key == "save_config":
            request_id = f"save_config-{int(time.time() * 1000)}"
            bundle = {"id": request_id, "commands": [command]}
            self._conn.enqueue(request_id, bundle)
            self._append_log("hdr", "Saving config…")

        else:
            request_id = f"action-{key}-{int(time.time() * 1000)}"
            bundle = {"id": request_id, "commands": [command]}
            self._conn.enqueue(request_id, bundle)
            self._append_log("hdr", f"Action {key} sent.")

    def _update_run_buttons(self) -> None:
        self._renderer.set_action_enabled(
            "run", not self._running and self._workflow_loaded)
        self._renderer.set_action_enabled("cancel", self._running)
        any_batch = self._running or self._active_batches_count > 0
        self._renderer.set_action_enabled("cancel_all", any_batch)
        self._renderer.set_action_enabled("load", not self._running)
        self._renderer.set_action_enabled(
            "save_config", self._workflow_loaded and not self._running)

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
