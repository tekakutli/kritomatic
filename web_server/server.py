#!/usr/bin/env python3
"""
web_panel/server.py — standalone live view of the daemon's state, with
Run and Cancel buttons.

Read-only against the kritomatic CLI. Nothing under src/kritomatic/ or
kritomatic_daemon/ is modified.

Architecture
------------
The daemon holds the live state: active workflow, params, layer
aliases, canvas and selection bindings, publisher pins, everything
that used to be serialized into a `panel_config.toml`. The panel is a
window onto that state.

  * On startup, the panel connects to the daemon's event socket
    (localhost:12347) and receives the initial state snapshot.

  * Every subsequent state mutation — browser publishes and CLI
    commands alike — pushes a fresh snapshot over that socket.

  * The panel forwards each snapshot to any open browser tabs over
    SSE. The page renders the live config preview and the connection
    status. No polling, no files.

The Run button
--------------
Sends a minimal bundle to the daemon: one `comfy_generate` command.
The daemon executes it against its own in-memory state. There is no
config file to read; the file was a snapshot format that the live
model makes unnecessary.

Run progress streams back over the same SSE connection as `run`
events.

The Cancel button
-----------------
Mirrors the Ctrl+C behaviour of `kritomatic comfyui panel run`.

When the user hits Ctrl+C during a `panel run`, the CLI does not close
its batch connection. It opens a *second*, independent connection to
the daemon, sends `comfy_cancel` on it (targeted at that run's batch
id), and then goes back to reading the original connection, waiting
for the daemon to wind the batch down on its own. Cancel is a request,
not a local abort: the batch loop on the daemon notices the flag at
its next check, interrupts ComfyUI, marks unfinished runs as
cancelled, and returns. The batch's terminal message arrives on the
original socket as usual, and everything is torn down by the normal
paths.

The panel's Cancel button does exactly that, with one structural
difference: the request originates from the panel process, not from
the batch's own thread, because the batch is running in a background
thread that has no way to receive a signal. The sequence is:

  1. Browser POSTs /api/cancel.
  2. Panel's cancel handler sets `cancel_requested` on the active
     run's entry under `_runs_lock`, then releases the lock.
  3. The handler opens a fresh KritaClient to the daemon and issues
     `comfy_cancel --batch_id=<the run's batch id>`.
  4. The daemon sets the batch's cancel event and returns immediately.
  5. The batch's worker thread — which is blocked in
     `BatchExecutor.execute`, reading from the *original* socket — is
     unblocked by the daemon's `batch_complete` message. It sees
     `cancel_requested` still set on its entry, emits a `cancelled`
     run event instead of a `done`, and cleans up.

Nothing kills a thread. Nothing closes a socket. The batch winds
itself down, exactly as in the CLI path.

Run bookkeeping
---------------
`_runs` holds every run currently in flight, keyed by run id. An
entry is inserted by `_handle_run_post` just before the worker thread
is spawned, and removed by `_do_run` itself in a `finally` when the
thread exits — success, failure, cancellation, or unhandled
exception, the entry goes away. The map therefore holds exactly the
set of running runs and nothing else.

Each entry carries:

  * `status` — "running". Present exactly while the run is in flight.
  * `batch_id` — the BatchExecutor's batch id, what comfy_cancel
    targets. Recorded by `_handle_run_post` so it is set before the
    thread starts and the cancel handler can find it without racing
    the thread's own setup.
  * `cancel_requested` — bool. Set by the cancel handler; read by
    `_do_run` when it decides between emitting `done` and `cancelled`.
  * `started` — timestamp.

Every access to `_runs` takes `_runs_lock`. The lock is held only for
the dict operation, never across a socket call or an SSE broadcast.

Event delivery
--------------
Each SSE subscriber owns a `LatestValueQueue` — a bounded FIFO with
a drop-oldest overflow policy. Two event families share it:

  * `state` events, pushed every time the daemon's state changes.
    These are snapshots, idempotent, and only the newest matters.

  * `run` events, pushed by the panel's own `_do_run` as a run
    advances through progress, cancelling, and a terminal
    done/cancelled/error.

A single-slot queue (which this used to be) is correct for state but
wrong for run events: a state update arriving in the same instant as
a run's terminal event would silently overwrite it, leaving the
browser's Run button stuck disabled with no way to recover short of
a page reload. A bounded FIFO gives the run events room to land
without dropping; the overflow policy still biases toward the newest
state, which is what a subscriber wants when it is genuinely behind.

Run it
------
    python web_panel/server.py
    # or
    WEB_PANEL_PORT=8289 python web_panel/server.py

Then open http://127.0.0.1:8189/.
"""

