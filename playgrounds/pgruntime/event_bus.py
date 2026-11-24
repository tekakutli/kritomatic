"""Daemon event-bus subscriber.

The daemon publishes events on TCP port 12347, separate from the
request/response socket on port 12346.  Each playground that consumes
events opens a long-lived connection, reads newline-delimited JSON,
and dispatches by event type.

Two live consumers as of the outpaint port:

  · multimask — ``paste_applied`` and ``canvas_update`` events are
    forwarded to browser clients over WebSocket.

  · outpaint  — ``outpaint_result`` events are stored in memory
    (``_last_pasted``) for the browser's autosync poll to pick up.

Both shapes work with the same dispatcher: the caller supplies a
dict mapping event type → handler, and each handler receives the
event's ``data`` field.

The subscriber is idempotent per (host, port, handler-keys) tuple —
calling ``subscribe`` twice with the same handlers returns the same
thread rather than spawning a second connection.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time


DAEMON_EVENT_HOST = "127.0.0.1"
DAEMON_EVENT_PORT = 12347


_threads: dict = {}
_lock = threading.Lock()


def subscribe(
    handlers: dict,
    *,
    host: str = DAEMON_EVENT_HOST,
    port: int = DAEMON_EVENT_PORT,
    reconnect_s: float = 2.0,
    log_prefix: str = "playground",
) -> threading.Thread:
    """Spawn (or reuse) a daemon thread that dispatches events.

    ``handlers`` maps event type (str) → callable receiving the
    event's ``data`` dict.  Events whose type is not in ``handlers``
    are silently ignored.

    Returns the thread.  Safe to call multiple times with the same
    arguments; the second call returns the existing thread.
    """
    if not isinstance(handlers, dict) or not handlers:
        raise ValueError("handlers must be a non-empty dict")
    key = (host, port, tuple(sorted(handlers.keys())))
    with _lock:
        existing = _threads.get(key)
        if existing is not None and existing.is_alive():
            return existing
        thread = threading.Thread(
            target=_loop,
            args=(dict(handlers), host, port, reconnect_s, log_prefix),
            name=f"{log_prefix}-event-bus",
            daemon=True,
        )
        _threads[key] = thread
        thread.start()
        return thread


def _loop(handlers, host, port, reconnect_s, log_prefix):
    while True:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((host, port))
            sock.settimeout(None)
            print(f"[{log_prefix}] event bus connected on {host}:{port}",
                  flush=True)
            buf = b""
            while True:
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue
                    etype = event.get("type")
                    handler = handlers.get(etype)
                    if handler is None:
                        continue
                    try:
                        handler(event.get("data") or {})
                    except Exception as e:
                        print(
                            f"[{log_prefix}] event handler {etype!r} "
                            f"raised: {e}",
                            file=sys.stderr, flush=True,
                        )
        except (OSError, socket.timeout) as e:
            print(f"[{log_prefix}] event bus: {e}", flush=True)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        time.sleep(reconnect_s)
