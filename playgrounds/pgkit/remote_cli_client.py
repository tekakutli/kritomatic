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
    .wait_until_settled(name, timeout=60.0)     legacy; see below

Convenience
    .bundle(set=None, actions=None, select=None,
            wait_for_running=False, timeout=60.0, on_progress=None)

Events
    .on(msg_type, callback)
    .off(msg_type, callback)

Async actions
=============
`command()` blocks until the action's observable work settles, because
`pgkit/remote_client.js`'s `pgkitRemoteHandleCommand` `await`s the
action's `run` function. A playground whose `run` is `async function`,
or whose `run` is a sync function that `return`s its async work's
Promise, gets this for free: `command()` returns only after the work
is done, and a subsequent `state_request` sees the side effects. The
same guarantee holds for `select` (via `pgkitHandleSelect`) and
`deselect` (via the injected deselect handler).

Playgrounds whose action `run` is a sync function that calls async
work *without returning the Promise* do not get this guarantee — the
`command_result` fires at the moment `run` returns, before the async
work has started.  The correct fix is on the playground side: make
`run` return the Promise, or wrap the body in `async function`.  The
`wait_until_settled` / `bundle(wait_for_running=True)` mechanism is a
legacy best-effort safety net for such playgrounds; it is not relied
on by well-formed callers and its default is now off.

Reader thread + condition variable
==================================
The reader thread updates the caches under `self._cv` and notifies;
every wait method parks on the condition variable and re-checks its
predicate on each notification. Sends are gated by a separate
`_send_lock` so a slow send never blocks a state update.

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