import json
import os
import queue
import random
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# ----------------------------------------------------------------------
#  Make the kritomatic package importable
# ----------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic.batch import BatchExecutor
from kritomatic.client import KritaClient


# ----------------------------------------------------------------------
#  Configuration
# ----------------------------------------------------------------------

HOST = os.environ.get("WEB_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_PANEL_PORT", "8189"))

DAEMON_HOST = "localhost"
DAEMON_PORT = 12346          # command socket
DAEMON_EVENT_PORT = 12347    # event socket

# Delay before reconnect on the daemon event stream. Only relevant
# when the daemon is not running.
RECONNECT_DELAY = 2.0

# SSE heartbeat interval, in seconds. A comment line on the SSE
# connection; keeps idle connections from being dropped.
SSE_HEARTBEAT = 15.0

# Read timeout for the run's socket. Must exceed the daemon's per-run
# timeout (55s default), with margin for prompt queueing and pasting.
RUN_READ_TIMEOUT = 180.0

# Per-subscriber event queue depth. See the module docstring's "Event
# delivery" section for why this is not 1. Well above any realistic
# burst of state updates arriving between two SSE writes.
SUBSCRIBER_QUEUE_SIZE = 256


# ----------------------------------------------------------------------
#  State cache (updated only from daemon pushes)
# ----------------------------------------------------------------------

_state = {
    "daemon": None,             # latest comfy_status data, or None
    "live_content": None,       # str or None (rendered preview)
    "live_reason": None,        # str or None (why live_content is absent)
    "daemon_connected": False,  # panel-to-daemon event stream up?
    "last_update": 0.0,
    "last_event": None,
    "last_error": None,
}
_state_lock = threading.Lock()


# ----------------------------------------------------------------------
#  SSE subscribers
# ----------------------------------------------------------------------

class LatestValueQueue:
    """A bounded FIFO with a drop-oldest overflow policy.

    One instance per SSE subscriber. Exactly one consumer (that
    subscriber's SSE handler thread) reads from it; puts come from any
    thread that calls `_broadcast`, which is the daemon event-client
    thread for `state` events and the run worker thread for `run`
    events.

    Why bounded and not unbounded: a subscriber whose SSE writer is
    stalled (slow browser socket, hung tab) must not grow memory
    without limit. The oldest entries are the ones dropped because
    that is the correct bias — a fresh state snapshot supersedes an
    older one, and a run event that has already been superseded by
    its own successor is of no further use. In normal operation the
    queue never fills: the SSE writer drains it as fast as `_broadcast`
    fills it.

    Why not single-slot: a single-slot queue would silently discard a
    run's terminal `done` event whenever a state update arrived in the
    same instant. That was the bug; a bounded FIFO with headroom
    removes it without changing the "newest state wins" bias.
    """

    def __init__(self, maxsize: int = SUBSCRIBER_QUEUE_SIZE):
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)

    def put(self, value) -> None:
        try:
            self._q.put_nowait(value)
        except queue.Full:
            # Drop the oldest, then retry once. If the queue is still
            # full (a concurrent consumer refilled it — impossible in
            # practice, since there is exactly one consumer and it only
            # removes), give up: a fresh value arriving at a
            # permanently-saturated queue is not worth an unbounded
            # loop.
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(value)
            except queue.Full:
                pass

    def get(self, timeout):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


_subscribers: list = []
_subscribers_lock = threading.Lock()


def _broadcast(event_type: str, payload: dict) -> None:
    with _subscribers_lock:
        targets = list(_subscribers)
    msg = (event_type, payload)
    for q in targets:
        q.put(msg)


def _broadcast_state() -> None:
    with _state_lock:
        payload = dict(_state)
    _broadcast("state", payload)


# ----------------------------------------------------------------------
#  Live config rendering
# ----------------------------------------------------------------------
#
# Mirrors the shape of the config the CLI would emit for a browser
# workflow, but substitutes the daemon's current state (`params`,
# `layer_aliases`, canvas/selection bindings) in place of schema
# defaults.

def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if v is None:
        return '""'
    s = str(v)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _toml_key(k: str) -> str:
    if k and all(c.isalnum() or c in "-_" for c in k):
        return k
    return _toml_value(k)


