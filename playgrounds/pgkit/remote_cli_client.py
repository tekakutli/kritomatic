"""
pgkit.remote_cli_client — Python client for the pgkit remote protocol.

The Python-side counterpart to `pgkit/remote_client.py` (the browser
client). Same wire protocol, same `hello` handshake, same message
types. Any Python process can import this module and drive a running
playground exactly the way the browser does.

Public API
==========
    RemoteCliClient(url)

Lifecycle
    .connect(timeout=15.0, require_browser=False)
    .close()
    .__enter__ / .__exit__

Snapshots (all returned dicts are defensive copies)
    .values()         field name -> current value
    .schema()         field name -> metadata dict
    .entities()       level name -> {label, selected, parent, items}
    .entity_order()   ordered list of level names
    .presence()       {"browser": N, "remote": M}
    .client_id()      the hub-assigned client id
    .is_connected()

Sends
    .request_state()
    .request_state_and_wait(timeout=5.0)
    .patch(values)
    .patch_and_verify(values, timeout=5.0)
    .command(kind, payload=None, timeout=30.0)
    .raw_send(msg)

Waits
    .wait_value(name, predicate, timeout=10.0)
    .wait_change(fields=None, timeout=10.0)
    .wait_until_settled(name, timeout=60.0)

Convenience
    .bundle(set=None, actions=None, select=None,
            wait_for_running=True, timeout=60.0, on_progress=None)

Events
    .on(msg_type, callback)
    .off(msg_type, callback)

Design notes
============
Reader thread + condition variable. The reader thread updates the
caches under `self._cv` and notifies; every wait method parks on the
condition variable and re-checks its predicate on each notification.
Sends are gated by a separate `_send_lock` so a slow send never blocks
a state update.

Echo suppression: the hub forwards a `patch` to every client except
its sender, so this client never receives its own patches. That is
how the browser-side client behaves too.

Browser appearance: the browser's schema is only sent in response to
`state_request`. When a browser connects to the hub *after* this
client, the browser does not push its own schema — it only answers
`state_request`s. The client therefore watches `presence` and issues
a fresh `state_request` on the 0→1 browser transition, so a CLI that
connected to a browser-less hub learns the schema the moment a
browser appears.

Async actions: playground actions return before any HTTP round-trip
finishes. `bundle(wait_for_running=True)` inserts a
`wait_until_settled("status.running")` between sequential actions.
`wait_until_settled` first does a `state_request` to see whether the
action is actually running (sync actions never set the flag), then
waits for the flag to go False if it is.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable, Optional

try:
    from websockets.sync.client import connect as _ws_connect
    from websockets.exceptions import ConnectionClosed as _WSConnectionClosed
    _WS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ws_connect = None
    _WSConnectionClosed = Exception
    _WS_AVAILABLE = False

from . import PROTOCOL_VERSION


# ============================================================================
#  Errors
# ============================================================================

class RemoteCliError(Exception):
    """Base for every client-side error."""


class RemoteTimeout(RemoteCliError):
    """A wait or command timed out."""


class RemoteNotConnected(RemoteCliError):
    """Operation attempted before connect() or after close()."""


class RemoteCommandFailed(RemoteCliError):
    """The playground refused or errored on a command."""

    def __init__(self, kind: str, message: str, result: Optional[dict]):
        self.kind = kind
        self.result = result
        super().__init__(f"{kind}: {message}")


# ============================================================================
#  Helpers
# ============================================================================

_log_send: Optional[Callable[[dict], None]] = None
_log_recv: Optional[Callable[[dict], None]] = None


def set_verbose(enabled: bool = True) -> None:
    """Turn message tracing on/off. Tracing goes to stderr."""
    import sys
    global _log_send, _log_recv
    if enabled:
        _log_send = lambda m: print(f"→ {json.dumps(m)}", file=sys.stderr)
        _log_recv = lambda m: print(f"← {json.dumps(m)}", file=sys.stderr)
    else:
        _log_send = None
        _log_recv = None


def parse_value(raw):
    """Turn a shell string into a typed Python value.

    Tries json.loads first: handles numbers, booleans, quoted strings,
    arrays, objects. Falls back to the raw string for anything JSON
    cannot parse (which is most bare words).
    """
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _predicate_matches(op: str, expected, actual) -> bool:
    if op == "equals":
        return actual == expected
    if op == "not_equals":
        return actual != expected
    if op == "greater_than":
        try: return float(actual) > float(expected)
        except (TypeError, ValueError): return False
    if op == "less_than":
        try: return float(actual) < float(expected)
        except (TypeError, ValueError): return False
    if op == "contains":
        try: return expected in actual
        except TypeError: return False
    if op == "matches":
        import re
        try: return bool(re.search(str(expected), str(actual)))
        except re.error: return False
    raise ValueError(f"unknown predicate: {op}")


# ============================================================================
#  Client
# ============================================================================

class RemoteCliClient:
    """Python client for a pgkit playground's /remote endpoint."""

    def __init__(self, url: str, *, protocol: int = PROTOCOL_VERSION):
        if not _WS_AVAILABLE:
            raise RuntimeError(
                "websockets>=12 is required for RemoteCliClient. "
                "Install with: pip install 'websockets>=12'"
            )
        self._url = url
        self._protocol = protocol

        self._ws = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()

        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

        self._client_id: Optional[str] = None
        self._values: dict = {}
        self._schema: dict = {}
        self._entities: Optional[dict] = None
        self._entity_order: list = []
        self._presence = {"browser": 0, "remote": 0}
        self._connected = False
        self._state_recv_count = 0

        self._seq = 0
        self._pending: dict = {}
        self._listeners: dict = {}

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

    def connect(self, *, timeout: float = 15.0,
                require_browser: bool = False) -> None:
        if self._ws is not None:
            raise RemoteCliError("already connected")

        try:
            self._ws = _ws_connect(
                self._url,
                max_size=2 ** 30,
                open_timeout=10.0,
                close_timeout=5.0,
            )
        except Exception as e:
            raise RemoteCliError(
                f"could not connect to {self._url}: {e}"
            ) from e

        with self._cv:
            self._connected = True
            self._cv.notify_all()

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="remote-cli-reader",
            daemon=True,
        )
        self._reader_thread.start()

        self._send({
            "type": "hello",
            "role": "remote",
            "protocol": self._protocol,
        })

        deadline = time.monotonic() + timeout

        with self._cv:
            while self._client_id is None:
                if not self._connected:
                    raise RemoteCliError(
                        "connection closed before hello_ack"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteTimeout("no hello_ack from playground")
                self._cv.wait(timeout=min(remaining, 0.5))

            # Give the hub a moment to deliver presence.
            presence_deadline = min(deadline, time.monotonic() + 3.0)
            while (self._presence["browser"] == 0
                   and self._presence["remote"] <= 1):
                remaining = presence_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=min(remaining, 0.25))

            have_browser = self._presence["browser"] > 0

            if require_browser and not have_browser:
                raise RemoteCliError(
                    f"no browser connected at {self._url}. "
                    f"Open the playground in a browser tab first."
                )

            if have_browser:
                while True:
                    if (self._state_recv_count > 0
                            and self._entities is not None):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RemoteTimeout(
                            "browser connected but "
                            "state/schema/entities did not arrive"
                        )
                    self._cv.wait(timeout=min(remaining, 0.5))

    def close(self) -> None:
        self._stop.set()
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=3.0)
            self._reader_thread = None
        with self._cv:
            self._connected = False
            self._cv.notify_all()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    #  Reader
    # ------------------------------------------------------------------

    def _reader_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            while not self._stop.is_set():
                try:
                    raw = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except _WSConnectionClosed:
                    break
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._handle(msg)
        except Exception:
            pass
        finally:
            with self._cv:
                self._connected = False
                self._cv.notify_all()

    def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if _log_recv is not None:
            try:
                _log_recv(msg)
            except Exception:
                pass

        need_state_refresh = False
        callbacks = []

        with self._cv:
            if t == "hello_ack":
                self._client_id = msg.get("clientId")
            elif t == "state":
                self._values = dict(msg.get("values") or {})
                self._state_recv_count += 1
            elif t == "schema":
                self._schema = dict(msg.get("fields") or {})
            elif t == "entities":
                self._entities = dict(msg.get("levels") or {})
                self._entity_order = list(msg.get("order") or [])
            elif t == "presence":
                new_browser = int(msg.get("browser") or 0)
                old_browser = self._presence["browser"]
                self._presence = {
                    "browser": new_browser,
                    "remote": int(msg.get("remote") or 0),
                }
                if new_browser > old_browser and not self._schema:
                    need_state_refresh = True
            elif t == "patch":
                values = msg.get("values") or {}
                if isinstance(values, dict):
                    self._values.update(values)
            elif t == "command_result":
                cmd_id = msg.get("id")
                pending = self._pending.get(cmd_id)
                if pending is not None:
                    pending["result"] = msg
                    pending["event"].set()

            self._cv.notify_all()
            callbacks = list(self._listeners.get(t, []))

        if need_state_refresh:
            try:
                self._send({"type": "state_request"})
            except Exception:
                pass

        for cb in callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    # ------------------------------------------------------------------
    #  Send
    # ------------------------------------------------------------------

    def _send(self, msg: dict) -> None:
        if _log_send is not None:
            try:
                _log_send(msg)
            except Exception:
                pass
        with self._send_lock:
            if self._ws is None:
                raise RemoteNotConnected("not connected")
            self._ws.send(json.dumps(msg))

    def raw_send(self, msg: dict) -> None:
        self._send(msg)

    # ------------------------------------------------------------------
    #  Snapshot accessors
    # ------------------------------------------------------------------

    def values(self) -> dict:
        with self._lock:
            return dict(self._values)

    def schema(self) -> dict:
        with self._lock:
            return dict(self._schema)

    def entities(self) -> Optional[dict]:
        with self._lock:
            return dict(self._entities) if self._entities is not None else None

    def entity_order(self) -> list:
        with self._lock:
            return list(self._entity_order)

    def presence(self) -> dict:
        with self._lock:
            return dict(self._presence)

    def client_id(self) -> Optional[str]:
        with self._lock:
            return self._client_id

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    # ------------------------------------------------------------------
    #  Requests
    # ------------------------------------------------------------------

    def request_state(self) -> None:
        self._send({"type": "state_request"})

    def request_state_and_wait(self, timeout: float = 5.0) -> None:
        with self._lock:
            baseline = self._state_recv_count
        self._send({"type": "state_request"})
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._state_recv_count <= baseline:
                if not self._connected:
                    raise RemoteNotConnected("connection closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteTimeout("state_request timed out")
                self._cv.wait(timeout=min(remaining, 0.5))

    # ------------------------------------------------------------------
    #  Patch
    # ------------------------------------------------------------------

    def patch(self, values: dict) -> None:
        if not isinstance(values, dict) or not values:
            return
        with self._lock:
            self._seq += 1
            seq = self._seq
            origin = self._client_id
        self._send({
            "type": "patch",
            "origin": origin,
            "seq": seq,
            "values": values,
        })

    def patch_and_verify(self, values: dict,
                         timeout: float = 5.0) -> dict:
        self.patch(values)
        try:
            self.request_state_and_wait(timeout=timeout)
        except RemoteTimeout:
            pass
        with self._lock:
            return {k: self._values.get(k) for k in values}

    # ------------------------------------------------------------------
    #  Command
    # ------------------------------------------------------------------

    def command(self, kind: str, payload: Optional[dict] = None,
                timeout: float = 30.0) -> dict:
        cmd_id = "cli-" + uuid.uuid4().hex[:12]
        pending = {"event": threading.Event(), "result": None}
        with self._lock:
            self._pending[cmd_id] = pending
        try:
            self._send({
                "type": "command",
                "id": cmd_id,
                "kind": kind,
                "payload": payload or {},
            })
            if not pending["event"].wait(timeout=timeout):
                raise RemoteTimeout(
                    f"command {kind!r} did not complete within {timeout}s"
                )
        finally:
            with self._lock:
                self._pending.pop(cmd_id, None)

        result = pending["result"]
        if result is None:
            raise RemoteCommandFailed(kind, "no result", None)
        if not result.get("ok"):
            raise RemoteCommandFailed(
                kind,
                result.get("error") or "unknown error",
                result,
            )
        return result

    # ------------------------------------------------------------------
    #  Waits
    # ------------------------------------------------------------------

    def wait_value(self, name: str,
                   predicate: Callable[[Any], bool],
                   timeout: float = 10.0) -> Any:
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                current = self._values.get(name)
                try:
                    if predicate(current):
                        return current
                except Exception:
                    pass
                if not self._connected:
                    raise RemoteNotConnected("connection closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteTimeout(
                        f"wait_value({name!r}) timed out after {timeout}s"
                    )
                self._cv.wait(timeout=min(remaining, 0.5))

    def wait_change(self, *, fields=None,
                    timeout: float = 10.0) -> list:
        with self._lock:
            baseline_values = dict(self._values)
            baseline_entities = (
                json.dumps(self._entities, sort_keys=True, default=str)
                if self._entities is not None else None
            )
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                changed: list = []
                for k, v in self._values.items():
                    if k not in baseline_values or baseline_values[k] != v:
                        changed.append(k)
                for k in baseline_values:
                    if k not in self._values:
                        changed.append(k)
                if (self._entities is not None
                        and baseline_entities is not None):
                    curr = json.dumps(self._entities,
                                      sort_keys=True, default=str)
                    if curr != baseline_entities:
                        changed.append("__entities__")
                if fields is not None:
                    changed = [c for c in changed
                               if c in fields or c == "__entities__"]
                if changed:
                    return changed
                if not self._connected:
                    raise RemoteNotConnected("connection closed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteTimeout(
                        f"wait_change timed out after {timeout}s"
                    )
                self._cv.wait(timeout=min(remaining, 0.5))

    def wait_until_settled(self, name: str,
                           timeout: float = 60.0) -> None:
        """Wait for a bool field to finish running.

        If the field is already False when the wait starts (sync
        action, or an async action that finished before the wait
        began), returns immediately. Otherwise waits for the field to
        become False.

        The first check goes through a fresh `state_request` so the
        decision is made on current state, not on a possibly stale
        cache. That first check costs one round trip (~10ms on
        localhost) and prevents the "wait starts before the browser
        has flipped the flag" race entirely.
        """
        try:
            self.request_state_and_wait(timeout=min(2.0, timeout))
        except RemoteTimeout:
            pass
        with self._lock:
            is_running = bool(self._values.get(name))
        if not is_running:
            return
        self.wait_value(name, lambda v: not v, timeout=timeout)

    # ------------------------------------------------------------------
    #  Events
    # ------------------------------------------------------------------

    def on(self, msg_type: str,
           callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.setdefault(msg_type, []).append(callback)

    def off(self, msg_type: str,
            callback: Callable[[dict], None]) -> None:
        with self._lock:
            lst = self._listeners.get(msg_type, [])
            if callback in lst:
                lst.remove(callback)

    # ------------------------------------------------------------------
    #  Bundle
    # ------------------------------------------------------------------

    def bundle(self, *, set: Optional[dict] = None,
               actions: Optional[list] = None,
               select: Optional[tuple] = None,
               wait_for_running: bool = True,
               timeout: float = 60.0,
               on_progress: Optional[Callable[[str, dict], None]] = None,
               ) -> dict:
        """Apply a coordinated set of writes and actions.

        `set`       : one patch with N field updates (1 round trip)
        `actions`   : sequential commands, one round trip + one wait each
        `select`    : one final `select` command
        Then a single refresh and return of the fresh values/entities.

        `wait_for_running` inserts a `wait_until_settled("status.running")`
        after each action *if* the playground publishes that field.
        Playgrounds without `status.running` are unaffected.
        """
        progress = on_progress or (lambda kind, data: None)

        if set:
            self.patch(set)
            progress("patch", {"values": dict(set)})

        if actions:
            has_running = "status.running" in self.schema()
            for spec in actions:
                kind = spec.get("kind") or spec.get("action")
                if not kind:
                    raise ValueError(f"action missing 'kind': {spec!r}")
                payload = spec.get("payload") or {}
                result = self.command(kind, payload, timeout=timeout)
                progress("action", {"kind": kind, "result": result})
                if wait_for_running and has_running:
                    try:
                        self.wait_until_settled(
                            "status.running", timeout=timeout
                        )
                    except RemoteTimeout:
                        progress("action", {
                            "kind": kind,
                            "warning": "status.running wait timed out",
                        })

        if select:
            level, name = select
            result = self.command(
                "select", {"level": level, "name": name},
                timeout=timeout,
            )
            progress("select",
                     {"level": level, "name": name, "result": result})

        try:
            self.request_state_and_wait(timeout=5.0)
        except RemoteTimeout:
            pass

        with self._lock:
            return {
                "values": dict(self._values),
                "entities": (dict(self._entities)
                             if self._entities is not None else None),
            }