Disconnect handling: when the WebSocket closes or `close()` is called,
every pending `command()` is failed immediately with
`RemoteNotConnected`. A `command()` that was waiting on a
`command_result` does not block out its full timeout; it raises the
underlying disconnect immediately.
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

            # Wait for the first presence event so we learn whether a
            # browser is connected.  When `require_browser` is set,
            # wait the full connection timeout — the caller explicitly
            # asked for a browser, so a slow one (ngrok handshake, a
            # tab reload in progress) should not be mistaken for none.
            # Otherwise use a short cap so a browser-less session
            # returns promptly and the caller can start a command that
            # will wait for the browser to appear.
            if require_browser:
                presence_deadline = deadline
            else:
                presence_deadline = min(
                    deadline, time.monotonic() + 3.0
                )

            while (self._presence["browser"] == 0
                   and self._presence["remote"] <= 1):
                remaining = presence_deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cv.wait(timeout=min(remaining, 0.25))

            have_browser = self._presence["browser"] > 0

            if require_browser and not have_browser:
                raise RemoteCliError(
                    f"no browser connected at {self._url} after "
                    f"{timeout:.0f}s. Open the playground in a browser "
                    f"tab first, or increase --timeout."
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
        # Defensive: if the reader thread already failed the pending
        # commands when it noticed the socket close, this is a no-op
        # (each pending event is already set).  If close() raced the
        # reader and the reader hasn't yet reached its own finally,
        # this fails them now so the caller does not wait out a
        # command timeout.
        self._fail_pending(RemoteNotConnected("client closed"))

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
            self._fail_pending(RemoteNotConnected(
                "connection closed before command result"
            ))

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

    def _fail_pending(self, exc: Exception) -> None:
        """Set `error` on every pending command and wake its waiter.

        Called from the reader loop's shutdown path and from close().
        The `is_set` guard makes it idempotent: the first call wins,
        subsequent calls are no-ops.
        """
        with self._lock:
            snapshot = list(self._pending.values())
        for pending in snapshot:
            if pending["event"].is_set():
                continue
            pending["error"] = exc
            pending["event"].set()

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
        """Send a patch, wait for the resulting state, return applied.

        Returns a dict mapping each requested field to the value the
        browser reports after the patch.  A field whose applied value
        differs from the requested value was clamped, rejected, or
        not applied at all; the caller compares.  If the state does
        not arrive within `timeout`, the returned dict reflects the
        last-known cache (which may be pre-patch); the caller can
        distinguish "applied a different value" from "could not
        verify" only by repeating the read.
        """
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
        """Send one command and block until it settles.

        Returns the `command_result` message on `ok: true`.  Raises
        `RemoteCommandFailed` on `ok: false`.  Raises
        `RemoteNotConnected` if the connection closes while the
        command is in flight — the disconnect is surfaced immediately
        rather than after `timeout`.  Raises `RemoteTimeout` if the
        command does not settle within `timeout`.

        See "Async actions" in the module docstring for what "settles"
        means: on a correctly-fixed playground, `command()` returns
        after the action's observable work is done, not merely after
        the action was dispatched.
        """
        cmd_id = "cli-" + uuid.uuid4().hex[:12]
        pending = {
            "event":  threading.Event(),
            "result": None,
            "error":  None,
        }
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

        if pending["error"] is not None:
            raise pending["error"]
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
        """Legacy safety net: wait for a bool field to finish running.

        On a correctly-fixed playground, `command()` already blocks
        until the action's work settles (see "Async actions" in the
        module docstring).  By the time `command()` returns,
        `status.running` is already False, and this method returns
        immediately after one `state_request` round trip.  It is a
        no-op.

        On a playground whose action `run` dispatches async work
        *without* returning the Promise, `command()` returns
        immediately and the action's work may still be in flight.
        This method does one `state_request` and checks the flag: if
        it reads True, it waits for the flag to go False; if it
        reads False, it returns.

        This is best-effort and racy.  A wrapper that has not yet
        flipped the flag when the `state_request` reaches the browser
        is missed.  The correct fix is on the playground side (make
        `run` return its Promise, or wrap its body in `async
        function`), not here.
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
               wait_for_running: bool = False,
               timeout: float = 60.0,
               on_progress: Optional[Callable[[str, dict], None]] = None,
               ) -> dict:
        """Apply a coordinated set of writes and actions.

        `set`       : one patch with N field updates, then one
                      `state_request` to verify.  A field whose
                      applied value differs from the requested value
                      (setter clamped it, rejected it, or the patch
                      never landed) appears in the returned
                      `mismatched` dict.

        `actions`   : sequential commands, one round trip each.
                      `command()` blocks until the action's work
                      settles on a correctly-fixed playground, so
                      sequential actions can rely on the previous
                      having taken effect.  See "Async actions" in
                      the module docstring.

        `select`    : one final `select` command.

        Then a single refresh and return of the fresh values,
        entities, and any set mismatches.

        `wait_for_running` inserts a `wait_until_settled("status.running")`
        after each action.  This is a legacy safety net for
        playgrounds whose `run` dispatches async work without
        returning the Promise; it is best-effort and racy (see
        `wait_until_settled`'s docstring).  Defaults to False.  On a
        correctly-fixed playground, leave it False — you would pay
        one extra round trip per action for no correctness gain.

        Returns:
            {
              "values":     dict of all current field values,
              "entities":   dict of level -> items, or None,
              "mismatched": dict of {field: {"requested": x,
                                             "applied":   y}}
                            for any `set` field whose applied value
                            differs from the requested value.
                            Empty when `set` is None or the patch
                            applied cleanly.
            }
        """
        progress = on_progress or (lambda kind, data: None)
        mismatched: dict = {}

        if set:
            applied = self.patch_and_verify(set, timeout=5.0)
            mismatched = {
                k: {"requested": set[k], "applied": applied.get(k)}
                for k in set
                if applied.get(k) != set[k]
            }
            progress("patch", {
                "values":     dict(set),
                "mismatched": mismatched,
            })

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
                "values":     dict(self._values),
                "entities":   (dict(self._entities)
                               if self._entities is not None else None),
                "mismatched": mismatched,
            }
