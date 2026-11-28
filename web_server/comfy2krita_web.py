#!/usr/bin/env python3
"""
comfy2krita_web.py — standalone browser view of the daemon's state.

A pure HTTP client of the daemon. Reads the same event stream the Krita
docker reads, renders the same panel description the docker renders,
and posts the same commands to the same daemon command socket.

Parity notes vs. the docker
===========================
- Layer-name fields are plain text inputs. No layer list, no completer.
- Playground section renders both `layer_slots` (skipping
  `managed_by_playground: true`) and `control_params`.
- Intercept summary is recognised and shown as "review in interceptor
  playground".
- After a structure change, a config push is scheduled so the daemon's
  `_active_config` stays in sync.

Debug logging
=============
Every command POST, every daemon call, every run start/finish prints
to stdout with a `[web_panel]` prefix. The HTML also logs every action
click to the progress area, and any JS exception / unhandled rejection
is captured and displayed inline.
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


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic.client import KritaClient


HOST = os.environ.get("WEB_PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEB_PANEL_PORT", "8190"))

DAEMON_HOST = "localhost"
DAEMON_PORT = 12346
DAEMON_EVENT_PORT = 12347

RECONNECT_DELAY = 2.0
SSE_HEARTBEAT = 15.0
SUBSCRIBER_QUEUE_SIZE = 256

RUN_READ_TIMEOUT_MARGIN = 60.0

# How long to wait for the daemon's first panel push before falling
# back to a direct `panel_description` command.  The daemon pushes a
# panel after every state event, but the browser might connect *before*
# the daemon has ever published anything (fresh daemon, no workflow
# loaded yet, no playground registered yet).  Without this fallback
# the form area stays blank and the user has no way to trigger a
# workflow load via the UI.
INITIAL_PANEL_FALLBACK_S = 2.0


def _log(msg: str) -> None:
    print(f"[web_panel] {msg}", flush=True)


_state = {
    "daemon": None,
    "panel": None,
    "daemon_connected": False,
    "last_update": 0.0,
    "last_event": None,
    "last_error": None,
}
_state_lock = threading.Lock()


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


def _daemon_event_client() -> None:
    while True:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((DAEMON_HOST, DAEMON_EVENT_PORT))
            _log(f"connected to daemon event stream on "
                 f"{DAEMON_HOST}:{DAEMON_EVENT_PORT}")

            with _state_lock:
                _state["daemon_connected"] = True
                _state["last_error"] = None
            _broadcast_state()

            buffer = b""
            sock.settimeout(None)
            while True:
                try:
                    chunk = sock.recv(65536)
                except (ConnectionResetError, OSError) as e:
                    _log(f"daemon event read error: {e}")
                    break
                if not chunk:
                    _log("daemon event stream closed by peer")
                    break
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except Exception as e:
                        _log(f"daemon event parse error: {e}")
                        continue
                    _handle_daemon_event(event)

        except Exception as e:
            _log(f"daemon event connect failed: {e}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            with _state_lock:
                was_connected = _state["daemon_connected"]
                _state["daemon_connected"] = False
                _state["last_error"] = "daemon event stream disconnected"
            if was_connected:
                _log("daemon event stream disconnected; reconnecting")
            _broadcast_state()

        time.sleep(RECONNECT_DELAY)


def _daemon_call(command: dict) -> tuple:
    cmd_type = command.get("type", "?")
    client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
    if not client.connect():
        _log(f"daemon_call {cmd_type} → CONNECT FAILED")
        return False, "could not connect to daemon", {}
    try:
        args = {k: v for k, v in command.items() if k != "type"}
        resp = client.execute(cmd_type, **args)
    except Exception as e:
        _log(f"daemon_call {cmd_type} → EXCEPTION: {e}")
        return False, str(e), {}
    finally:
        client.close()

    if not resp:
        _log(f"daemon_call {cmd_type} → NO RESPONSE")
        return False, "no response from daemon", {}
    if resp.get("status") != "success":
        msg = resp.get("message", "daemon refused the command")
        _log(f"daemon_call {cmd_type} → ERROR: {msg}")
        return False, msg, {}
    _log(f"daemon_call {cmd_type} → ok")
    return True, resp.get("message", ""), resp.get("data") or {}


def _fetch_initial_panel() -> None:
    """If no panel has arrived after INITIAL_PANEL_FALLBACK_S, poll it
    directly via `panel_description`.  The daemon pushes a panel after
    every state event, but a fresh daemon that has never published
    anything leaves the browser with an empty form and no button to
    click.  This fallback is what closes that window."""
    time.sleep(INITIAL_PANEL_FALLBACK_S)
    with _state_lock:
        if _state["panel"] is not None:
            return
        if not _state["daemon_connected"]:
            return
    _log("no initial panel received; polling panel_description")
    ok, msg, data = _daemon_call({"type": "panel_description"})
    if ok and isinstance(data, dict):
        with _state_lock:
            _state["panel"] = data
        _broadcast("panel", data)
        _log("initial panel_description fetched")
    else:
        _log(f"initial panel_description failed: {msg}")


_runs: dict = {}
_runs_lock = threading.Lock()


def _new_run_id() -> str:
    return f"run-{int(time.time())}-{random.randint(0, 9999):04d}"


def _do_run(run_id: str, command: dict) -> None:
    def emit(status: str, **extra):
        payload = {"run_id": run_id, "status": status}
        payload.update(extra)
        _broadcast("run", payload)

    _log(f"run {run_id} starting command={command.get('type')}")
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
        _log(f"run {run_id} read_timeout={read_timeout}s")

        bundle = {"id": run_id, "commands": [command]}

        client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
        if not client.connect():
            _log(f"run {run_id} daemon connect FAILED")
            emit("error", error="could not connect to daemon")
            return

        try:
            def on_progress(index, result_entry):
                cmd_type = result_entry.get("command", "?")
                status = result_entry.get("status", "?")
                msg = result_entry.get("message", "")
                _log(f"run {run_id} progress {index}: {cmd_type} {status}: {msg}")
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
            _log(f"run {run_id} no summary from daemon")
            emit("error", error="no summary from daemon")
            return

        _log(f"run {run_id} summary type={summary.get('type')} "
             f"status={summary.get('status')}")

        intercepted = bool(summary.get("intercepted"))
        if not intercepted:
            for r in (summary.get("results") or []):
                if (isinstance(r, dict)
                        and isinstance(r.get("data"), dict)
                        and r["data"].get("intercepted")):
                    intercepted = True
                    break
        if intercepted:
            _log(f"run {run_id} intercepted")
            emit("intercepted",
                 message=summary.get("message", "Run intercepted"))
            return

        succ = summary.get("successful", 0)
        fail = summary.get("failed", 0)

        results = summary.get("results", []) or []
        cancelled_count = sum(
            1 for r in results
            if r.get("status") == "error"
            and "cancelled" in (r.get("message") or "").lower()
        )
        _log(f"run {run_id} done: {succ} ok, {fail} failed, "
             f"{cancelled_count} cancelled")
        if cancelled_count > 0 and succ == 0:
            emit("cancelled",
                 successful=succ, failed=fail,
                 cancelled=cancelled_count)
        else:
            emit("done",
                 successful=succ, failed=fail,
                 cancelled=cancelled_count)

    except Exception as e:
        _log(f"run {run_id} exception: {e}")
        traceback.print_exc()
        emit("error", error=str(e))
    finally:
        with _runs_lock:
            _runs.pop(run_id, None)


def _send_daemon_cancel(batch_id) -> tuple:
    _log(f"cancel request batch_id={batch_id!r}")
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


def _fetch_batches() -> list:
    client = KritaClient(host=DAEMON_HOST, port=DAEMON_PORT)
    if not client.connect():
        return []
    try:
        resp = client.execute("comfy_batches")
    except Exception:
        return []
    finally:
        client.close()
    if not resp or resp.get("status") != "success":
        return []
    data = resp.get("data") or {}
    return list(data.get("batches") or [])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KritomaticWebPanel/2.6"

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200,
                   content_type="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status=status)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > 1048576:
            return None
        try:
            body = self.rfile.read(length)
        except (ConnectionError, OSError):
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                self._send_text(HTML_PAGE,
                                content_type="text/html; charset=utf-8")
            elif self.path == "/api/state":
                with _state_lock:
                    self._send_json(dict(_state))
            elif self.path == "/api/batches":
                self._send_json({"batches": _fetch_batches()})
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
        _log("SSE client connected")
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
            _log("SSE client disconnected")

    def _write_sse(self, event_type: str, payload) -> None:
        data = json.dumps(payload)
        msg = f"event: {event_type}\ndata: {data}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

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
            _log("command POST: bad body")
            self._send_error_json("expected a JSON object", status=400)
            return
        command = body.get("command")
        if not isinstance(command, dict) or "type" not in command:
            _log(f"command POST: bad command shape {command!r}")
            self._send_error_json(
                "'command' must be an object with a 'type'", status=400)
            return
        _log(f"command POST: {command.get('type')}")
        ok, msg, data = _daemon_call(command)
        if ok:
            self._send_json({"ok": True, "message": msg, "data": data})
        else:
            self._send_error_json(msg, status=502)

    def _handle_run_post(self):
        body = self._read_json_body()
        if not isinstance(body, dict):
            _log("run POST: bad body")
            self._send_error_json("expected a JSON object", status=400)
            return
        command = body.get("command")
        if not isinstance(command, dict) \
                or command.get("type") not in ("comfy_generate",
                                               "comfy_panel_run"):
            _log(f"run POST: bad command {command!r}")
            self._send_error_json(
                "'command' must be a comfy_generate or comfy_panel_run "
                "command", status=400)
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
        _log(f"run POST: started {run_id}")
        self._send_json({"run_id": run_id})

    def _handle_cancel_post(self):
        body = self._read_json_body()
        if not isinstance(body, dict):
            body = {}

        scope = body.get("scope", "current")
        _log(f"cancel POST: scope={scope}")

        if scope == "all":
            with _runs_lock:
                run_ids = list(_runs.keys())
                for rid in run_ids:
                    r = _runs.get(rid)
                    if r is not None:
                        r["cancel_requested"] = True

            for rid in run_ids:
                _broadcast("run", {"run_id": rid, "status": "cancelling"})

            ok, msg = _send_daemon_cancel(None)
            if not ok:
                _log(f"cancel_all failed: {msg}")
                self._send_error_json(msg, status=502)
                return

            _log("cancel_all requested")
            self._send_json({"status": "cancelling_all", "message": msg,
                             "run_ids": run_ids})
            return

        with _runs_lock:
            run_ids = list(_runs.keys())
            for rid in run_ids:
                r = _runs.get(rid)
                if r is not None:
                    r["cancel_requested"] = True

        if not run_ids:
            self._send_error_json("no run is in progress", status=409)
            return

        errors = []
        for rid in run_ids:
            _broadcast("run", {"run_id": rid, "status": "cancelling"})
            ok, msg = _send_daemon_cancel(rid)
            if not ok:
                errors.append(f"{rid}: {msg}")
        _log(f"cancel requested for {len(run_ids)} run(s)")

        self._send_json({
            "run_ids": run_ids,
            "status": "cancelling",
            "errors": errors,
        })


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
  .field.field-row {
    align-items: center;
  }
  .row-container {
    display: flex;
    gap: 0.4rem;
    align-items: center;
    min-width: 0;
  }
  .sub-field {
    min-width: 0;
    box-sizing: border-box;
  }
  .sub-field select,
  .sub-field input[type=text],
  .sub-field input[type=number] {
    width: 100%;
    box-sizing: border-box;
    font: inherit;
    font-size: 0.85rem;
    padding: 0.3rem 0.5rem;
    border: 1px solid #ccc;
    border-radius: 3px;
    background: white;
  }
  .sub-field select:focus,
  .sub-field input[type=text]:focus,
  .sub-field input[type=number]:focus {
    outline: 2px solid #1a73e8;
    outline-offset: -1px;
    border-color: transparent;
  }
  .sub-field input[type=checkbox] {
    margin: 0;
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

  .workflow-bar {
    display: flex; gap: 6px; align-items: center;
  }
  .workflow-bar select,
  .workflow-bar input[type=text] {
    flex: 1;
    font: inherit; font-size: 0.85rem;
    padding: 0.3rem 0.5rem;
    border: 1px solid #ccc; border-radius: 3px;
    background: white;
  }
  .workflow-bar button { flex: 0 0 auto; }

  .actions-section { display: flex; gap: 0.5rem; }
  button {
    font: inherit; padding: 0.5rem 1rem;
    cursor: pointer; border: none; border-radius: 4px;
  }
  button.primary { background: #1a73e8; color: white; }
  button.danger { background: #c5221f; color: white; }
  button:disabled { background: #c8c8c8; color: #f0f0f0;
                    cursor: not-allowed; }

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

  .playgrounds-section {
    background: #fafafa;
    border: 1px solid #eee;
    border-radius: 4px;
    padding: 0.75rem 1rem;
    min-height: 1.5rem;
  }
  .playground-row {
    display: grid;
    grid-template-columns: 140px 110px 100px 1fr;
    gap: 0.5rem;
    align-items: center;
    padding: 0.35rem 0;
    border-bottom: 1px solid #f0f0f0;
    font-size: 0.85rem;
  }
  .playground-row:last-child { border-bottom: none; }
  .playground-row .name { font-weight: 600; color: #333; }
  .playground-row .slot-id,
  .playground-row .param-name {
    color: #555; font-family: ui-monospace, monospace;
  }
  .playground-row select,
  .playground-row input[type=text],
  .playground-row input[type=number] {
    font: inherit;
    font-size: 0.85rem;
    padding: 0.25rem 0.4rem;
    border: 1px solid #ccc;
    border-radius: 3px;
    background: white;
  }
  .playground-row input[type=text][hidden] { display: none; }
  .playground-row input[type=checkbox] { margin: 0; }
  .playground-row .stretch { min-width: 0; }
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

<h2>Playground Input
  <span class="tag">daemon-registered</span>
</h2>
<div class="playgrounds-section" id="playgrounds">
  <div class="empty">No playgrounds running.</div>
</div>

<div class="progress" id="progress"></div>

<script>
"use strict";

// ---- Global error capture (before anything else) ----
// Any JS error or unhandled promise rejection is dumped into the
// progress log so the user can see it without opening devtools.
function logFatal(msg) {
  const log = document.getElementById("progress");
  if (!log) return;
  const d = document.createElement("div");
  d.className = "row err";
  d.textContent = "[JS ERROR] " + msg;
  log.appendChild(d);
}
window.addEventListener("error", function (e) {
  logFatal((e.message || "error") + " at " + (e.filename || "?")
           + ":" + (e.lineno || "?"));
});
window.addEventListener("unhandledrejection", function (e) {
  const r = e.reason;
  logFatal("unhandled rejection: "
           + (r && r.message ? r.message : String(r)));
});

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
  activeRuns: new Map(),
  activeBatchesCount: 0,
  eventsConnected: false,
  firstPanelReceived: false,
  currentWorkflow: "",
  paramPrefixByWorkflow: new Map(),
  localHandlers: new Map(),
};

function logLine(kind, text) {
  const log = document.getElementById("progress");
  if (!log) return;
  const d = document.createElement("div");
  d.className = "row " + kind;
  d.textContent = text;
  log.appendChild(d);
}

(function restoreParamPrefixMap() {
  try {
    const raw = localStorage.getItem("kritomatic_param_prefix_by_workflow");
    if (!raw) return;
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== "object") return;
    for (const [k, v] of Object.entries(obj)) {
      if (typeof k === "string" && typeof v === "string") {
        app.paramPrefixByWorkflow.set(k, v);
      }
    }
  } catch (_) {}
})();

function persistParamPrefixMap() {
  try {
    const obj = {};
    for (const [k, v] of app.paramPrefixByWorkflow.entries()) {
      obj[k] = v;
    }
    localStorage.setItem(
      "kritomatic_param_prefix_by_workflow", JSON.stringify(obj),
    );
  } catch (_) {}
}

app.localHandlers.set("param_prefix_filter", function (prefix) {
  const wf = app.currentWorkflow;
  if (wf) {
    app.paramPrefixByWorkflow.set(wf, String(prefix || ""));
    persistParamPrefixMap();
  }
  if (app.latestPanel) {
    rebuildPanel(app.latestPanel);
  }
});

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

function isCancelAllItem(item) {
  return item && (item.cancel_all === true || item.key === "cancel_all");
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
    lines.push('<span class="dim">Select a workflow in the Workflow bar below, then click Load.</span>');
  } else {
    el.classList.add("ok");
    const d = s.daemon;
    if (d.connected_to) {
      lines.push("Connected to ComfyUI at " + esc(d.connected_to));
    } else {
      lines.push("Connected to the daemon.");
    }
  }
  if (s && s.last_event) {
    lines.push('<span class="dim">Last event: ' + esc(s.last_event) + '</span>');
  }
  el.innerHTML = lines.map(l => "<div>" + l + "</div>").join("")
    || '<div class="empty">(no state)</div>';
}

function _filteredDescription(description) {
  if (!description) return description;
  const prefix = app.paramPrefixByWorkflow.get(app.currentWorkflow) || "";
  if (!prefix) return description;
  try {
    const copy = JSON.parse(JSON.stringify(description));
    for (const s of copy.sections || []) {
      for (const f of s.fields || []) {
        const key = f.key || "";
        if (key.startsWith("cfg-param.")) {
          const title = key.substring("cfg-param.".length);
          if (!title.startsWith(prefix)) {
            f.visible = false;
          }
        }
      }
    }
    return copy;
  } catch (_) {
    return description;
  }
}

function buildWorkflowBarHtml(section) {
  let html = `<div class="config-section">`;
  if (section.title) html += `<h3>${esc(section.title)}</h3>`;
  html += `<div class="workflow-bar">`;
  for (const f of (section.fields || [])) {
    const key = f.key;
    const value = f.value ?? "";
    if (f.type === "select") {
      const opts = (f.options || []).map(o =>
        `<option value="${escAttr(o)}"${String(o) === String(value) ? " selected" : ""}>${esc(o)}</option>`
      ).join("");
      html += `<select data-field="${escAttr(key)}">${opts}</select>`;
    } else {
      html += `<input type="text" data-field="${escAttr(key)}" value="${escAttr(value)}">`;
    }
  }
  for (const item of (section.items || [])) {
    let cls = "";
    if (item.style === "danger") cls = "danger";
    else if (item.style === "primary") cls = "primary";
    let label = item.label || item.key;
    html += `<button class="${cls}" data-action-key="${escAttr(item.key)}">${esc(label)}</button>`;
  }
  html += `</div></div>`;
  return html;
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
  if (kind === "workflow-bar") {
    return buildWorkflowBarHtml(section);
  }
  if (kind === "actions") {
    let html = `<div class="config-section actions-section">`;
    for (const item of (section.items || [])) {
      let cls = "";
      if (item.style === "danger") cls = "danger";
      else if (item.style === "primary") cls = "primary";
      let label = item.label || item.key;
      if (item.cancel_all) label = "Cancel All";
      html += `<button class="${cls}" data-action-key="${escAttr(item.key)}">${esc(label)}</button>`;
    }
    html += `</div>`;
    return html;
  }
  return "";
}

function buildSubFieldHtml(f) {
  const key = f.key;
  if (!key) return "";
  const t = f.type;
  let inner = "";
  if (t === "select") {
    const opts = (f.options || []).map(o =>
      `<option value="${escAttr(o)}"${String(o) === String(f.value) ? " selected" : ""}>${esc(o)}</option>`
    ).join("");
    inner = `<select data-field="${escAttr(key)}">${opts}</select>`;
  } else if (t === "textarea") {
    inner = `<textarea data-field="${escAttr(key)}" rows="2">${esc(f.value ?? "")}</textarea>`;
  } else if (t === "integer" || t === "number") {
    const step = t === "integer" ? "1" : "any";
    inner = `<input type="number" step="${step}" data-field="${escAttr(key)}" value="${escAttr(f.value ?? 0)}">`;
  } else if (t === "toggle") {
    inner = `<input type="checkbox" data-field="${escAttr(key)}" ${f.value ? "checked" : ""}>`;
  } else {
    inner = `<input type="text" data-field="${escAttr(key)}" value="${escAttr(f.value ?? "")}">`;
  }
  const stretch = f.stretch != null ? Number(f.stretch) : 0;
  const flexStyle = stretch > 0
    ? `flex: ${stretch} 1 0;`
    : `flex: 0 0 auto;`;
  return `<div class="sub-field" data-field-container="${escAttr(key)}" style="${flexStyle}">${inner}</div>`;
}

function buildFieldHtml(f) {
  const key = f.key;
  if (!key) return "";
  const overridden = f.overridden ? " overridden" : "";
  const labelHtml = esc(f.label || key);
  const hiddenStyle = f.visible === false ? ' style="display:none;"' : "";
  let inner = "";
  const t = f.type;
  if (t === "row") {
    const subs = (f.sub_fields || []).map(buildSubFieldHtml).join("");
    return `<div class="field field-row" data-row-key="${escAttr(key)}"${hiddenStyle}>
    <label>${esc(f.label || "")}</label>
    <div class="row-container">${subs}</div>
  </div>`;
  }
  if (t === "text") {
    const lhAttr = f.local_handler
      ? ` data-local-handler="${escAttr(f.local_handler)}"` : "";
    let value = f.value ?? "";
    if (f.local_handler === "param_prefix_filter") {
      value = app.paramPrefixByWorkflow.get(app.currentWorkflow) || "";
    }
    inner = `<input type="text" data-field="${escAttr(key)}"${lhAttr} value="${escAttr(value)}">`;
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
  return `<div class="field${overridden}" data-field-container="${escAttr(key)}"${hiddenStyle}>
    <label>${labelHtml}</label>
    ${inner}
    <div class="field-error" data-error-for="${escAttr(key)}"></div>
  </div>`;
}

function rebuildPanel(description) {
  const filtered = _filteredDescription(description);
  const form = document.getElementById("config-form");
  app.fields = new Map();
  app.specs = new Map();
  app.sections = new Map();
  app.sectionSpecs = (filtered && filtered.sections) || [];

  let html = "";
  for (const s of app.sectionSpecs) html += buildSectionHtml(s);
  if (!html) {
    html = '<div class="empty">Panel is empty. Load a workflow using '
         + 'the Workflow bar above.</div>';
  }
  form.innerHTML = html;

  form.querySelectorAll("[data-field]").forEach(el => {
    const key = el.getAttribute("data-field");
    if (!key) return;
    if (!app.fields.has(key)) app.fields.set(key, []);
    app.fields.get(key).push(el);
  });
  for (const s of app.sectionSpecs) {
    for (const f of (s.fields || [])) {
      if (f.type === "row" && Array.isArray(f.sub_fields)) {
        for (const sub of f.sub_fields) {
          if (sub && sub.key) {
            app.specs.set(sub.key, sub);
            app.sections.set(sub.key, s);
          }
        }
      } else if (f.key) {
        app.specs.set(f.key, f);
        app.sections.set(f.key, s);
      }
    }
  }

  form.querySelectorAll("[data-action-key]").forEach(btn => {
    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      const k = btn.getAttribute("data-action-key");
      logLine("hdr", "Click: " + k);
      runAction(k).catch(function (e) {
        logLine("err", "runAction(" + k + ") failed: "
                + (e && e.message ? e.message : String(e)));
      });
    });
  });

  for (const s of app.sectionSpecs) {
    for (const f of (s.fields || [])) {
      if (f.type === "row" && Array.isArray(f.sub_fields)) {
        for (const sub of f.sub_fields) {
          if (sub && sub.key && sub.visible_when) evaluateVisibility(sub.key);
        }
      } else if (f.key && f.visible_when) {
        evaluateVisibility(f.key);
      }
    }
  }

  updateRunButtons();
}

function syncValues(description) {
  const filtered = _filteredDescription(description);
  const newSpecs = new Map();
  for (const s of (filtered && filtered.sections) || []) {
    for (const f of (s.fields || [])) {
      if (f.type === "row" && Array.isArray(f.sub_fields)) {
        for (const sub of f.sub_fields) {
          if (sub && sub.key) newSpecs.set(sub.key, sub);
        }
      } else if (f.key) {
        newSpecs.set(f.key, f);
      }
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
        if (spec.local_handler) continue;
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
      if (f.type === "row" && Array.isArray(f.sub_fields)) {
        for (const sub of f.sub_fields) {
          if (sub && sub.key && sub.visible_when) evaluateVisibility(sub.key);
        }
      } else if (f.key && f.visible_when) {
        evaluateVisibility(f.key);
      }
    }
  }
  updateRunButtons();
}

function evaluateVisibility(key) {
  const spec = app.specs.get(key);
  if (!spec) return;
  if (spec.visible === false) {
    const container = document.querySelector(
      `[data-field-container="${cssEsc(key)}"]`);
    if (container) container.style.display = "none";
    return;
  }
  if (!spec.visible_when) return;
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

const APPLY_DEBOUNCE_MS = 150;
let _applyTimer = null;
let _applyChain = Promise.resolve();

function readConfigWidgetValue(el, spec) {
  const t = spec.type;
  if (t === "integer") {
    const n = parseInt(el.value, 10);
    return Number.isFinite(n) ? n : 0;
  }
  if (t === "number") {
    const n = parseFloat(el.value);
    return Number.isFinite(n) ? n : 0.0;
  }
  if (t === "toggle") return !!el.checked;
  return el.value;
}

function buildConfigFromWidgets() {
  const cfg = { resources: {}, params: {}, crop: {}, paste: {} };
  for (const [key, spec] of app.specs.entries()) {
    try {
      const path = spec.config_path;
      if (!Array.isArray(path) || path.length === 0) continue;
      const els = app.fields.get(key) || [];
      if (els.length === 0) continue;
      const value = readConfigWidgetValue(els[0], spec);
      let d = cfg;
      for (let i = 0; i < path.length - 1; i++) {
        const step = path[i];
        if (!(step in d) || typeof d[step] !== "object" || d[step] === null) {
          d[step] = {};
        }
        d = d[step];
      }
      d[path[path.length - 1]] = value;
    } catch (e) {
      logLine("err", "buildConfigFromWidgets skipped " + key
              + ": " + (e && e.message ? e.message : String(e)));
    }
  }
  return cfg;
}

function _configFieldKeys() {
  const keys = [];
  for (const [key, spec] of app.specs.entries()) {
    if (Array.isArray(spec.config_path) && spec.config_path.length) {
      keys.push(key);
    }
  }
  return keys;
}

function scheduleApply() {
  if (_applyTimer !== null) clearTimeout(_applyTimer);
  _applyTimer = setTimeout(() => {
    _applyTimer = null;
    _applyChain = _applyChain.then(flushApply, flushApply);
  }, APPLY_DEBOUNCE_MS);
}

async function flushApply() {
  const cfg = buildConfigFromWidgets();
  const command = {
    type: "comfy_apply_config",
    resources: JSON.stringify(cfg.resources),
    params:    JSON.stringify(cfg.params),
    crop:      JSON.stringify(cfg.crop),
    paste:     JSON.stringify(cfg.paste),
  };
  return sendCommand("apply", command, [], _configFieldKeys(), false);
}

async function flushPendingApply() {
  while (true) {
    if (_applyTimer !== null) {
      clearTimeout(_applyTimer);
      _applyTimer = null;
      _applyChain = _applyChain.then(flushApply, flushApply);
      continue;
    }
    if (_applyChain === null) return;
    const p = _applyChain;
    _applyChain = null;
    try { await p; } catch (_) {}
  }
}

function commitField(key) {
  const spec = app.specs.get(key);
  const section = app.sections.get(key);
  if (!spec || !section) return;

  if (spec.local_handler) {
    const handler = app.localHandlers.get(spec.local_handler);
    if (!handler) return;
    const els = app.fields.get(key) || [];
    const value = els.length > 0 ? readWidgetValue(els[0], spec) : "";
    try { handler(value); } catch (e) {
      logLine("err", "local handler failed: "
              + (e && e.message ? e.message : String(e)));
    }
    return;
  }

  if (Array.isArray(spec.config_path) && spec.config_path.length) {
    scheduleApply();
    return;
  }

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
  const doIt = () => sendCommand(requestId, command, [key], undefined, false);
  app.commitQueue = app.commitQueue.then(doIt, doIt);
}

async function sendCommand(requestId, command, affectedKeys, inFlightKeys,
                            verbose) {
  const track = inFlightKeys !== undefined ? inFlightKeys : affectedKeys;
  for (const k of track) {
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
      if (verbose) {
        logLine("err", "Command " + (command.type || "?")
                + " failed: " + msg);
      }
      revertFields(track);
      return false;
    }
    for (const k of affectedKeys) setFieldError(k, "");
    if (verbose) {
      let data = null;
      try { data = await resp.json(); } catch (_) {}
      const msg = (data && data.message) || "ok";
      logLine("ok", (command.type || "?") + ": " + msg);
    }
    return true;
  } catch (e) {
    for (const k of affectedKeys) {
      setFieldError(k, "network error: "
                    + (e && e.message ? e.message : String(e)));
    }
    if (verbose) {
      logLine("err", "Command " + (command.type || "?")
              + " network error: "
              + (e && e.message ? e.message : String(e)));
    }
    revertFields(track);
    return false;
  } finally {
    for (const k of track) {
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
    s => (s.kind === "actions" || s.kind === "workflow-bar")
      && (s.items || []).some(i => i.key === key));
  if (!section) {
    logLine("err", "runAction: no section found for key=" + key);
    return;
  }
  const item = section.items.find(i => i.key === key);
  if (!item) {
    logLine("err", "runAction: no item found for key=" + key);
    return;
  }

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

  const cancelAll = isCancelAllItem(item);

  if (key === "run") {
    const cfg = buildConfigFromWidgets();
    command.resources = JSON.stringify(cfg.resources);
    command.params    = JSON.stringify(cfg.params);
    command.crop      = JSON.stringify(cfg.crop);
    command.paste     = JSON.stringify(cfg.paste);
    await startRun(command);
  } else if (key === "cancel" || cancelAll) {
    await cancelRun(command, cancelAll ? "all" : "current");
  } else {
    await flushPendingApply();
    const requestId = "action:" + key;
    // verbose=true so Load / Save-config feedback shows in the log
    await sendCommand(requestId, command, [], undefined, true);
  }
}

function updateRunButtons() {
  const anyRun = app.activeRuns.size > 0;
  const anyBatch = anyRun || (app.activeBatchesCount > 0);
  for (const s of app.sectionSpecs) {
    if (s.kind !== "actions" && s.kind !== "workflow-bar") continue;
    for (const item of (s.items || [])) {
      const btn = document.querySelector(
        `[data-action-key="${cssEsc(item.key)}"]`);
      if (!btn) continue;
      if (item.key === "run") {
        btn.disabled = !(app.latestState
                         && app.latestState.daemon
                         && app.latestState.daemon.workflow);
      } else if (item.key === "cancel") {
        btn.disabled = !anyRun;
      } else if (isCancelAllItem(item)) {
        btn.disabled = !anyBatch;
      } else if (item.key === "load") {
        btn.disabled = anyRun;
      } else if (item.key === "save_config") {
        btn.disabled = anyRun
          || !(app.latestState
               && app.latestState.daemon
               && app.latestState.daemon.workflow);
      }
    }
  }
}

async function startRun(command) {
  let resp;
  try {
    resp = await fetch("/api/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command: command}),
    });
  } catch (e) {
    logLine("err", "Run request failed: "
            + (e && e.message ? e.message : String(e)));
    return;
  }
  if (!resp.ok) {
    let msg = "HTTP " + resp.status;
    try { const b = await resp.json(); if (b && b.error) msg = b.error; } catch (_) {}
    logLine("err", "Cannot run: " + msg);
    return;
  }
  const body = await resp.json();
  const runId = body.run_id;
  app.activeRuns.set(runId, { startedAt: Date.now() });
  logLine("hdr", "Run " + runId + " started.");
  updateRunButtons();
}

async function cancelRun(command, scope) {
  let resp;
  try {
    resp = await fetch("/api/cancel", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command: command, scope: scope || "current"}),
    });
  } catch (e) {
    logLine("err", "Cancel request failed: "
            + (e && e.message ? e.message : String(e)));
    return;
  }
  if (!resp.ok) {
    let msg = "HTTP " + resp.status;
    try { const b = await resp.json(); if (b && b.error) msg = b.error; } catch (_) {}
    logLine("err", "Cannot cancel: " + msg);
    if (resp.status === 409) {
      updateRunButtons();
    }
  } else {
    logLine("warn",
      scope === "all" ? "Cancel All sent." : "Cancel sent for all active runs.");
  }
}

function handleRunEvent(e) {
  const s = JSON.parse(e.data);
  const runId = s.run_id || "?";
  const short = String(runId).slice(-8);
  const prefix = "[" + short + "] ";
  if (s.status === "progress") {
    const cls = s.run_status === "success" ? "ok"
               : s.run_status === "error" ? "err" : "";
    logLine(cls,
      prefix + "[" + (s.index + 1) + "/" + s.total + "] "
      + s.command + ": " + s.message);
  } else if (s.status === "cancelling") {
    logLine("warn", prefix + "Cancelling\u2026");
  } else if (s.status === "cancelled") {
    const n = s.cancelled != null ? s.cancelled : "";
    logLine("warn",
      prefix + "Cancelled. " + n + " run(s) not completed.");
    app.activeRuns.delete(runId);
    updateRunButtons();
  } else if (s.status === "intercepted") {
    const msg = s.message || "Run intercepted";
    logLine("hdr",
      prefix + msg + " \u2014 review in interceptor playground");
    app.activeRuns.delete(runId);
    updateRunButtons();
  } else if (s.status === "done") {
    let text = "Done. " + s.successful + " ok, " + s.failed + " failed.";
    if (s.cancelled) text += " (" + s.cancelled + " cancelled)";
    logLine("hdr", prefix + text);
    app.activeRuns.delete(runId);
    updateRunButtons();
  } else if (s.status === "error") {
    logLine("err",
      prefix + "Run failed: " + (s.error || "unknown"));
    app.activeRuns.delete(runId);
    updateRunButtons();
  }
}

// ---------------------------------------------------------------------
// Playground input section
// ---------------------------------------------------------------------

const LAYER_SOURCES = ["active", "global", "layer"];

function _rowKey(pid, sid) {
  return pid + "::" + sid;
}
function _ctrlKey(pid, name) {
  return pid + "::ctrl::" + name;
}

function renderPlaygrounds(playgrounds) {
  if (!Array.isArray(playgrounds)) playgrounds = [];
  const container = document.getElementById("playgrounds");
  if (!container) return;

  const desiredSlots = new Map();
  const desiredCtrls = new Map();
  const pidsWithNameShown = new Set();

  for (const p of playgrounds) {
    if (!p || !p.id) continue;

    const slots = p.layer_slots || [];
    let firstVisibleSlotDone = false;
    for (const slot of slots) {
      if (!slot || !slot.id) continue;
      if (slot.managed_by_playground === true) continue;
      const showName = !firstVisibleSlotDone;
      firstVisibleSlotDone = true;
      if (showName) pidsWithNameShown.add(p.id);
      desiredSlots.set(_rowKey(p.id, slot.id), {
        playground: p,
        slot: slot,
        show_name: showName,
      });
    }

    const ctrls = p.control_params || [];
    let firstCtrlDone = pidsWithNameShown.has(p.id);
    for (const cp of ctrls) {
      if (!cp || !cp.name) continue;
      const showName = !firstCtrlDone;
      firstCtrlDone = true;
      desiredCtrls.set(_ctrlKey(p.id, cp.name), {
        playground: p,
        ctrl: cp,
        show_name: showName,
      });
    }
  }

  const totalDesired = desiredSlots.size + desiredCtrls.size;
  if (totalDesired === 0) {
    if (container.querySelector(".playground-row")) {
      container.innerHTML =
        '<div class="empty">No playgrounds running.</div>';
    }
    return;
  }

  const placeholder = container.querySelector(".empty");
  if (placeholder) placeholder.remove();

  const existingSlots = new Map();
  const existingCtrls = new Map();
  for (const row of container.querySelectorAll(".playground-row")) {
    const pid = row.getAttribute("data-playground-id");
    if (row.hasAttribute("data-ctrl-name")) {
      const cname = row.getAttribute("data-ctrl-name");
      if (pid && cname) existingCtrls.set(_ctrlKey(pid, cname), row);
    } else {
      const sid = row.getAttribute("data-slot-id");
      if (pid && sid) existingSlots.set(_rowKey(pid, sid), row);
    }
  }

  for (const [key, row] of existingSlots) {
    if (!desiredSlots.has(key)) {
      row.remove();
      existingSlots.delete(key);
    }
  }
  for (const [key, row] of existingCtrls) {
    if (!desiredCtrls.has(key)) {
      row.remove();
      existingCtrls.delete(key);
    }
  }

  for (const [key, meta] of desiredSlots) {
    let row = existingSlots.get(key);
    if (!row) {
      row = createPlaygroundRow(meta.playground, meta.slot, meta.show_name);
      container.appendChild(row);
      existingSlots.set(key, row);
    }
    syncPlaygroundRow(row, meta.playground, meta.slot);
  }

  for (const [key, meta] of desiredCtrls) {
    let row = existingCtrls.get(key);
    if (!row) {
      row = createControlParamRow(
        meta.playground, meta.ctrl, meta.show_name);
      container.appendChild(row);
      existingCtrls.set(key, row);
    }
    syncControlParamRow(row, meta.playground, meta.ctrl, meta.show_name);
  }
}

function createPlaygroundRow(p, slot, show_name) {
  const pid = p.id;
  const sid = slot.id;
  const pname = p.name || pid || "?";

  const row = document.createElement("div");
  row.className = "playground-row";
  row.setAttribute("data-playground-id", pid);
  row.setAttribute("data-slot-id", sid);

  const nameSpan = document.createElement("span");
  nameSpan.className = "name";
  nameSpan.textContent = show_name ? pname : "";
  row.appendChild(nameSpan);

  const slotSpan = document.createElement("span");
  slotSpan.className = "slot-id";
  slotSpan.textContent = sid;
  slotSpan.title = "Slot " + sid + " of " + pname;
  row.appendChild(slotSpan);

  const select = document.createElement("select");
  select.setAttribute("data-playground-source", _rowKey(pid, sid));
  for (const opt of LAYER_SOURCES) {
    const o = document.createElement("option");
    o.value = opt;
    o.textContent = opt;
    select.appendChild(o);
  }
  row.appendChild(select);

  const input = document.createElement("input");
  input.type = "text";
  input.setAttribute("data-playground-layer", _rowKey(pid, sid));
  input.placeholder = "layer name";
  row.appendChild(input);

  const autoSelectHint = (slot.auto_select_layer || "").trim();

  select.addEventListener("change", () => {
    const source = select.value;
    input.hidden = (source !== "layer");

    if (source === "layer"
          && !input.value.trim()
          && autoSelectHint) {
      input.value = autoSelectHint;
    }

    row.dataset.editing = "1";
    const layerName = (source === "layer") ? input.value.trim() : "";
    sendPlaygroundSetLayer(pid, sid, source, layerName);
  });
  input.addEventListener("change", () => {
    if (select.value !== "layer") return;
    row.dataset.editing = "1";
    sendPlaygroundSetLayer(pid, sid, "layer", input.value.trim());
  });

  return row;
}

function syncPlaygroundRow(row, p, slot) {
  const pid = p.id;
  const sid = slot.id;

  const selector = slot.selector || {};
  const source = selector.source || "active";
  const layerName = selector.layer_name || "";

  const nameSpan = row.querySelector(".name");
  if (nameSpan && nameSpan.textContent) {
    const newName = p.name || pid || "";
    if (nameSpan.textContent !== newName) {
      nameSpan.textContent = newName;
    }
  }

  const slotSpan = row.querySelector(".slot-id");
  if (slotSpan && slotSpan.textContent !== sid) {
    slotSpan.textContent = sid;
  }

  const select = row.querySelector("[data-playground-source]");
  const input = row.querySelector("[data-playground-layer]");
  if (!select || !input) return;

  if (row.dataset.editing === "1") {
    if (select.value === source) {
      delete row.dataset.editing;
    } else {
      return;
    }
  }

  if (select.value !== source && select !== document.activeElement) {
    select.value = source;
  }
  if (input !== document.activeElement && input.value !== layerName) {
    input.value = layerName;
  }
  input.hidden = (source !== "layer");
}

async function sendPlaygroundSetLayer(pid, sid, source, layerName) {
  const command = {
    type: "playground_set_layer",
    id: pid,
    slot_id: sid,
    source: source,
    layer_name: (source === "layer") ? layerName : "",
  };
  try {
    const resp = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command: command}),
    });
    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try { const b = await resp.json(); if (b && b.error) msg = b.error; } catch (_) {}
      logLine("err", "playground_set_layer failed: " + msg);
      const row = document.querySelector(
        `[data-playground-id="${cssEsc(pid)}"][data-slot-id="${cssEsc(sid)}"]`);
      if (row) delete row.dataset.editing;
    }
  } catch (e) {
    logLine("err", "playground_set_layer network error: "
            + (e && e.message ? e.message : String(e)));
    const row = document.querySelector(
      `[data-playground-id="${cssEsc(pid)}"][data-slot-id="${cssEsc(sid)}"]`);
    if (row) delete row.dataset.editing;
  }
}

function _ctrlValueToStr(ctype, value) {
  if (ctype === "toggle") {
    return value ? "true" : "false";
  }
  if (value === null || value === undefined) return "";
  return String(value);
}

function createControlParamRow(p, cp, show_name) {
  const pid = p.id;
  const name = cp.name;
  const pname = p.name || pid || "?";
  const ctype = cp.type || "text";

  const row = document.createElement("div");
  row.className = "playground-row";
  row.setAttribute("data-playground-id", pid);
  row.setAttribute("data-ctrl-name", name);

  const nameSpan = document.createElement("span");
  nameSpan.className = "name";
  nameSpan.textContent = show_name ? pname : "";
  row.appendChild(nameSpan);

  const paramSpan = document.createElement("span");
  paramSpan.className = "param-name";
  paramSpan.textContent = name;
  paramSpan.title =
    "Control parameter '" + name + "' of the '" + pname + "' playground. "
    + "Daemon-owned routing hint; merged into config['params'] at "
    + "generation time.";
  row.appendChild(paramSpan);

  let widget;
  if (ctype === "select") {
    widget = document.createElement("select");
    for (const opt of (cp.options || [])) {
      const o = document.createElement("option");
      o.value = String(opt);
      o.textContent = String(opt);
      widget.appendChild(o);
    }
    widget.value = _ctrlValueToStr("text", cp.value);
  } else if (ctype === "toggle") {
    widget = document.createElement("input");
    widget.type = "checkbox";
    widget.checked = !!cp.value;
  } else if (ctype === "number") {
    widget = document.createElement("input");
    widget.type = "number";
    widget.step = "any";
    widget.value = _ctrlValueToStr("number", cp.value);
  } else if (ctype === "integer") {
    widget = document.createElement("input");
    widget.type = "number";
    widget.step = "1";
    widget.value = _ctrlValueToStr("integer", cp.value);
  } else {
    widget = document.createElement("input");
    widget.type = "text";
    widget.value = _ctrlValueToStr("text", cp.value);
  }
  widget.setAttribute("data-playground-ctrl", _ctrlKey(pid, name));
  row.appendChild(widget);

  const stretch = document.createElement("span");
  stretch.className = "stretch";
  row.appendChild(stretch);

  const commit = () => {
    row.dataset.editing = "1";
    let value;
    if (ctype === "toggle") {
      value = widget.checked ? "true" : "false";
    } else {
      value = widget.value;
    }
    sendPlaygroundSetControlParam(pid, name, value);
  };

  if (ctype === "select" || ctype === "toggle") {
    widget.addEventListener("change", commit);
  } else {
    widget.addEventListener("change", commit);
    widget.addEventListener("blur", commit);
  }

  return row;
}

function syncControlParamRow(row, p, cp, show_name) {
  const pid = p.id;
  const name = cp.name;
  const ctype = cp.type || "text";
  const wantValue = cp.value;
  const wantName = show_name ? (p.name || pid || "") : "";

  const nameSpan = row.querySelector(".name");
  if (nameSpan && nameSpan.textContent !== wantName) {
    nameSpan.textContent = wantName;
  }

  const paramSpan = row.querySelector(".param-name");
  if (paramSpan && paramSpan.textContent !== name) {
    paramSpan.textContent = name;
  }

  const widget = row.querySelector("[data-playground-ctrl]");
  if (!widget) return;

  if (row.dataset.editing === "1") {
    if (ctype === "toggle") {
      const have = widget.checked ? "true" : "false";
      const want = _ctrlValueToStr("toggle", wantValue);
      if (have === want) {
        delete row.dataset.editing;
      } else {
        return;
      }
    } else {
      const want = _ctrlValueToStr(ctype, wantValue);
      if (widget.value === want) {
        delete row.dataset.editing;
      } else {
        return;
      }
    }
  }

  if (widget === document.activeElement) return;

  if (ctype === "toggle") {
    const want = !!wantValue;
    if (widget.checked !== want) widget.checked = want;
  } else {
    const want = _ctrlValueToStr(ctype, wantValue);
    if (widget.value !== want) widget.value = want;
  }
}

async function sendPlaygroundSetControlParam(pid, name, value) {
  const command = {
    type: "playground_set_control_param",
    id: pid,
    name: name,
    value: value,
  };
  try {
    const resp = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({command: command}),
    });
    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try { const b = await resp.json(); if (b && b.error) msg = b.error; } catch (_) {}
      logLine("err", "playground_set_control_param failed: " + msg);
      const row = document.querySelector(
        `[data-playground-id="${cssEsc(pid)}"][data-ctrl-name="${cssEsc(name)}"]`);
      if (row) delete row.dataset.editing;
    }
  } catch (e) {
    logLine("err", "playground_set_control_param network error: "
            + (e && e.message ? e.message : String(e)));
    const row = document.querySelector(
      `[data-playground-id="${cssEsc(pid)}"][data-ctrl-name="${cssEsc(name)}"]`);
    if (row) delete row.dataset.editing;
  }
}

function connectEvents() {
  const es = new EventSource("/api/events");

  es.addEventListener("open", function () {
    app.eventsConnected = true;
    logLine("hdr", "SSE connected.");
  });

  es.addEventListener("state", function (e) {
    try {
      const s = JSON.parse(e.data);
      app.latestState = s;
      const wf = (s && s.daemon && s.daemon.workflow) || "";
      const wfChanged = wf !== app.currentWorkflow;
      app.currentWorkflow = wf;
      if (wfChanged && app.latestPanel) {
        rebuildPanel(app.latestPanel);
      }
      const batches = (s && s.daemon
                       && Array.isArray(s.daemon.active_batches))
        ? s.daemon.active_batches.length : 0;
      app.activeBatchesCount = batches;
      updateRunButtons();
      renderStatus(s);
      if (s && s.daemon && Array.isArray(s.daemon.playgrounds)) {
        renderPlaygrounds(s.daemon.playgrounds);
      } else {
        renderPlaygrounds([]);
      }
    } catch (err) {
      logFatal("state parse: " + (err && err.message ? err.message : err));
    }
  });

  es.addEventListener("panel", function (e) {
    try {
      const desc = JSON.parse(e.data);
      if (!desc || typeof desc !== "object") return;
      app.latestPanel = desc;
      app.firstPanelReceived = true;
      const structureChanged = desc.structure_id !== app.panelStructureId;
      if (structureChanged) {
        app.panelStructureId = desc.structure_id;
        rebuildPanel(desc);
        if (app.latestState && app.latestState.daemon
              && app.latestState.daemon.workflow) {
          scheduleApply();
        }
      } else {
        syncValues(desc);
      }
    } catch (err) {
      logFatal("panel parse: " + (err && err.message ? err.message : err));
    }
  });

  es.addEventListener("run", handleRunEvent);

  es.onerror = function () {
    logLine("warn", "SSE error; browser will retry.");
  };

  setTimeout(function () {
    if (app.firstPanelReceived) return;
    const form = document.getElementById("config-form");
    if (!form) return;
    form.innerHTML = app.eventsConnected
      ? '<div class="empty">Connected to the daemon; waiting for a '
        + 'panel description. If this persists, the daemon may not '
        + 'be publishing panel events.</div>'
      : '<div class="empty">Could not reach /api/events. '
        + 'Check that the web server is running.</div>';
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


def main() -> int:
    threading.Thread(target=_daemon_event_client,
                     name="web-panel-daemon-client",
                     daemon=True).start()
    # Fallback: if no panel arrives within INITIAL_PANEL_FALLBACK_S,
    # poll panel_description once so the browser isn't left staring
    # at an empty form.
    threading.Thread(target=_fetch_initial_panel,
                     name="web-panel-initial-panel",
                     daemon=True).start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True

    _log(f"listening on http://{HOST}:{PORT}/")
    _log(f"daemon command socket: {DAEMON_HOST}:{DAEMON_PORT}")
    _log(f"daemon event socket:   {DAEMON_HOST}:{DAEMON_EVENT_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())