def _render_live_config(status) -> str:
    """Render the daemon's current state as a human-readable preview.

    This is for reading, not for running. The Run button sends a
    minimal bundle and the daemon executes against its own state;
    nothing here is round-tripped.
    """
    if not status:
        return ("# The daemon has no active workflow.\n"
                "# Publish a graph from ComfyUI's browser, or load a\n"
                "# file with `kritomatic comfyui panel run <file.json>`.\n")

    source = status.get("workflow_source")
    sig = status.get("workflow_signature") or ""

    lines: list = []
    lines.append(f'# source = {source or "unknown"}')
    if sig:
        lines.append(f'signature = {_toml_value(sig)}')
    lines.append('')

    canvas_info = status.get("workflow_canvas_info") or {}
    has_canvas = bool(canvas_info.get("has_canvas", False))
    has_selection = bool(canvas_info.get("has_selection", False))
    sel_context = canvas_info.get("selection_context") or "entire_image"
    sel_padding = int(canvas_info.get("selection_padding", 0) or 0)

    lines.append('# ---- Canvas and selection --------------------------------')
    lines.append('#')
    if has_canvas:
        lines.append('# This workflow uses ETN_KritaCanvas.')
    else:
        lines.append('# This workflow does not use ETN_KritaCanvas.')
    if has_selection:
        lines.append(f'# It also uses ETN_KritaSelection '
                     f'(context="{sel_context}", padding={sel_padding}).')
    else:
        lines.append('# It does not use ETN_KritaSelection.')

    canvas_source = status.get("canvas_source") or "active"
    canvas_layer = status.get("canvas_layer")
    if canvas_source == "layer" and canvas_layer:
        lines.append(f'canvas = ["layer", {_toml_value(canvas_layer)}]')
    else:
        lines.append(f'canvas = {_toml_value(canvas_source)}')

    sel_source = status.get("selection_source")
    sel_layer = status.get("selection_layer")
    if sel_source and sel_source != "global":
        lines.append(
            f'selection = [{_toml_value(sel_source)}, '
            f'{_toml_value(sel_layer or "")}, '
            f'{_toml_value(sel_context)}, {sel_padding}]'
        )
    else:
        lines.append('# selection = "global"')

    lines.append('')

    layer_reqs = status.get("workflow_layer_requirements") or {}
    mask_layers = list(layer_reqs.get("mask_layers") or [])
    image_layers = list(layer_reqs.get("image_layers") or [])
    aliases = status.get("layer_aliases") or {}

    lines.append('# ---- Layer aliases ----------------------------------------')
    lines.append('#')
    lines.append('[layer_aliases]')
    if not mask_layers and not image_layers:
        lines.append('# This workflow does not reference named layers.')
    else:
        for name in mask_layers:
            actual = aliases.get(name, name)
            lines.append(f'{_toml_key(name)} = {_toml_value(actual)}   '
                         f'# ETN_KritaMaskLayer')
        for name in image_layers:
            actual = aliases.get(name, name)
            lines.append(f'{_toml_key(name)} = {_toml_value(actual)}   '
                         f'# ETN_KritaImageLayer')
    lines.append('')

    lines.append('# ETN_Parameter values, keyed by each node\'s `name` widget.')
    lines.append('[params]')
    params_list = status.get("workflow_parameters") or []
    current_params = status.get("params") or {}
    if not params_list:
        lines.append('# (no ETN_Parameter nodes in this workflow)')
    else:
        for p in params_list:
            name = p.get("name", "")
            if not name:
                continue
            ptype = p.get("type", "auto")
            default = p.get("default", "")
            value = current_params.get(name, default)
            suffix = ""
            if ptype and ptype != "auto":
                marker = " (overridden)" if name in current_params else ""
                suffix = f'   # {ptype}{marker}'
            lines.append(f'{_toml_key(name)} = {_toml_value(value)}{suffix}')

    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
#  State updates (driven by daemon pushes)
# ----------------------------------------------------------------------

def _update_state_from_daemon(status, event_label: str) -> None:
    try:
        try:
            live = _render_live_config(status)
            live_reason = None
        except Exception as e:
            live = None
            live_reason = f"rendering failed: {e}"

        with _state_lock:
            _state["daemon"] = status
            _state["live_content"] = live
            _state["live_reason"] = live_reason
            _state["last_update"] = time.time()
            _state["last_event"] = event_label
            _state["last_error"] = None

    except Exception as e:
        with _state_lock:
            _state["last_error"] = str(e)
            _state["last_update"] = time.time()
            _state["last_event"] = event_label

    _broadcast_state()


