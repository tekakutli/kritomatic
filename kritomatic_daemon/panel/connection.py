"""
Socket client for the daemon's event and command streams.

One persistent event connection (port 12347) that forwards every push
as a Qt signal. One command worker thread that drains a queue of
requests, each on its own fresh connection (port 12346).

Everything runs on plain Python threads and communicates with the
GUI thread through Qt signals. No widget is ever touched off the
GUI thread.

The single command worker is by design: it serialises state-mutating
commands so an edit is committed before the next command reaches the
daemon. That is what makes "edit a parameter then click Run" reliable.

The corollary is that a command sent while the worker is busy waits.
For long-running commands (comfy_generate holds the worker for the
whole duration of the run), a queued follow-up would never be
delivered until the run finished — which is exactly wrong for a
cancel or for a read.

Two methods therefore bypass the queue and open their own socket
per call:

  send_cancel_now(batch_id, request_id)
  request_layers()

Both run on their own background thread, send one command, read one
line, and close. The daemon's command server is per-connection and
stateless across calls, so a fresh socket is safe, and the run's own
socket is undisturbed.

`request_layers` sends two commands — `list_layers --all` and
`comfy_list_recent_aliases` — on **two separate sockets**, one after
the other. That is intentional: the daemon's socket server's inner
loop parses each received chunk as one JSON object, so two commands
concatenated on one socket would fail to parse. Two sequential
sockets avoid the problem with a bounded, negligible cost on
localhost.

There is deliberately no `request_batches()` here. The docker used
to poll `comfy_batches` on events to keep the Cancel-All button's
precondition fresh, but that is unnecessary now: the daemon
publishes a `state` event at batch *start* (see
`handlers/comfyui.py` — `_publish_state_change` is called at batch
registration) and again at batch *end* (via `execute()`'s wrapper
when `comfy_generate` returns). The `active_batches` field in those
state events is the sole source of truth for "is anything running
anywhere", and the docker's `_on_state` handler reads it directly.
No fetch, no timer.
"""

from __future__ import annotations

import json
import queue
import socket
import threading

from PyQt6.QtCore import QObject, pyqtSignal

from ..utils.layer_types import IMAGE_LAYER_TYPES, MASK_LAYER_TYPES


EVENT_HOST = "localhost"
EVENT_PORT = 12347
COMMAND_HOST = "localhost"
COMMAND_PORT = 12346

RECONNECT_DELAY = 2.0


