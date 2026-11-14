"""
Socket client for the daemon's event and command streams.

One persistent event connection (port 12347) that forwards every push
as a Qt signal. One command worker thread that drains a queue of
requests, each on its own fresh connection (port 12346).

Everything runs on plain Python threads and communicates with the
GUI thread through Qt signals. No widget is ever touched off the
GUI thread.

Why the serial worker (and when it is bypassed)
===============================================
The single command worker is by design: it serialises state-mutating
commands so an edit is committed before the next command reaches the
daemon. That is what makes "edit a parameter then click Run" reliable.

The corollary is that a command sent while the worker is busy waits.
For long-running commands (`comfy_generate` holds the worker for the
whole duration of the run), a queued follow-up would never be
delivered until the run finished — which is exactly wrong for a
cancel, for a read, or for the user's second Run click.

Three methods therefore bypass the queue and open their own socket
per call:

  send_run_now(request_id, bundle)      fire a run on its own thread
                                         and socket; streams progress
                                         and the batch_complete summary
                                         back through the same Qt
                                         signals the serial worker
                                         uses.
  send_cancel_now(batch_id, request_id)  fire a cancel; single reply
                                         line.
  request_layers()                       two commands, two sockets;
                                         the daemon's inner loop parses
                                         each chunk as one JSON object,
                                         so two commands concatenated on
                                         one socket would fail to parse.

Each runs on its own background thread, so N concurrent calls to any
of them do not interfere. `send_run_now` in particular is what makes
"click Run twice" work: each click fires a bundle on its own socket,
the daemon's per-connection handler spawns a handler thread for each,
and the two runs proceed concurrently.

There is deliberately no `request_batches()` here. The daemon
publishes a `state` event at batch *start* and again at batch *end*,
and the `active_batches` field in those state events is the sole
source of truth for "is anything running anywhere". The docker's
`_on_state` handler reads it directly.

Layer cache invalidation
========================
`request_layers` deduplicates fetches: if the layer signature
(`image` tuple, `mask` tuple, `recent` tuple) matches `_last_layers`,
it returns without emitting `layersFetched`. That suppresses noisy
re-emits when the watcher fires spuriously.

The dedup has one blind spot: when the panel's widget tree is
rebuilt (a description push whose `structure_id` changed), every
`_LayerComboBox` is destroyed and recreated with no items — the
combo's *text* is set from the spec, but the dropdown has nothing
to offer. If the daemon's layer set happens to be unchanged since
the last emit (the common case: rebuilds are triggered by workflow
loads, not by layer edits), `request_layers` skips the emit and
`set_layers` never runs against the new combos — they stay empty
until the layer set actually changes.

`invalidate_layers_cache` fixes that: it resets `_last_layers` to
its sentinel initial value (the empty tuple), which can never equal
a real signature, so the next fetch always emits. The docker calls
it from `_on_panel` whenever `structure_id` changes, right before
scheduling the watcher, so the next fetch is guaranteed to
repopulate the new combos.
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
      runEventReceived(dict)       — progress entries from any running
                                     command (serial worker or
                                     send_run_now); `request_id` in the
                                     payload identifies which run
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

        # Deduplication state for request_layers. The empty tuple is
        # the sentinel initial value; any real signature is a 3-tuple
        # of tuples, which never compares equal to the bare empty
        # tuple. `invalidate_layers_cache` resets to this sentinel to
        # force the next fetch to emit.
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
        JSON object; one command per socket is what it expects.
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

    def send_run_now(self, request_id: str, bundle: dict) -> None:
        """Fire a run bundle on its own thread and socket, bypassing
        the serial command worker.

        Runs a `{"id": ..., "commands": [...]}` bundle exactly the way
        the serial worker would, but without queueing. This is what
        lets the docker user click Run multiple times in quick
        succession: each click calls this method once, and N concurrent
        calls run on N threads with N sockets.

        Delegates to `_run_one_command`, which already implements the
        protocol: send the bundle, stream `progress` messages through
        `runEventReceived`, and terminate with one `commandCompleted`
        carrying the `batch_complete` summary. The only difference
        between this and the serial worker's path is that the call
        happens on a fresh thread instead of through the queue.

        The daemon accepts concurrent connections (one handler thread
        per inbound socket), so the two runs proceed concurrently on
        the daemon side too; see `server/socket_server.py`.
        """
        threading.Thread(
            target=self._run_one_command,
            args=(request_id, bundle),
            name=f"kritomatic-run-{request_id}",
            daemon=True,
        ).start()

    def invalidate_layers_cache(self) -> None:
        """Force the next `request_layers` to emit even if the daemon's
        layer set matches the last successful fetch.

        Called by the docker when the panel's widget tree is rebuilt
        (a description push whose `structure_id` changed). The rebuild
        destroys every `_LayerComboBox` and recreates them empty; if
        the daemon's layer set hasn't changed since the last emit,
        `request_layers`'s signature dedup would skip the emit and
        `set_layers` would never repopulate the new combos. Resetting
        `_last_layers` to its sentinel initial value (the empty tuple)
        forces the next fetch to emit.

        Thread-safe: `_last_layers` is written by `request_layers`
        on its own background thread and read here from the GUI
        thread. The comparison is against a fresh tuple from the
        next fetch, so a transient torn read (Python doesn't have
        them, but consider the abstract hazard) can only cause one
        spurious re-emit, never a missed population. The failure
        mode of a wrong read is "one extra fetch" — benign.
        """
        self._last_layers = ()

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

        Emits are also forced when `_last_layers` was reset by
        `invalidate_layers_cache` — the comparison against the
        sentinel empty tuple always fails, so the fetch emits
        unconditionally. See the module docstring, "Layer cache
        invalidation".
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
        """Send one bundle on a fresh socket, stream progress, emit
        the summary.

        Runs on the serial worker thread (from `_command_loop`) when
        invoked through `enqueue`, or on a fresh thread when invoked
        through `send_run_now`. Both call sites share this
        implementation because the wire protocol is the same.
        """
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