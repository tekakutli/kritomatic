#!/usr/bin/env python3
"""
web_panel/server.py — standalone live view of the daemon's state.

A pure client of the daemon. Reads the same event stream the Krita
docker reads, renders the same panel description the docker renders,
and posts the same commands to the same command socket.

Two responsibilities:

  * Maintain a small in-process cache of the latest state and panel
    events received from the daemon, and forward both to any open
    browser tabs over SSE.

  * Forward single commands and run requests from the browser back to
    the daemon, streaming run progress over SSE.

Everything the browser needs to draw comes from the panel description
the daemon publishes. There is no comfyui-specific logic in this file.

Run it
------
    python web_server/server.py
    # or
    WEB_PANEL_PORT=8289 python web_server/server.py

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

from kritomatic.client import KritaClient


# ----------------------------------------------------------------------
#  Configuration
# ----------------------------------------------------------------------

HOST = os.environ.get("WEB_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_PANEL_PORT", "8189"))

DAEMON_HOST = "localhost"
DAEMON_PORT = 12346
DAEMON_EVENT_PORT = 12347

RECONNECT_DELAY = 2.0
SSE_HEARTBEAT = 15.0
SUBSCRIBER_QUEUE_SIZE = 256

RUN_READ_TIMEOUT_MARGIN = 60.0


# ----------------------------------------------------------------------
#  State cache
# ----------------------------------------------------------------------

_state = {
    "daemon": None,
    "panel": None,
    "daemon_connected": False,
    "last_update": 0.0,
    "last_event": None,
    "last_error": None,
}
_state_lock = threading.Lock()


# ----------------------------------------------------------------------
#  SSE subscribers
# ----------------------------------------------------------------------

class LatestValueQueue:
    def __init__(self, maxsize: int = SUBSCRIBER_QUEUE_SIZE):
        self._q: queue.Queue = queue.Queue(maxsize=maxsize)

    def put(self, value) -> None:
        try:
            self._q.put_nowait(value)
        except queue.Full:
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


def _broadcast(event_type: str, payload) -> None:
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
#  Daemon event stream client
# ----------------------------------------------------------------------

def _daemon_event_client() -> None:
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
    etype = event.get("type")
    data = event.get("data")
    if etype == "state":
        with _state_lock:
            _state["daemon"] = data
            _state["last_update"] = time.time()
            _state["last_event"] = "state push"
            _state["last_error"] = None
        _broadcast_state()
    elif etype == "panel":
        with _state_lock:
            _state["panel"] = data
            _state["last_update"] = time.time()
            _state["last_event"] = "panel push"
        _broadcast("panel", data)


# ----------------------------------------------------------------------
#  Daemon command helpers
# ----------------------------------------------------------------------

def _daemon_call(command: dict) -> tuple:
    """Send one command on a fresh connection. Returns (ok, message, data)."""
    client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
    if not client.connect():
        return False, "could not connect to daemon", {}
    try:
        args = {k: v for k, v in command.items() if k != "type"}
        resp = client.execute(command.get("type"), **args)
    except Exception as e:
        return False, str(e), {}
    finally:
        client.close()

    if not resp:
        return False, "no response from daemon", {}
    if resp.get("status") != "success":
        return False, resp.get("message", "daemon refused the command"), {}
    return True, resp.get("message", ""), resp.get("data") or {}


# ----------------------------------------------------------------------
#  Run bookkeeping
# ----------------------------------------------------------------------

_runs: dict = {}
_runs_lock = threading.Lock()


def _new_run_id() -> str:
    return f"run-{int(time.time())}-{random.randint(0, 9999):04d}"


def _do_run(run_id: str, command: dict) -> None:
    def emit(status: str, **extra):
        payload = {"run_id": run_id, "status": status}
        payload.update(extra)
        _broadcast("run", payload)

    try:
        try:
            timeout = float(command.get("timeout") or 55.0)
        except (TypeError, ValueError):
            timeout = 55.0
        try:
            count = int(command.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, count)

        read_timeout = timeout * (count + 1) + RUN_READ_TIMEOUT_MARGIN

        bundle = {"id": run_id, "commands": [command]}

        client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
        if not client.connect():
            emit("error", error="could not connect to daemon")
            return

        try:
            def on_progress(index, result_entry):
                cmd_type = result_entry.get("command", "?")
                status = result_entry.get("status", "?")
                msg = result_entry.get("message", "")
                emit("progress",
                     index=index,
                     total=1,
                     command=cmd_type,
                     run_status=status,
                     message=msg)

            summary = client.send_batch(
                bundle, on_progress=on_progress, timeout=read_timeout,
            )
        finally:
            client.close()

        if summary is None:
            emit("error", error="no summary from daemon")
            return

        succ = summary.get("successful", 0)
        fail = summary.get("failed", 0)

        # A cancelled batch reports success=False and carries a
        # "cancelled" mention in the failure message. When nothing
        # succeeded and every error is a cancellation, the honest
        # terminal event is "cancelled", not "done with N failures".
        results = summary.get("results", []) or []
        cancelled_count = sum(
            1 for r in results
            if r.get("status") == "error"
            and "cancelled" in (r.get("message") or "").lower()
        )
        if cancelled_count > 0 and succ == 0:
            emit("cancelled",
                 successful=succ, failed=fail,
                 cancelled=cancelled_count)
        else:
            emit("done",
                 successful=succ, failed=fail,
                 cancelled=cancelled_count)

    except Exception as e:
        traceback.print_exc()
        emit("error", error=str(e))
    finally:
        with _runs_lock:
            _runs.pop(run_id, None)
        print(f"[web_panel] run {run_id} finished")


def _send_daemon_cancel(batch_id) -> tuple:
    client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
    if not client.connect():
        return False, "could not connect to daemon"
    try:
        if batch_id:
            resp = client.execute("comfy_cancel", batch_id=batch_id)
        else:
            resp = client.execute("comfy_cancel")
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
    server_version = "KritomaticWebPanel/2.2"

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

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > 65536:
            return None
        try:
            body = self.rfile.read(length)
        except (ConnectionError, OSError):
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ---- GET ----

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                self._send_text(HTML_PAGE,
                                content_type="text/html; charset=utf-8")
            elif self.path == "/api/state":
                with _state_lock:
                    self._send_json(dict(_state))
            elif self.path == "/api/events":
                self._handle_events()
            else:
                self._send_text("not found", status=404)
        except Exception:
            try:
                self._send_error_json("internal error", status=500)
            except Exception:
                pass

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
            if initial.get("panel") is not None:
                self._write_sse("panel", initial["panel"])

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

    def _write_sse(self, event_type: str, payload) -> None:
        data = json.dumps(payload)
        msg = f"event: {event_type}\ndata: {data}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

    # ---- POST ----

    def do_POST(self):
        try:
            path = self.path
            if path == "/api/command":
                self._handle_command_post()
            elif path == "/api/run":
                self._handle_run_post()
            elif path == "/api/cancel":
                self._handle_cancel_post()
            else:
                self._send_text("not found", status=404)
        except Exception:
            try:
                self._send_error_json("internal error", status=500)
            except Exception:
                pass

    def _handle_command_post(self):
        body = self._read_json_body()
        if not isinstance(body, dict):
            self._send_error_json("expected a JSON object", status=400)
            return
        command = body.get("command")
        if not isinstance(command, dict) or "type" not in command:
            self._send_error_json(
                "'command' must be an object with a 'type'", status=400)
            return
        ok, msg, data = _daemon_call(command)
        if ok:
            self._send_json({"ok": True, "message": msg, "data": data})
        else:
            self._send_error_json(msg, status=502)

    def _handle_run_post(self):
        body = self._read_json_body()
        if not isinstance(body, dict):
            self._send_error_json("expected a JSON object", status=400)
            return
        command = body.get("command")
        if not isinstance(command, dict) \
                or command.get("type") != "comfy_generate":
            self._send_error_json(
                "'command' must be a comfy_generate command", status=400)
            return

        with _runs_lock:
            for r in _runs.values():
                if r.get("status") == "running":
                    self._send_error_json(
                        "a run is already in progress", status=409)
                    return

        run_id = _new_run_id()
        with _runs_lock:
            _runs[run_id] = {"status": "running", "started": time.time()}

        t = threading.Thread(
            target=_do_run,
            args=(run_id, command),
            name=f"web-panel-run-{run_id}",
            daemon=True,
        )
        t.start()
        print(f"[web_panel] run {run_id} started")
        self._send_json({"run_id": run_id})

    def _handle_cancel_post(self):
        body = self._read_json_body()
        if not isinstance(body, dict):
            body = {}

        scope = body.get("scope", "current")

        if scope == "all":
            # Cancel every batch the daemon knows about — including
            # ones launched from the CLI or another panel — plus any
            # async generates. Mark our own run as cancelling so the
            # UI status flips, then send the cancel-all command with
            # no batch id.
            with _runs_lock:
                run_id = None
                for rid, r in _runs.items():
                    if r.get("status") == "running":
                        run_id = rid
                        r["cancel_requested"] = True
                        break

            if run_id:
                _broadcast("run", {"run_id": run_id,
                                   "status": "cancelling"})

            ok, msg = _send_daemon_cancel(None)
            if not ok:
                print(f"[web_panel] cancel_all failed: {msg}")
                self._send_error_json(msg, status=502)
                return

            print("[web_panel] cancel_all requested")
            self._send_json({"status": "cancelling_all", "message": msg})
            return

        # scope == "current" — the original behaviour.
        with _runs_lock:
            run_id = None
            for rid, r in _runs.items():
                if r.get("status") == "running":
                    run_id = rid
                    break
            if run_id is None:
                self._send_error_json("no run is in progress", status=409)
                return
            entry = _runs[run_id]
            if entry.get("cancel_requested"):
                self._send_json({"run_id": run_id,
                                 "status": "already-cancelling"})
                return
            entry["cancel_requested"] = True

        _broadcast("run", {"run_id": run_id, "status": "cancelling"})
        print(f"[web_panel] cancel requested for run {run_id}")

        # `_do_run` sends the bundle with `id=run_id`, and
        # `handlers/base.py` propagates that id to each command as
        # `_batch_id`; `comfy_generate` uses `_batch_id` as its
        # registered batch id. So `run_id` here is the same value the
        # daemon has registered as the active batch's id. Cancel is
        # issued on a fresh connection (`_send_daemon_cancel`), which
        # is safe to do while the run's own socket is mid-read.
        ok, msg = _send_daemon_cancel(run_id)
        if not ok:
            print(f"[web_panel] cancel for {run_id} failed: {msg}")

        self._send_json({"run_id": run_id, "status": "cancelling"})


# ----------------------------------------------------------------------
#  HTML page — generic description renderer
# ----------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Kritomatic Web Panel</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 960px;
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

  .config-form {
    background: #fafafa;
    border: 1px solid #eee;
    border-radius: 4px;
    padding: 1rem 1rem 1.1rem;
    min-height: 2rem;
  }
  .metadata-block {
    display: flex; flex-wrap: wrap; gap: 1rem;
    font-size: 0.85rem; color: #444;
    padding-bottom: 0.6rem;
    border-bottom: 1px solid #eee;
    margin-bottom: 0.5rem;
  }
  .metadata-block .dim { color: #888; }
  .metadata-block .strong { font-weight: 600; }
  .config-section { margin-top: 1rem; padding-top: 0.75rem;
                    border-top: 1px solid #eee; }
  .config-section:first-child { margin-top: 0; padding-top: 0;
                                border-top: none; }
  .config-section h3 {
    margin: 0 0 0.75rem;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #666;
    font-weight: 600;
  }
  .field {
    display: grid;
    grid-template-columns: minmax(140px, max-content) 1fr;
    gap: 0.35rem 0.75rem;
    align-items: center;
    margin-bottom: 0.4rem;
  }
  .field > label {
    text-align: right; color: #444; font-size: 0.85rem;
    overflow-wrap: anywhere;
  }
  .field input[type=text],
  .field input[type=number],
  .field select,
  .field textarea {
    font: inherit; font-size: 0.85rem;
    padding: 0.3rem 0.5rem;
    border: 1px solid #ccc; border-radius: 3px;
    width: 100%; box-sizing: border-box; background: white;
  }
  .field textarea {
    font-family: inherit; resize: vertical; min-height: 3.5em;
  }
  .field input:focus, .field select:focus, .field textarea:focus {
    outline: 2px solid #1a73e8; outline-offset: -1px;
    border-color: transparent;
  }
  .field.in-flight input, .field.in-flight select,
  .field.in-flight textarea { opacity: 0.6; }
  .field.overridden > label { color: #1a73e8; }
  .field.overridden > label::after {
    content: " \25CF"; color: #1a73e8; font-size: 0.7em;
  }
  .field-error {
    grid-column: 2; color: #c5221f; font-size: 0.8rem;
    margin-top: -0.15rem; margin-bottom: 0.15rem;
  }
  .radio-group {
    display: flex; gap: 1rem; align-items: center; font-size: 0.85rem;
  }
  .radio-group label {
    display: flex; align-items: center; gap: 0.3rem;
    cursor: pointer;
  }
  .empty { color: #888; font-style: italic; font-size: 0.85rem; }

  .actions-section { display: flex; gap: 0.5rem; }
  button {
    font: inherit; padding: 0.5rem 1rem;
    cursor: pointer; border: none; border-radius: 4px;
  }
  button.primary { background: #1a73e8; color: white; }
  button.danger { background: #c5221f; color: white; }
  button:disabled { background: #aaa; cursor: not-allowed; }

  .progress { margin-top: 0.9rem; }
  .progress .row {
    padding: 0.35rem 0.5rem; font-size: 0.85rem;
    border-bottom: 1px solid #eee;
    font-family: ui-monospace, monospace; word-break: break-word;
  }
  .progress .row.ok { color: #0d652d; }
  .progress .row.err { color: #c5221f; }
  .progress .row.warn { color: #a05a00; }
  .progress .row.hdr { color: #333; font-weight: 600; }
</style>
</head>
<body>
<h1>Kritomatic Web Panel</h1>

<div class="status" id="status">
  <div class="empty">Connecting&hellip;</div>
</div>

<h2>Panel
  <span class="tag">daemon-driven description</span>
</h2>
<div class="config-form" id="config-form">
  <div class="empty">Loading&hellip;</div>
</div>

<div class="progress" id="progress"></div>

<script>
"use strict";

const app = {
  latestState: null,
  latestPanel: null,
  panelStructureId: null,
  fields: new Map(),
  specs: new Map(),
  sections: new Map(),
  sectionSpecs: [],
  focusKeys: new Set(),
  inFlight: new Set(),
  commitQueue: Promise.resolve(),
  running: false,
  eventsConnected: false,
  firstPanelReceived: false,
};

function esc(s) {
  return String(s).replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
}
function escAttr(s) {
  return String(s).replace(/&/g, "&amp;")
                  .replace(/"/g, "&quot;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");
}
function cssEsc(s) { return String(s).replace(/"/g, '\\"'); }

function toBool(v) {
  if (v === true) return true;
  if (typeof v === "string") {
    const low = v.toLowerCase();
    return low === "true" || low === "1";
  }
  return false;
}

function renderStatus(s) {
  const el = document.getElementById("status");
  el.className = "status";
  const lines = [];
  if (!s) {
    el.classList.add("warn");
    lines.push("Waiting for daemon state&hellip;");
  } else if (s.last_error) {
    el.classList.add("err");
    lines.push("Error: " + esc(s.last_error));
  } else if (!s.daemon_connected) {
    el.classList.add("warn");
    lines.push("Waiting for the daemon event stream on localhost:12347&hellip;");
    lines.push('<span class="dim">Start Krita with the Kritomatic Daemon plugin enabled.</span>');
  } else if (!s.daemon || !s.daemon.workflow) {
    el.classList.add("warn");
    lines.push("Connected to the daemon; no active workflow.");
    lines.push('<span class="dim">Publish a graph from ComfyUI&rsquo;s browser, or load a file.</span>');
  } else {
    el.classList.add("ok");
    const d = s.daemon;
    if (d.connected_to) {
      lines.push("Connected to ComfyUI at " + esc(d.connected_to));
    } else {
      lines.push("Connected to the daemon.");
    }
    if (d.workflow_exclusive) {
      lines.push('<span class="dim">File lock: on</span>');
    }
    if (d.pinned_publisher) {
      lines.push('<span class="dim">Pinned publisher: ' + esc(d.pinned_publisher) + '</span>');
    }
  }
  if (s && s.last_event) {
    lines.push('<span class="dim">Last event: ' + esc(s.last_event) + '</span>');
  }
  el.innerHTML = lines.map(l => "<div>" + l + "</div>").join("")
    || '<div class="empty">(no state)</div>';
}

function buildSectionHtml(section) {
  const kind = section.kind;
  const fields = section.fields || [];
  if (kind === "metadata") {
    const inner = fields.map(f => {
      const cls = f.emphasis || "";
      const txt = f.label
        ? `<b>${esc(f.label)}:</b> ${esc(f.value ?? "")}`
        : esc(f.value ?? "");
      return `<span class="${escAttr(cls)}">${txt}</span>`;
    }).join("");
    return `<div class="metadata-block">${inner}</div>`;
  }
  if (kind === "form") {
    let html = `<div class="config-section">`;
    if (section.title) html += `<h3>${esc(section.title)}</h3>`;
    for (const f of fields) html += buildFieldHtml(f);
    html += `</div>`;
    return html;
  }
  if (kind === "actions") {
    let html = `<div class="config-section actions-section">`;
    for (const item of (section.items || [])) {
      let cls = "";
      if (item.style === "danger") cls = "danger";
      else if (item.style === "primary") cls = "primary";
      // Every action renders as a normal button. The docker is where
      // "ALL" reads naturally as a compact companion next to Cancel;
      // in the web panel there is room, and a label of "ALL" alone
      // does not say what it cancels. `cancel_all` therefore gets the
      // full "Cancel All" label here.
      let label = item.label || item.key;
      if (item.cancel_all) label = "Cancel All";
      html += `<button class="${cls}" data-action-key="${escAttr(item.key)}">${esc(label)}</button>`;
    }
    html += `</div>`;
    return html;
  }
  return "";
}

function buildFieldHtml(f) {
  const key = f.key;
  if (!key) return "";
  const overridden = f.overridden ? " overridden" : "";
  const labelHtml = esc(f.label || key);
  let inner = "";
  const t = f.type;
  if (t === "text") {
    inner = `<input type="text" data-field="${escAttr(key)}" value="${escAttr(f.value ?? "")}">`;
  } else if (t === "textarea") {
    inner = `<textarea data-field="${escAttr(key)}" rows="3">${esc(f.value ?? "")}</textarea>`;
  } else if (t === "integer") {
    const min = f.min != null ? ` min="${escAttr(f.min)}"` : "";
    const max = f.max != null ? ` max="${escAttr(f.max)}"` : "";
    inner = `<input type="number" step="1" data-field="${escAttr(key)}" value="${escAttr(f.value ?? 0)}"${min}${max}>`;
  } else if (t === "number") {
    const min = f.min != null ? ` min="${escAttr(f.min)}"` : "";
    const max = f.max != null ? ` max="${escAttr(f.max)}"` : "";
    inner = `<input type="number" step="any" data-field="${escAttr(key)}" value="${escAttr(f.value ?? 0)}"${min}${max}>`;
  } else if (t === "toggle") {
    inner = `<input type="checkbox" data-field="${escAttr(key)}" ${f.value ? "checked" : ""}>`;
  } else if (t === "select") {
    const opts = (f.options || []).map(o =>
      `<option value="${escAttr(o)}"${String(o) === String(f.value) ? " selected" : ""}>${esc(o)}</option>`
    ).join("");
    inner = `<select data-field="${escAttr(key)}">${opts}</select>`;
  } else if (t === "radio") {
    const grp = (f.options || []).map(o => {
      const checked = String(o) === String(f.value) ? " checked" : "";
      return `<label><input type="radio" name="${escAttr(key)}" data-field="${escAttr(key)}" value="${escAttr(o)}"${checked}> ${esc(o)}</label>`;
    }).join("");
    inner = `<div class="radio-group">${grp}</div>`;
  } else {
    inner = `<input type="text" data-field="${escAttr(key)}" value="${escAttr(f.value ?? "")}">`;
  }
  return `<div class="field${overridden}" data-field-container="${escAttr(key)}">
    <label>${labelHtml}</label>
    ${inner}
    <div class="field-error" data-error-for="${escAttr(key)}"></div>
  </div>`;
}

function rebuildPanel(description) {
  const form = document.getElementById("config-form");
  app.fields = new Map();
  app.specs = new Map();
  app.sections = new Map();
  app.sectionSpecs = description.sections || [];

  let html = "";
  for (const s of app.sectionSpecs) html += buildSectionHtml(s);
  form.innerHTML = html;

  form.querySelectorAll("[data-field]").forEach(el => {
    const key = el.getAttribute("data-field");
    if (!app.fields.has(key)) app.fields.set(key, []);
    app.fields.get(key).push(el);
  });
  for (const s of app.sectionSpecs) {
    for (const f of (s.fields || [])) {
      if (f.key) {
        app.specs.set(f.key, f);
        app.sections.set(f.key, s);
      }
    }
  }

  form.querySelectorAll("[data-action-key]").forEach(btn => {
    btn.addEventListener("click", () => runAction(btn.getAttribute("data-action-key")));
  });

  for (const s of app.sectionSpecs) {
    for (const f of (s.fields || [])) {
      if (f.key && f.visible_when) evaluateVisibility(f.key);
    }
  }

  setRunning(app.running);
}

function syncValues(description) {
  const newSpecs = new Map();
  for (const s of description.sections || []) {
    for (const f of (s.fields || [])) {
      if (f.key) newSpecs.set(f.key, f);
    }
  }
  for (const [key, spec] of newSpecs.entries()) {
    const els = app.fields.get(key) || [];
    if (els.length === 0) continue;
    if (app.focusKeys.has(key)) { app.specs.set(key, spec); continue; }
    if (app.inFlight.has(key)) { app.specs.set(key, spec); continue; }
    for (const el of els) {
      if (el.type === "checkbox") {
        const v = toBool(spec.value);
        if (el.checked !== v) el.checked = v;
      } else if (el.tagName === "SELECT") {
        const target = String(spec.value ?? "");
        if (el.value !== target) el.value = target;
      } else if (el.type === "radio") {
        el.checked = String(spec.value) === String(el.value);
      } else {
        const target = String(spec.value ?? "");
        if (el.value !== target) el.value = target;
      }
    }
    app.specs.set(key, spec);
    const container = document.querySelector(
      `[data-field-container="${cssEsc(key)}"]`);
    if (container) container.classList.toggle("overridden", !!spec.overridden);
  }
  for (const s of app.sectionSpecs) {
    for (const f of (s.fields || [])) {
      if (f.key && f.visible_when) evaluateVisibility(f.key);
    }
  }
}

function evaluateVisibility(key) {
  const spec = app.specs.get(key);
  if (!spec || !spec.visible_when) return;
  let visible = true;
  for (const [otherKey, expected] of Object.entries(spec.visible_when)) {
    const otherSpec = app.specs.get(otherKey);
    if (!otherSpec) continue;
    const els = app.fields.get(otherKey) || [];
    let val = "";
    if (els.length > 0) {
      const el = els[0];
      if (el.type === "checkbox") val = el.checked ? "true" : "false";
      else if (el.type === "radio") {
        for (const e of els) if (e.checked) { val = e.value; break; }
      } else val = el.value;
    }
    if (String(val) !== String(expected)) { visible = false; break; }
  }
  const container = document.querySelector(
    `[data-field-container="${cssEsc(key)}"]`);
  if (container) container.style.display = visible ? "" : "none";
}

function onFocusIn(e) {
  const key = e.target && e.target.getAttribute
    && e.target.getAttribute("data-field");
  if (key) app.focusKeys.add(key);
}
function onFocusOut(e) {
  const key = e.target && e.target.getAttribute
    && e.target.getAttribute("data-field");
  if (key) app.focusKeys.delete(key);
}
function onKeyDown(e) {
  if (e.key !== "Enter") return;
  const el = e.target;
  if (!el || el.tagName !== "INPUT") return;
  if (el.type === "checkbox" || el.type === "radio") return;
  e.preventDefault();
  el.blur();
}
function onFormChange(e) {
  const el = e.target;
  const key = el.getAttribute && el.getAttribute("data-field");
  if (!key) return;
  for (const [k, spec] of app.specs.entries()) {
    if (spec.visible_when && key in spec.visible_when) evaluateVisibility(k);
  }
  commitField(key);
}

function readWidgetValue(el, spec) {
  if (el.type === "checkbox") return el.checked;
  if (el.type === "radio") {
    const els = app.fields.get(el.getAttribute("data-field")) || [];
    for (const e of els) if (e.checked) return e.value;
    return "";
  }
  if (el.tagName === "SELECT") return el.value;
  return el.value;
}

function readAllValues() {
  const out = {};
  for (const [key, els] of app.fields.entries()) {
    if (els.length === 0) continue;
    out[key] = readWidgetValue(els[0], app.specs.get(key));
  }
  return out;
}

function commitField(key) {
  const spec = app.specs.get(key);
  const section = app.sections.get(key);
  if (!spec || !section) return;

  const sectionCommit = section.commit;
  let command = null;
  if (sectionCommit) {
    const args = {};
    for (const [fk, argname] of Object.entries(sectionCommit.arg_map || {})) {
      const els = app.fields.get(fk) || [];
      if (els.length === 0) continue;
      args[argname] = readWidgetValue(els[0], app.specs.get(fk));
    }
    command = Object.assign({type: sectionCommit.type}, args);
  } else if (spec.set_command) {
    const argKey = spec.arg_key || "value";
    command = Object.assign({}, spec.set_command);
    const els = app.fields.get(key) || [];
    command[argKey] = els.length > 0 ? readWidgetValue(els[0], spec) : "";
  }
  if (!command) return;

  const requestId = "field:" + key;
  const doIt = () => sendCommand(requestId, command, [key]);
  app.commitQueue = app.commitQueue.then(doIt, doIt);
}

async function sendCommand(requestId, command, affectedKeys) {
  for (const k of affectedKeys) {
    app.inFlight.add(k);
    const c = document.querySelector(`[data-field-container="${cssEsc(k)}"]`);
    if (c) c.classList.add("in-flight");
  }
  try {
    const resp = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command: command}),
    });
    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try { const b = await resp.json(); if (b && b.error) msg = b.error; } catch (_) {}
      for (const k of affectedKeys) setFieldError(k, msg);
      revertFields(affectedKeys);
      return false;
    }
    for (const k of affectedKeys) setFieldError(k, "");
    return true;
  } catch (e) {
    for (const k of affectedKeys) setFieldError(k, "network error: " + e.message);
    revertFields(affectedKeys);
    return false;
  } finally {
    for (const k of affectedKeys) {
      app.inFlight.delete(k);
      const c = document.querySelector(`[data-field-container="${cssEsc(k)}"]`);
      if (c) c.classList.remove("in-flight");
    }
  }
}

function setFieldError(key, msg) {
  const el = document.querySelector(`[data-error-for="${cssEsc(key)}"]`);
  if (el) el.textContent = msg || "";
}

function revertFields(keys) {
  if (!app.latestPanel) return;
  const savedFocus = new Set();
  const savedInFlight = new Set();
  for (const k of keys) {
    if (app.focusKeys.has(k)) { savedFocus.add(k); app.focusKeys.delete(k); }
    if (app.inFlight.has(k)) { savedInFlight.add(k); app.inFlight.delete(k); }
  }
  syncValues(app.latestPanel);
  for (const k of savedFocus) app.focusKeys.add(k);
  for (const k of savedInFlight) app.inFlight.add(k);
}

async function runAction(key) {
  const section = app.sectionSpecs.find(
    s => s.kind === "actions" && (s.items || []).some(i => i.key === key));
  if (!section) return;
  const item = section.items.find(i => i.key === key);
  if (!item) return;

  const command = Object.assign({}, item.command || {});
  const allValues = readAllValues();
  for (const [fieldKey, argName] of Object.entries(item.collect || {})) {
    if (!(fieldKey in allValues)) continue;
    const v = allValues[fieldKey];
    if (argName === "seed" || argName === "output_name") {
      if (v !== null && v !== "") command[argName] = v;
    } else if (argName === "count") {
      const iv = parseInt(v, 10);
      if (!isNaN(iv) && iv > 1) command[argName] = iv;
    } else if (argName === "timeout") {
      const fv = parseFloat(v);
      if (!isNaN(fv)) command[argName] = fv;
    } else {
      command[argName] = v;
    }
  }

  const cancelAll = key === "cancel_all" || item.cancel_all === true;

  if (key === "run") {
    await startRun(command);
  } else if (key === "cancel" || cancelAll) {
    await cancelRun(command, cancelAll ? "all" : "current");
  } else {
    const requestId = "action:" + key;
    await sendCommand(requestId, command, []);
  }
}

function rowDiv(cls, text) {
  const d = document.createElement("div");
  d.className = "row " + cls;
  d.textContent = text;
  return d;
}

function setRunning(running) {
  app.running = running;
  for (const s of app.sectionSpecs) {
    if (s.kind !== "actions") continue;
    for (const item of (s.items || [])) {
      const btn = document.querySelector(
        `[data-action-key="${cssEsc(item.key)}"]`);
      if (!btn) continue;
      if (item.key === "run") btn.disabled = running;
      else if (item.key === "cancel" || item.key === "cancel_all")
        btn.disabled = !running;
    }
  }
}

async function startRun(command) {
  const log = document.getElementById("progress");
  log.innerHTML = "";
  setRunning(true);
  log.appendChild(rowDiv("hdr", "Starting run\u2026"));

  try { await app.commitQueue; } catch (_) {}

  let resp;
  try {
    resp = await fetch("/api/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command: command}),
    });
  } catch (e) {
    log.innerHTML = "";
    log.appendChild(rowDiv("err", "Request failed: " + e.message));
    setRunning(false);
    return;
  }
  if (!resp.ok) {
    let msg = "HTTP " + resp.status;
    try { const b = await resp.json(); if (b && b.error) msg = b.error; } catch (_) {}
    log.innerHTML = "";
    log.appendChild(rowDiv("err", "Cannot run: " + msg));
    setRunning(false);
    return;
  }
  const body = await resp.json();
  log.innerHTML = "";
  log.appendChild(rowDiv("hdr", "Run " + body.run_id + " started."));
}

async function cancelRun(command, scope) {
  const log = document.getElementById("progress");
  setRunning(true);
  let resp;
  try {
    resp = await fetch("/api/cancel", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command: command, scope: scope || "current"}),
    });
  } catch (e) {
    log.appendChild(rowDiv("err", "Cancel request failed: " + e.message));
    setRunning(false);
    return;
  }
  if (!resp.ok) {
    let msg = "HTTP " + resp.status;
    try { const b = await resp.json(); if (b && b.error) msg = b.error; } catch (_) {}
    log.appendChild(rowDiv("err", "Cannot cancel: " + msg));
    if (resp.status === 409) setRunning(false);
  }
}

function handleRunEvent(e) {
  const s = JSON.parse(e.data);
  const log = document.getElementById("progress");
  if (s.status === "progress") {
    const cls = s.run_status === "success" ? "ok"
               : s.run_status === "error" ? "err" : "";
    log.appendChild(rowDiv(cls,
      "[" + (s.index + 1) + "/" + s.total + "] "
      + s.command + ": " + s.message));
  } else if (s.status === "cancelling") {
    log.appendChild(rowDiv("warn", "Cancelling\u2026"));
  } else if (s.status === "cancelled") {
    const n = s.cancelled != null ? s.cancelled : "";
    log.appendChild(rowDiv("warn",
      "Cancelled. " + n + " run(s) not completed."));
    setRunning(false);
  } else if (s.status === "done") {
    let text = "Done. " + s.successful + " ok, " + s.failed + " failed.";
    if (s.cancelled) text += " (" + s.cancelled + " cancelled)";
    log.appendChild(rowDiv("hdr", text));
    setRunning(false);
  } else if (s.status === "error") {
    log.appendChild(rowDiv("err", "Run failed: " + (s.error || "unknown")));
    setRunning(false);
  }
}

function connectEvents() {
  const es = new EventSource("/api/events");

  es.addEventListener("open", function () {
    app.eventsConnected = true;
  });

  es.addEventListener("state", function (e) {
    try {
      const s = JSON.parse(e.data);
      app.latestState = s;
      renderStatus(s);
    } catch (err) {
      console.error("state parse error", err);
    }
  });

  es.addEventListener("panel", function (e) {
    try {
      const desc = JSON.parse(e.data);
      if (!desc || typeof desc !== "object") return;
      app.latestPanel = desc;
      app.firstPanelReceived = true;
      if (desc.structure_id !== app.panelStructureId) {
        app.panelStructureId = desc.structure_id;
        rebuildPanel(desc);
      } else {
        syncValues(desc);
      }
    } catch (err) {
      console.error("panel parse error", err);
    }
  });

  es.addEventListener("run", handleRunEvent);

  es.onerror = function () {
    // EventSource reconnects automatically.
  };

  setTimeout(function () {
    if (app.firstPanelReceived) return;
    const form = document.getElementById("config-form");
    if (!form) return;
    form.innerHTML = app.eventsConnected
      ? '<div class="empty">Connected to the panel; waiting for a '
        + 'panel description from the daemon. Is the daemon running '
        + 'with the latest code?</div>'
      : '<div class="empty">Could not reach the panel event stream on '
        + '/api/events. Check that the web server is running.</div>';
  }, 4000);
}

(function attachFormListeners() {
  const form = document.getElementById("config-form");
  if (!form) return;
  form.addEventListener("change", onFormChange);
  form.addEventListener("focusin", onFocusIn);
  form.addEventListener("focusout", onFocusOut);
  form.addEventListener("keydown", onKeyDown);
})();

renderStatus(null);

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