class DaemonConnection(QObject):
    """Client for the daemon's event and command sockets.

    Signals (all emitted from worker threads; Qt delivers them queued
    to the GUI thread):

      stateReceived(dict)          — comfy_status state snapshots
      panelReceived(dict)          — panel description updates
      runEventReceived(dict)       — progress entries from our own commands
      connectionStateChanged(bool) — event socket up/down
      commandCompleted(str, bool, dict)
                                   — (request_id, ok, summary)
      layersFetched(dict)          — {"image": [...], "mask": [...],
                                      "recent": [...]}
    """

    stateReceived = pyqtSignal(dict)
    panelReceived = pyqtSignal(dict)
    runEventReceived = pyqtSignal(dict)
    connectionStateChanged = pyqtSignal(bool)
    commandCompleted = pyqtSignal(str, bool, dict)
    layersFetched = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._command_queue: queue.Queue = queue.Queue()
        self._event_thread = None
        self._command_thread = None
        self._shutting_down = False

        self._last_layers: tuple = ()

    # -- lifecycle --

    def start(self) -> None:
        self._stop_event.clear()
        self._shutting_down = False
        self._event_thread = threading.Thread(
            target=self._event_loop,
            name="kritomatic-panel-event",
            daemon=True,
        )
        self._command_thread = threading.Thread(
            target=self._command_loop,
            name="kritomatic-panel-cmd",
            daemon=True,
        )
        self._event_thread.start()
        self._command_thread.start()

    def shutdown(self) -> None:
        self._shutting_down = True
        self._stop_event.set()
        self._command_queue.put(None)

    def enqueue(self, request_id: str, bundle: dict) -> None:
        if self._shutting_down:
            return
        self._command_queue.put((request_id, bundle))

    # -- out-of-band single-command fetches --

    @staticmethod
    def _fetch_one(command_dict: dict, timeout: float = 5.0):
        """Open a fresh socket, send one command, read one line,
        close, return the parsed reply or None.

        The daemon's socket server parses each incoming chunk as one
        JSON object; one command per socket is what it expects. See
        the module docstring for the two-command case.
        """
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((COMMAND_HOST, COMMAND_PORT))
            sock.settimeout(None)
            sock.sendall(
                (json.dumps(command_dict) + "\n").encode("utf-8")
            )
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(65536)
                if not chunk:
                    return None
                buffer += chunk
            line = buffer.split(b"\n", 1)[0]
            return json.loads(line.decode("utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def send_cancel_now(self, batch_id, request_id: str) -> None:
        """Send a `comfy_cancel` on a fresh socket, on a background
        thread. Bypasses the command queue so the cancel reaches the
        daemon while a long-running `comfy_generate` is still in
        flight.

        When `batch_id` is falsy, the `batch_id` key is omitted from
        the payload, and the daemon cancels *every* active batch —
        the same path the CLI's `panel cancel --all` uses.
        """
        def _run():
            cmd = {"type": "comfy_cancel"}
            if batch_id:
                cmd["batch_id"] = batch_id
            summary = self._fetch_one(cmd)
            if summary is None:
                self.commandCompleted.emit(
                    request_id, False,
                    {"status": "error",
                     "message": "no response from daemon"},
                )
                return
            ok = summary.get("status") == "success"
            self.commandCompleted.emit(request_id, ok, summary)

        threading.Thread(
            target=_run,
            name="kritomatic-cancel",
            daemon=True,
        ).start()

    def request_layers(self) -> None:
        """Fetch the layer list and recent aliases, classify, emit
        `layersFetched({"image": [...], "mask": [...], "recent": [...]})`.

        Runs on its own background thread. Sends each command on its
        own socket (see the module docstring) so the two do not
        concatenate on one wire. Diffed against the last fetched
        payload to suppress redundant emits when the layer tree and
        the recent-aliases list are both unchanged.

        `list_layers` is called with `all=True`: the default walk
        recurses only into groups, which hides masks — a selection
        mask or a transparency mask is a child of a paint layer, not
        of a group. Without `all=True` the mask bucket is always empty
        and mask alias combos have no items.
        """
        def _run():
            layers_resp = self._fetch_one(
                {"type": "list_layers", "all": True}
            )
            recent_resp = self._fetch_one(
                {"type": "comfy_list_recent_aliases"}
            )

            image_names: list = []
            mask_names: list = []
            if layers_resp is not None:
                data = layers_resp.get("data") or {}
                for entry in (data.get("layers") or []):
                    name = entry.get("name") or ""
                    ltype = entry.get("type") or ""
                    if not name:
                        continue
                    if ltype in IMAGE_LAYER_TYPES:
                        image_names.append(name)
                    elif ltype in MASK_LAYER_TYPES:
                        mask_names.append(name)

            recent_names: list = []
            if recent_resp is not None:
                data = recent_resp.get("data") or {}
                raw = data.get("recent_aliases") or []
                if isinstance(raw, list):
                    recent_names = [str(a) for a in raw
                                    if isinstance(a, str)]

            new_signature = (
                tuple(image_names),
                tuple(mask_names),
                tuple(recent_names),
            )
            if new_signature == self._last_layers:
                return
            self._last_layers = new_signature

            self.layersFetched.emit({
                "image": image_names,
                "mask": mask_names,
                "recent": recent_names,
            })

        threading.Thread(
            target=_run,
            name="kritomatic-layers-fetch",
            daemon=True,
        ).start()

    # -- event loop --

    def _event_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_event_connection()
            except Exception:
                pass
            if self._stop_event.wait(RECONNECT_DELAY):
                return

    def _run_event_connection(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((EVENT_HOST, EVENT_PORT))
        except OSError:
            return

        try:
            self.connectionStateChanged.emit(True)
            sock.settimeout(None)
            buffer = b""
            while not self._stop_event.is_set():
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    self._dispatch_event(event)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self.connectionStateChanged.emit(False)

    def _dispatch_event(self, event: dict) -> None:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "state":
            self.stateReceived.emit(data)
        elif etype == "panel":
            self.panelReceived.emit(data)
        elif etype == "run":
            self.runEventReceived.emit(data)

    # -- command loop --

    def _command_loop(self) -> None:
        while not self._stop_event.is_set():
            item = self._command_queue.get()
            if item is None:
                return
            request_id, bundle = item
            try:
                self._run_one_command(request_id, bundle)
            except Exception as e:
                self.commandCompleted.emit(
                    request_id, False, {"error": str(e)},
                )

    def _run_one_command(self, request_id: str, bundle: dict) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((COMMAND_HOST, COMMAND_PORT))
        except OSError as e:
            self.commandCompleted.emit(
                request_id, False, {"error": f"connect failed: {e}"},
            )
            return

        summary = None
        try:
            sock.settimeout(None)
            sock.sendall((json.dumps(bundle) + "\n").encode("utf-8"))
            buffer = b""
            while True:
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    mtype = msg.get("type")
                    if mtype == "progress":
                        self.runEventReceived.emit({
                            "kind": "progress",
                            "request_id": request_id,
                            "index": msg.get("index", 0),
                            "total": msg.get("total", 0),
                            "result": msg.get("result", {}),
                        })
                    elif (mtype == "batch_complete"
                          or msg.get("status") == "batch_complete"):
                        summary = msg
                        break
                    else:
                        summary = msg
                        break
                if summary is not None:
                    break
        finally:
            try:
                sock.close()
            except OSError:
                pass

        summary = summary or {"error": "no response"}
        status = summary.get("status")
        if status == "batch_complete":
            ok = bool(summary.get("failed", 0) == 0)
        elif status == "success":
            ok = True
        else:
            ok = False
        self.commandCompleted.emit(request_id, ok, summary)
