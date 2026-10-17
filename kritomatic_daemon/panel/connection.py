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
cancel. `send_cancel_now` therefore bypasses the queue entirely: it
opens its own socket on a fresh thread, sends one `comfy_cancel`,
reads the single reply, and closes. The daemon's command server is
per-connection and stateless across calls, so nothing about the run's
own connection is disturbed.
"""

from __future__ import annotations

import json
import queue
import socket
import threading

from PyQt6.QtCore import QObject, pyqtSignal


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
    """

    stateReceived = pyqtSignal(dict)
    panelReceived = pyqtSignal(dict)
    runEventReceived = pyqtSignal(dict)
    connectionStateChanged = pyqtSignal(bool)
    commandCompleted = pyqtSignal(str, bool, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._command_queue: queue.Queue = queue.Queue()
        self._event_thread = None
        self._command_thread = None
        self._shutting_down = False

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

    def send_cancel_now(self, batch_id: str, request_id: str) -> None:
        """Send a `comfy_cancel` on a fresh socket, on a background thread.

        Bypasses the command queue so the cancel reaches the daemon
        while a long-running `comfy_generate` is still in flight on the
        queue's worker. The daemon's command server is per-connection
        and stateless across calls, so a fresh connection is safe.

        The single-command form (`{"type": "comfy_cancel", "batch_id":
        ...}`, no `commands` wrapper) is used so the daemon replies with
        exactly one line. This thread reads that line, emits
        `commandCompleted`, and exits.
        """
        def _run():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10.0)
                sock.connect((COMMAND_HOST, COMMAND_PORT))
                sock.settimeout(None)

                command = {"type": "comfy_cancel", "batch_id": batch_id}
                sock.sendall((json.dumps(command) + "\n").encode("utf-8"))

                buffer = b""
                while b"\n" not in buffer:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk

                if not buffer:
                    self.commandCompleted.emit(
                        request_id, False,
                        {"status": "error",
                         "message": "no response from daemon"},
                    )
                    return

                line = buffer.split(b"\n", 1)[0]
                try:
                    summary = json.loads(line.decode("utf-8"))
                except Exception:
                    summary = {"status": "error",
                               "message": "invalid response from daemon"}

                ok = summary.get("status") == "success"
                self.commandCompleted.emit(request_id, ok, summary)

            except OSError as e:
                self.commandCompleted.emit(
                    request_id, False,
                    {"status": "error",
                     "message": f"cancel socket failed: {e}"},
                )
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

        threading.Thread(
            target=_run,
            name="kritomatic-cancel",
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