# ----------------------------------------------------------------------
#  Daemon event stream client
# ----------------------------------------------------------------------

def _daemon_event_client() -> None:
    """Connect to the daemon's event socket and forward every push to
    SSE clients. Reconnects on disconnect.
    """
    while True:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((DAEMON_HOST, DAEMON_EVENT_PORT))

            with _state_lock:
                _state["daemon_connected"] = True
            _broadcast_state()

            buffer = b""
            sock.settimeout(None)
            while True:
                try:
                    chunk = sock.recv(65536)
                except (ConnectionResetError, OSError):
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
                    _handle_daemon_event(event)

        except Exception:
            pass
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            with _state_lock:
                _state["daemon_connected"] = False
            _broadcast_state()

        time.sleep(RECONNECT_DELAY)


def _handle_daemon_event(event: dict) -> None:
    if not isinstance(event, dict):
        return
    if event.get("type") == "state":
        status = event.get("data") or {}
        _update_state_from_daemon(status, "daemon push")


# ----------------------------------------------------------------------
#  Run bookkeeping
# ----------------------------------------------------------------------
#
# `_runs` is the set of runs currently in flight, keyed by run id. The
# invariant that makes the whole thing work: an entry exists exactly
# while its worker thread is alive. `_handle_run_post` inserts before
# spawning the thread; `_do_run` removes in a `finally` on every exit
# path. The "already in progress" guard is then a plain membership
# test against a map that cannot go stale.
#
# Every access — insert, guard-check, remove, cancel-flag read/write —
# takes `_runs_lock`. The lock is held only for the dict operation
# itself, never across a socket call or the SSE broadcast.

_runs: dict = {}
_runs_lock = threading.Lock()


def _new_run_id() -> str:
    return f"run-{int(time.time())}-{random.randint(0, 9999):04d}"


def _do_run(run_id: str, executor: BatchExecutor) -> None:
    """Execute one generation against the daemon's current state.

    The bundle is minimal: just `comfy_generate`. Everything else —
    params, layer aliases, canvas and selection bindings, the active
    workflow — is already in the daemon's memory. Sending them again
    would be redundant and would risk overwriting newer values that
    arrived between the user clicking Run and the batch reaching the
    daemon.

    This function owns the lifetime of `_runs[run_id]`. The entry is
    inserted by `_handle_run_post` before this thread is spawned, and
    it is removed here in a `finally` — success, failure, cancellation,
    or unhandled exception, the slot is freed on every path.

    Cancellation is cooperative: the cancel handler does not signal
    this thread, it just sets `cancel_requested` on the run entry and
    sends `comfy_cancel` to the daemon on a separate connection. The
    daemon winds the batch down, its terminal `batch_complete` arrives
    on this thread's own socket, `executor.execute` returns normally,
    and the flag is read here to decide between the `done` and
    `cancelled` terminal events.
    """
    def emit(status: str, **extra) -> None:
        payload = {"run_id": run_id, "status": status}
        payload.update(extra)
        _broadcast("run", payload)

    try:
        def on_progress(index, total, cmd_type, result_data):
            emit("progress",
                 index=index - 1,
                 total=total,
                 command=cmd_type,
                 run_status=result_data.get("status"),
                 message=result_data.get("message", ""))

        executor.callbacks.append(on_progress)

        try:
            results = executor.execute(
                {
                    "id": "web_panel",
                    "commands": [
                        {"type": "comfy_generate", "timeout": 55.0},
                    ],
                },
                timeout=RUN_READ_TIMEOUT,
            )
        finally:
            executor.close()

        successful = sum(1 for r in results
                         if r.get("status") == "success")
        failed = sum(1 for r in results
                     if r.get("status") == "error")

        # Read the cancel flag under the lock. If a cancel was
        # requested, we emit a distinct `cancelled` event so the
        # browser can render it differently from a normal completion
        # or an error. The read happens after the batch has returned
        # because that is when we know the daemon is done with it;
        # a cancel that arrived after this point loses the race and
        # the run is reported as it actually ended.
        with _runs_lock:
            was_cancelled = bool(
                _runs.get(run_id, {}).get("cancel_requested")
            )

        if was_cancelled:
            emit("cancelled", successful=successful, failed=failed)
        else:
            emit("done", successful=successful, failed=failed)

    except Exception as e:
        traceback.print_exc()
        emit("error", error=str(e))

    finally:
        # Free this run's slot before the thread exits. The guard in
        # _handle_run_post checks this map; leaving the entry in place
        # would keep it returning 409 forever after the first run.
        with _runs_lock:
            _runs.pop(run_id, None)
        print(f"[web_panel] run {run_id} finished")


def _send_daemon_cancel(batch_id: str) -> tuple[bool, str]:
    """Send a comfy_cancel to the daemon for one batch id.

    Runs on a fresh KritaClient, independent of the batch's own
    connection — same pattern as the CLI's Ctrl+C handler in
    kritomatic.batch.executor. Returns (ok, message). ok is True when
    the daemon accepted the request; the actual wind-down is
    asynchronous and confirmed later by the batch's own terminal
    message on its original socket.
    """
    client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
    if not client.connect():
        return False, "could not connect to daemon"
    try:
        resp = client.execute("comfy_cancel", batch_id=batch_id)
    except Exception as e:
        return False, str(e)
    finally:
        client.close()

    if not resp:
        return False, "no response from daemon"
    if resp.get("status") != "success":
        return False, resp.get("message", "daemon refused the cancel")
    return True, resp.get("message", "cancel requested")


# ----------------------------------------------------------------------
#  HTTP handler
# ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KritomaticWebPanel/1.4"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200,
                   content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status=status)

    # ---- GET ----

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                self._handle_index()
            elif self.path == "/api/state":
                self._handle_state()
            elif self.path == "/api/events":
                self._handle_events()
            else:
                self._send_text("not found", status=404)
        except Exception:
            try:
                self._send_error_json("internal error", status=500)
            except Exception:
                pass

    def _handle_index(self):
        self._send_text(HTML_PAGE,
                        content_type="text/html; charset=utf-8")

    def _handle_state(self):
        with _state_lock:
            s = dict(_state)
        self._send_json(s)

    def _handle_events(self):
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:
            return

        q = LatestValueQueue()
        with _subscribers_lock:
            _subscribers.append(q)

        try:
            with _state_lock:
                initial = dict(_state)
            self._write_sse("state", initial)

            while True:
                msg = q.get(timeout=SSE_HEARTBEAT)
                if msg is None:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue
                event_type, payload = msg
                self._write_sse(event_type, payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception:
            traceback.print_exc()
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    def _write_sse(self, event_type: str, payload: dict) -> None:
        data = json.dumps(payload)
        msg = f"event: {event_type}\ndata: {data}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

    # ---- POST ----

    def do_POST(self):
        try:
            if self.path == "/api/run":
                self._handle_run_post()
            elif self.path == "/api/cancel":
                self._handle_cancel_post()
            else:
                self._send_text("not found", status=404)
        except Exception:
            try:
                self._send_error_json("internal error", status=500)
            except Exception:
                pass

    def _handle_run_post(self):
        # Guard: refuse if a run is already in flight. `_runs` holds
        # exactly the running runs — see _do_run's docstring for why
        # that invariant holds — so a single membership test is
        # enough.
        with _runs_lock:
            for r in _runs.values():
                if r.get("status") == "running":
                    self._send_error_json(
                        "a run is already in progress", status=409)
                    return

        with _state_lock:
            daemon = _state.get("daemon")

        if not daemon or not daemon.get("workflow"):
            self._send_error_json(
                "no active workflow in the daemon. Publish from ComfyUI's "
                "browser, or load a file with "
                "`kritomatic comfyui panel run <file.json>`.",
                status=400)
            return

        # Create the executor here rather than inside the worker, so
        # its batch id is available to the cancel handler from the
        # moment the run entry exists. The executor is single-threaded
        # by construction; nothing else touches it once _do_run owns
        # it.
        executor = BatchExecutor()

        run_id = _new_run_id()
        with _runs_lock:
            _runs[run_id] = {
                "status": "running",
                "batch_id": executor.batch_id,
                "cancel_requested": False,
                "started": time.time(),
            }
        print(f"[web_panel] run {run_id} started "
              f"(batch {executor.batch_id})")

        t = threading.Thread(
            target=_do_run,
            args=(run_id, executor),
            name=f"web-panel-run-{run_id}",
            daemon=True,
        )
        t.start()

        self._send_json({"run_id": run_id,
                         "batch_id": executor.batch_id})

    def _handle_cancel_post(self):
        """Handle the browser's Cancel click.

        Locates the currently running entry, marks it as
        cancel_requested under the lock, then issues comfy_cancel to
        the daemon on a fresh connection. The actual wind-down is
        asynchronous: the daemon sets the batch's cancel event, the
        batch loop notices at its next check, and the batch's own
        worker thread emits the terminal `cancelled` event when the
        resulting `batch_complete` reaches it on the original socket.

        A cancel that arrives after the run has already finished is
        answered with 409 "no run is in progress" — the run entry is
        gone by then, and there is nothing to cancel. A cancel that
        races the run's completion is best-effort: the daemon may
        reply "no active batch with id X", which the panel logs and
        discards; the browser will still receive the run's normal
        terminal event.
        """
        with _runs_lock:
            run_id = None
            for rid, r in _runs.items():
                if r.get("status") == "running":
                    run_id = rid
                    break

            if run_id is None:
                self._send_error_json(
                    "no run is in progress", status=409)
                return

            entry = _runs[run_id]
            if entry.get("cancel_requested"):
                # Already asked. Report the current state without
                # sending a second cancel to the daemon.
                self._send_json({
                    "run_id": run_id,
                    "status": "already-cancelling",
                })
                return

            entry["cancel_requested"] = True
            batch_id = entry.get("batch_id") or ""

        # Tell the browser to render the cancelling state, then send
        # the cancel to the daemon. The broadcast is issued outside
        # the lock so a slow subscriber cannot stall the cancel path.
        _broadcast("run", {"run_id": run_id, "status": "cancelling"})
        print(f"[web_panel] cancel requested for run {run_id} "
              f"(batch {batch_id})")

        if batch_id:
            ok, msg = _send_daemon_cancel(batch_id)
            if not ok:
                # Not fatal. The run is still live on the daemon (or
                # has just completed); either way the browser will see
                # its terminal event normally. Log and continue.
                print(f"[web_panel] cancel for {run_id} failed: {msg}")
        else:
            print(f"[web_panel] cancel for {run_id}: no batch id recorded")

        self._send_json({"run_id": run_id, "status": "cancelling"})


# ----------------------------------------------------------------------
#  HTML page — EventSource only, no polling
# ----------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Kritomatic Web Panel</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px;
         margin: 2rem auto; padding: 0 1rem; color: #222;
         line-height: 1.5; }
  h1 { margin: 0 0 1rem; font-size: 1.4rem; }
  h2 { margin: 1.5rem 0 0.5rem; font-size: 1rem;
       text-transform: uppercase; letter-spacing: 0.05em;
       color: #555; }
  h2 .tag { font-size: 0.75rem; text-transform: none;
            letter-spacing: 0; color: #888; font-weight: normal;
            margin-left: 0.5rem; }
  .status { padding: 0.75rem 1rem; background: #f4f4f4;
            border-radius: 4px; font-size: 0.9rem; }
  .status.ok { background: #e6f4ea; }
  .status.warn { background: #fef7e0; }
  .status.err { background: #fce8e6; }
  .status div { margin: 0.2rem 0; }
  .status .dim { color: #666; font-size: 0.85rem; }
  pre { background: #1e1e1e; color: #d4d4d4; padding: 1rem;
        border-radius: 4px; overflow-x: auto; font-size: 0.85rem;
        line-height: 1.45; max-height: 36rem; overflow-y: auto; }
  .note { font-size: 0.85rem; color: #666; margin: 0.25rem 0 0.5rem; }
  button { font: inherit; padding: 0.5rem 1rem; cursor: pointer;
           background: #1a73e8; color: white; border: none;
           border-radius: 4px; margin-right: 0.5rem; }
  button:disabled { background: #aaa; cursor: not-allowed; }
  button.cancel { background: #c5221f; }
  button.cancel:disabled { background: #aaa; }
  .progress { margin-top: 0.75rem; }
  .progress .row { padding: 0.35rem 0.5rem; font-size: 0.85rem;
                   border-bottom: 1px solid #eee;
                   font-family: ui-monospace, monospace;
                   word-break: break-word; }
  .progress .row.ok { color: #0d652d; }
  .progress .row.err { color: #c5221f; }
  .progress .row.warn { color: #a05a00; }
  .progress .row.hdr { color: #333; font-weight: 600; }
  .empty { color: #888; font-style: italic; font-size: 0.85rem; }
</style>
</head>
<body>
<h1>Kritomatic Web Panel</h1>

<div class="status" id="status">
  <div class="empty">Connecting&hellip;</div>
</div>

<h2>Config
  <span class="tag">the daemon's live state, pushed as you edit</span>
</h2>
<div class="note">
  The daemon holds this state in memory. It updates on every browser
  publish and every CLI command, and is pushed to this page over a
  single long-lived connection. Values marked <em>(overridden)</em>
  differ from the workflow's schema default. Nothing here is read
  from or written to disk.
</div>
<pre id="live-content">Loading&hellip;</pre>

<h2>Run</h2>
<div class="note">
  Runs one generation against the daemon's current state. Any
  parameters, layer aliases, or canvas bindings the daemon is holding
  right now are what get used. For batch runs, seed control, or
  count &gt; 1, use the CLI. Cancel sends a request to the daemon;
  the batch winds itself down, and any layers it already pasted stay
  in the document.
</div>
<button id="run-btn">Run</button>
<button id="cancel-btn" class="cancel" disabled>Cancel</button>
<div class="progress" id="progress"></div>

<script>
"use strict";

// --------------------------------------------------------------
// Button state machine
// --------------------------------------------------------------
//
// The two buttons and their enabled flags encode one of three
// states. Keeping the transitions in named functions — rather
// than scattering `btn.disabled = ...` assignments — keeps the
// UI consistent regardless of which event (POST response, SSE
// run event, network error) drove the transition.

function setIdle() {
  document.getElementById("run-btn").disabled = false;
  document.getElementById("cancel-btn").disabled = true;
}

function setRunning() {
  document.getElementById("run-btn").disabled = true;
  document.getElementById("cancel-btn").disabled = false;
}

function setCancelling() {
  document.getElementById("run-btn").disabled = true;
  document.getElementById("cancel-btn").disabled = true;
}

// --------------------------------------------------------------
// Status rendering (unchanged)
// --------------------------------------------------------------

function esc(s) {
  return String(s).replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
}

function renderStatus(s) {
  const el = document.getElementById("status");
  el.className = "status";
  const lines = [];

  if (s.last_error) {
    el.classList.add("err");
    lines.push("Error: " + esc(s.last_error));
  } else if (!s.daemon_connected) {
    el.classList.add("warn");
    lines.push("Waiting for the daemon event stream on " +
               "localhost:12347&hellip;");
    lines.push('<span class="dim">Start Krita with the Kritomatic ' +
               'Daemon plugin enabled. This page reconnects ' +
               'automatically.</span>');
  } else if (!s.daemon || !s.daemon.workflow) {
    el.classList.add("warn");
    lines.push("Connected to the daemon; no active workflow.");
    const pubs = (s.daemon && s.daemon.published_publishers) || [];
    if (pubs.length) {
      lines.push('<span class="dim">' + pubs.length +
                 " browser publisher(s) tracked; publish from " +
                 "ComfyUI's browser to make one active.</span>");
    } else {
      lines.push('<span class="dim">Open ComfyUI in a browser with an ' +
                 'ETN_KritaOutput node to publish a graph.</span>');
    }
  } else {
    const d = s.daemon;
    el.classList.add("ok");
    lines.push("Workflow: <b>" + esc(d.workflow) + "</b>");
    lines.push('<span class="dim">Source: ' +
               esc(d.workflow_source || "?") +
               " &middot; Signature: " +
               esc(d.workflow_signature || "?") + "</span>");
    if (d.connected_to) {
      lines.push('<span class="dim">ComfyUI: ' +
                 esc(d.connected_to) + "</span>");
    }
    if (d.workflow_exclusive) {
      lines.push('<span class="dim">File lock: on</span>');
    }
    if (d.pinned_publisher) {
      lines.push('<span class="dim">Pinned publisher: ' +
                 esc(d.pinned_publisher) + "</span>");
    }
  }

  if (s.last_event) {
    lines.push('<span class="dim">Last event: ' +
               esc(s.last_event) + "</span>");
  }

  el.innerHTML = lines.map(l => "<div>" + l + "</div>").join("")
    || '<div class="empty">(no state)</div>';
}

function renderState(s) {
  renderStatus(s);

  const liveEl = document.getElementById("live-content");

  if (s.live_content) {
    liveEl.textContent = s.live_content;
  } else if (s.live_reason) {
    liveEl.textContent = "(no preview: " + s.live_reason + ")";
  } else {
    liveEl.textContent = "(no preview)";
  }
}

function rowDiv(cls, text) {
  const d = document.createElement("div");
  d.className = "row " + cls;
  d.textContent = text;
  return d;
}

// --------------------------------------------------------------
// Run event handling
// --------------------------------------------------------------

function handleRunEvent(e) {
  const s = JSON.parse(e.data);
  const log = document.getElementById("progress");

  if (s.status === "progress") {
    const cls = s.run_status === "success" ? "ok"
               : s.run_status === "error" ? "err"
               : "";
    log.appendChild(rowDiv(cls,
      "[" + (s.index + 1) + "/" + s.total + "] " +
      s.command + ": " + s.message));

  } else if (s.status === "cancelling") {
    log.appendChild(rowDiv("warn",
      "Cancelling\u2026 waiting for the daemon to wind down the batch."));
    setCancelling();

  } else if (s.status === "cancelled") {
    log.appendChild(rowDiv("warn",
      "Cancelled. " + s.successful + " ok, " + s.failed +
      " not completed."));
    setIdle();

  } else if (s.status === "done") {
    log.appendChild(rowDiv("hdr",
      "Done. " + s.successful + " ok, " + s.failed + " failed."));
    setIdle();

  } else if (s.status === "error") {
    log.appendChild(rowDiv("err",
      "Run failed: " + (s.error || "unknown")));
    setIdle();
  }
}

function connectEvents() {
  const es = new EventSource("/api/events");

  es.addEventListener("state", function (e) {
    try {
      renderState(JSON.parse(e.data));
    } catch (err) {
      console.error("bad state payload", err);
    }
  });

  es.addEventListener("run", handleRunEvent);

  es.onerror = function () {
    // EventSource reconnects automatically. A fresh initial state
    // arrives on the next `state` event.
  };
}

// --------------------------------------------------------------
// Run and Cancel click handlers
// --------------------------------------------------------------

async function startRun() {
  const log = document.getElementById("progress");
  log.innerHTML = "";
  setRunning();
  log.appendChild(rowDiv("hdr", "Starting run\u2026"));

  let resp;
  try {
    resp = await fetch("/api/run", {method: "POST"});
  } catch (e) {
    log.innerHTML = "";
    log.appendChild(rowDiv("err", "Request failed: " + e.message));
    setIdle();
    return;
  }

  if (!resp.ok) {
    let msg = "HTTP " + resp.status;
    try {
      const body = await resp.json();
      if (body && body.error) msg = body.error;
    } catch (_) {}
    log.innerHTML = "";
    log.appendChild(rowDiv("err", "Cannot run: " + msg));
    setIdle();
    return;
  }

  const body = await resp.json();
  log.innerHTML = "";
  log.appendChild(rowDiv("hdr", "Run " + body.run_id + " started."));
  // Progress streams in over the already-open SSE connection.
}

async function cancelRun() {
  const log = document.getElementById("progress");
  setCancelling();

  let resp;
  try {
    resp = await fetch("/api/cancel", {method: "POST"});
  } catch (e) {
    log.appendChild(rowDiv("err",
      "Cancel request failed: " + e.message));
    // The run may still be going; do not re-enable Run on a local
    // network failure, since the daemon has not been told anything.
    setRunning();
    return;
  }

  if (!resp.ok) {
    let msg = "HTTP " + resp.status;
    try {
      const body = await resp.json();
      if (body && body.error) msg = body.error;
    } catch (_) {}
    log.appendChild(rowDiv("err", "Cannot cancel: " + msg));
    // 409 means no run is in progress; the terminal event from the
    // run we thought was active has already come and gone, so we
    // are idle. Any other status leaves the run state untouched;
    // the next run event will resolve it.
    if (resp.status === 409) {
      setIdle();
    } else {
      setRunning();
    }
    return;
  }

  // Success: the request was accepted. The run's terminal event
  // will arrive over SSE and move us to idle. Nothing more to do
  // here beyond the "Cancelling..." row that the `cancelling`
  // run event already adds.
}

document.getElementById("run-btn").addEventListener("click", startRun);
document.getElementById("cancel-btn").addEventListener("click", cancelRun);

connectEvents();
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------
#  Main
# ----------------------------------------------------------------------

def main() -> int:
    threading.Thread(target=_daemon_event_client,
                     name="web-panel-daemon-client",
                     daemon=True).start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True

    print(f"Kritomatic web panel listening on http://{HOST}:{PORT}/")
    print(f"  Daemon command socket: {DAEMON_HOST}:{DAEMON_PORT}")
    print(f"  Daemon event socket:   {DAEMON_HOST}:{DAEMON_EVENT_PORT}")
    print("  Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
