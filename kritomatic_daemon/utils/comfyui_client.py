"""
comfyui_client.py — synchronous HTTP + WebSocket client for ComfyUI.

Transport layer for the Kritomatic `comfyui` handler. Knows nothing about
Krita, nothing about the tooling-nodes' ETN_* semantics, and nothing about
the workflow rewrite that turns ETN_* nodes into real ComfyUI nodes. It
speaks HTTP and WebSocket to a running ComfyUI server and hands decoded
images and event data back to its caller.

Dependencies
------------
    HTTP:        stdlib `urllib.request`   (no external dep)
    WebSocket:   `websockets >= 12`        (`websockets.sync.client`)

=============================================================================
THE PROTOCOL
=============================================================================

Preview routing
---------------
ComfyUI routes `UNENCODED_PREVIEW_IMAGE` binary frames to the WebSocket
whose client id matches the submitting prompt's `client_id`. The
submitting client id is bound at `/prompt` time, and the server must
have already registered the WebSocket under that id when the submit
arrives. Registration is asynchronous on the server side: the WS open
succeeds on the client before the server's `on_open` handler has run.

The client therefore waits for the server's first `status` message on
each new WebSocket before submitting any prompt. That message is sent
immediately after registration, so receiving it is proof the server
now knows this client id.

Two WebSocket connections can be open at once: one persistent watcher
that receives `etn_workflow_published` events from the browser-side JS,
and one short-lived connection per `generate()` or `generate_batch()`
call that receives execution events and preview frames.

Single-flight
-------------
Both `generate()` and `generate_batch()` hold `self._lock` for their
entire duration. Two overlapping calls would send previews to whichever
WS connected last, so they are serialized. The watcher also acquires
the lock, but only for the WS connect, not for its read loop.

Interruptible lock acquisition
------------------------------
`generate_batch` acquires `self._lock` in short intervals rather than
blocking on it. Between intervals it consults the caller's
`should_stop` callback. This is what lets a cancel request reach a
batch that is waiting for the lock — a batch that has been queued
behind another but has not yet been able to submit anything. Without
this, the waiting batch would sit on `lock.acquire()` until the other
batch finished, and the user's cancel would only take effect then.

When the stop condition fires while waiting for the lock, the batch
returns immediately, every prompt marked `CANCELLED`. Nothing was
submitted to ComfyUI, so nothing needs to be interrupted there.

Batch execution
---------------
`generate_batch(graphs, ...)` submits N prompts on one WebSocket and
reads events until all N have completed. ComfyUI queues the prompts
and executes them sequentially on a single worker, so events for one
prompt never interleave with another. The client tracks each prompt
by the prompt_id it sent; when an `executing` event with a null `node`
arrives for that prompt, that prompt is done. Binary preview frames
arrive in execution order and are popped positionally by whichever
prompt's `executed` event is being processed at that moment.

For each completed prompt the client invokes a caller-supplied
`on_result(index, result)` callback on the calling thread.

`should_stop` is an optional zero-argument callable consulted at the
top of every read iteration and every lock-acquisition interval. When
it returns True, the batch marks every prompt that has not yet
completed as cancelled, asks ComfyUI to interrupt the currently-
executing prompt and drop the queued ones, and returns. The read
timeout in the batch loop is 1 second, and the lock-acquisition poll
interval is also 1 second, so a stop request is noticed promptly.

HTTP endpoints used
-------------------
GET  /object_info
POST /prompt
POST /interrupt
POST /queue
PUT  /api/etn/image/{id}
GET  /api/etn/image/{id}
POST /api/etn/workflow/subscribe
POST /api/etn/workflow/unsubscribe

JSON event types
................
    status, execution_start, execution_cached, executing, progress,
    executed, execution_error, execution_interrupted,
    etn_workflow_published

Binary preview frames
.....................
Header: 8 bytes, `struct.unpack(">II", data[:8])` -> (event_type, format).
    event_type == 1   BinaryEventTypes.UNENCODED_PREVIEW_IMAGE
    format     == 1   JPEG
    format     == 2   PNG

Output-node handling
--------------------
A workflow may contain several output nodes. Two families are relevant:

  * Tooling-nodes output nodes (`ETN_KritaOutput`, `ETN_SaveImageCache`).
    These produce an `executed` event whose `output.images` entries
    carry `source: "websocket"` or `source: "http"`. They are the ones
    this client retrieves.

  * Stock ComfyUI output nodes (`SaveImage`, `PreviewImage`). These
    produce an `executed` event whose `output.images` entries carry
    `filename`, `subfolder`, `type`, and no `source`. They write to
    disk; there is nothing for this client to fetch. They are skipped.

=============================================================================
WHAT THIS MODULE DOES NOT DO
=============================================================================
    - Rewrite ETN_* nodes into real ComfyUI nodes. See comfyui_tooling.
    - Read or write pixels from a Krita document. See comfyui_krita.
    - Manage workflow files, parameter values, or generation history.
"""

from __future__ import annotations

import json
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ============================================================================
#  Dependency probe
# ============================================================================

try:
    from websockets.sync.client import connect as _ws_connect
    from websockets.exceptions import ConnectionClosed as _WSConnectionClosed
    _WS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ws_connect = None
    _WSConnectionClosed = Exception
    _WS_AVAILABLE = False


# ============================================================================
#  Data types
# ============================================================================

@dataclass
class JobInfo:
    """One entry from an `executed` event's `output.info` list.

    batch_mode: "default" | "images" | "animation" | "layers"
    """
    name: str = ""
    offset_x: int = 0
    offset_y: int = 0
    batch_mode: str = "default"
    resize_canvas: bool = False


@dataclass
class TextOutput:
    """One entry from an `executed` event's `output.text` list."""
    key: str
    name: str
    text: str
    content_type: str = "text/plain"


@dataclass
class GenerationResult:
    """Everything a single prompt produced.

    `images`, `infos`, and `event_index` are parallel lists: entry `i`
    in all three describes the same image. `event_index[i]` is the
    ordinal of the `executed` event that produced that image, counting
    from 0 within the prompt.

    `error` is None for a successful run. When set, it holds a
    human-readable description of why the prompt failed, was
    interrupted, was cancelled, or produced no usable images. In
    `generate()` a failure raises; in `generate_batch()` a failure sets
    this field and the batch continues.

    The literal string `"cancelled"` marks a run that never produced a
    result because the caller asked the batch to stop.
    """
    prompt_id: str
    images: list[bytes] = field(default_factory=list)
    infos: list[JobInfo] = field(default_factory=list)
    event_index: list[int] = field(default_factory=list)
    texts: list[TextOutput] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


CANCELLED = "cancelled"


class ComfyError(Exception):
    """Any transport or ComfyUI-side error."""


class ComfyTimeout(ComfyError):
    """`generate()` exceeded its timeout."""


class ComfyCancelled(ComfyError):
    """The prompt was interrupted."""


# ============================================================================
#  Binary preview frame decoding
# ============================================================================

_PREVIEW_HEADER = struct.Struct(">II")
_PREVIEW_EVENT = 1
_PREVIEW_FORMAT_JPEG = 1
_PREVIEW_FORMAT_PNG = 2


def decode_preview_frame(data: bytes) -> Optional[bytes]:
    """Return embedded image bytes if `data` is a preview frame, else None."""
    if len(data) < _PREVIEW_HEADER.size:
        return None
    event_type, fmt = _PREVIEW_HEADER.unpack_from(data, 0)
    if event_type != _PREVIEW_EVENT:
        return None
    if fmt not in (_PREVIEW_FORMAT_JPEG, _PREVIEW_FORMAT_PNG):
        return None
    return data[_PREVIEW_HEADER.size:]


# ============================================================================
#  HTTP plumbing
# ============================================================================

def _http_request(method: str, url: str, *,
                  data: Optional[bytes] = None,
                  headers: Optional[dict] = None,
                  timeout: float = 60.0):
    """Issue one HTTP request; return (status, headers, body_bytes).

    Raises ComfyError on network failure. Does NOT raise on HTTP error
    statuses -- the caller inspects `status`.
    """
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()
        except Exception:
            body = b""
        return e.code, dict(e.headers or {}), body
    except urllib.error.URLError as e:
        raise ComfyError(f"HTTP {method} {url}: {e.reason}") from e
    except Exception as e:
        raise ComfyError(f"HTTP {method} {url}: {e}") from e


# ============================================================================
#  Client
# ============================================================================

class ComfyClient:
    """Synchronous HTTP + WebSocket client for a running ComfyUI server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188,
                 connect_timeout: float = 10.0):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.ws_base = f"ws://{host}:{port}/ws"
        self.connect_timeout = connect_timeout

        self._lock = threading.Lock()

        self._passthrough_headers = {
            "ngrok-skip-browser-warning": "69420",
            "skip_zrok_interstitial": "69420",
        }

        # ---- Watcher state ----
        self._watch_callbacks: list[Callable[[str, dict], None]] = []
        self._watch_stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None
        self._watch_client_id = str(uuid.uuid4())
        self._watch_state_lock = threading.Lock()
        self._watch_connected = False
        self._watch_subscribed = threading.Event()

    # ------------------------------------------------------------------
    #  Lock acquisition
    # ------------------------------------------------------------------

    def _acquire_lock_interruptibly(
            self,
            should_stop: Optional[Callable[[], bool]],
            timeout: float,
    ) -> bool:
        """Acquire `self._lock`, polling `should_stop` at intervals.

        Returns True if the lock was acquired, False if `should_stop`
        returned True while waiting or the timeout expired. The poll
        interval is one second, so a stop request during the wait is
        noticed promptly rather than after the current holder finishes.
        """
        deadline = time.monotonic() + timeout
        while True:
            if should_stop is not None and should_stop():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._lock.acquire(timeout=min(1.0, remaining)):
                return True

    # ------------------------------------------------------------------
    #  HTTP helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _json_request(self, method: str, path: str,
                      body: Optional[dict] = None,
                      timeout: float = 60.0) -> Any:
        data = None
        headers = dict(self._passthrough_headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, _hdrs, resp_body = _http_request(
            method, self._url(path),
            data=data, headers=headers, timeout=timeout,
        )
        if status >= 400:
            snippet = resp_body[:500].decode("utf-8", errors="replace")
            raise ComfyError(f"HTTP {status} {method} {path}: {snippet}")
        if not resp_body:
            return {}
        try:
            return json.loads(resp_body)
        except json.JSONDecodeError as e:
            raise ComfyError(
                f"HTTP {method} {path}: expected JSON, got "
                f"{resp_body[:80]!r} ({e})"
            ) from e

    def _bytes_request(self, method: str, path: str, body: bytes,
                       timeout: float = 300.0,
                       content_type: str = "application/octet-stream"):
        headers = dict(self._passthrough_headers)
        headers["Content-Type"] = content_type
        status, _hdrs, resp_body = _http_request(
            method, self._url(path),
            data=body, headers=headers, timeout=timeout,
        )
        return status, resp_body

    def _download_request(self, path: str, timeout: float = 300.0) -> bytes:
        headers = dict(self._passthrough_headers)
        status, _hdrs, body = _http_request(
            "GET", self._url(path), headers=headers, timeout=timeout,
        )
        if status >= 400:
            raise ComfyError(
                f"HTTP {status} GET {path}: "
                f"{body[:200].decode('utf-8', errors='replace')}"
            )
        return body

    # ------------------------------------------------------------------
    #  Public HTTP API
    # ------------------------------------------------------------------

    def object_info(self) -> dict:
        """GET /object_info."""
        return self._json_request("GET", "object_info", timeout=30.0)

    def upload_image(self, image_id: str, data: bytes) -> bool:
        """PUT /api/etn/image/{id}. Returns True if newly uploaded, False
        if ComfyUI already had this id cached."""
        status, _ = self._bytes_request(
            "PUT", f"api/etn/image/{image_id}", data, timeout=60.0,
        )
        if status == 201:
            return True
        if status == 200:
            return False
        raise ComfyError(f"PUT /api/etn/image/{image_id} returned {status}")

    def download_image(self, image_id: str) -> bytes:
        """GET /api/etn/image/{id}."""
        return self._download_request(f"api/etn/image/{image_id}",
                                      timeout=300.0)

    def interrupt(self) -> None:
        """POST /interrupt. Best-effort."""
        try:
            self._json_request("POST", "interrupt", body={}, timeout=10.0)
        except ComfyError:
            pass

    def cancel_queued(self, prompt_ids) -> None:
        """POST /queue {"delete": [...]}. Best-effort."""
        ids = list(prompt_ids)
        if not ids:
            return
        try:
            self._json_request(
                "POST", "queue", body={"delete": ids}, timeout=10.0,
            )
        except ComfyError:
            pass

    # ------------------------------------------------------------------
    #  Workflow watcher
    # ------------------------------------------------------------------

    def watch_workflows(self, callback: Callable[[str, dict], None],
                        replace: bool = True) -> None:
        """Register a callback and start the watcher if not running."""
        if not _WS_AVAILABLE:
            raise ComfyError(
                "websockets>=12 is required for watch_workflows(). "
                "Install with: pip install 'websockets>=12'"
            )
        with self._watch_state_lock:
            if replace:
                self._watch_callbacks.clear()
            if callback not in self._watch_callbacks:
                self._watch_callbacks.append(callback)
            if self._watch_thread is None or not self._watch_thread.is_alive():
                self._watch_stop.clear()
                self._watch_subscribed.clear()
                self._watch_thread = threading.Thread(
                    target=self._watch_loop,
                    name="comfy-workflow-watch",
                    daemon=True,
                )
                self._watch_thread.start()

    def wait_subscribed(self, timeout: float = 5.0) -> bool:
        """Block until the watcher's subscription is live."""
        return self._watch_subscribed.wait(timeout=timeout)

    def unwatch_workflows(self) -> None:
        """Stop the watcher and clear callbacks. Idempotent."""
        with self._watch_state_lock:
            self._watch_callbacks.clear()
            self._watch_stop.set()
            thread = self._watch_thread
            self._watch_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        try:
            self._unsubscribe_workflows()
        except Exception:
            pass
        self._watch_subscribed.clear()

    def is_watching(self) -> bool:
        with self._watch_state_lock:
            return self._watch_connected

    def disconnect(self) -> None:
        """Shut down the watcher."""
        self.unwatch_workflows()

    # ---- Watcher internals ----

    def _watch_loop(self) -> None:
        while not self._watch_stop.is_set():
            try:
                self._watch_connect_and_read()
            except Exception:
                with self._watch_state_lock:
                    self._watch_connected = False
                self._watch_subscribed.clear()
            if self._watch_stop.wait(2.0):
                return

    def _watch_connect_and_read(self) -> None:
        ws_url = f"{self.ws_base}?clientId={self._watch_client_id}"

        with self._lock:
            ws = _ws_connect(
                ws_url,
                max_size=2 ** 30,
                open_timeout=self.connect_timeout,
                close_timeout=5.0,
                additional_headers=self._passthrough_headers,
            )
            try:
                self._subscribe_workflows()
            except Exception:
                ws.close()
                raise
            with self._watch_state_lock:
                self._watch_connected = True
            self._watch_subscribed.set()

        try:
            while not self._watch_stop.is_set():
                try:
                    msg = ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except _WSConnectionClosed:
                    return
                if msg is None:
                    return
                if isinstance(msg, (bytes, bytearray)):
                    continue
                self._watch_dispatch_json(msg)
        finally:
            with self._watch_state_lock:
                self._watch_connected = False
            self._watch_subscribed.clear()
            try:
                ws.close()
            except Exception:
                pass

    def _watch_dispatch_json(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("type") != "etn_workflow_published":
            return
        data = msg.get("data") or {}
        publisher = data.get("publisher") or {}
        name = publisher.get("name") or publisher.get("id") or "unknown"
        graph = data.get("workflow")
        if not isinstance(graph, dict) or not graph:
            return
        with self._watch_state_lock:
            callbacks = list(self._watch_callbacks)
        for cb in callbacks:
            try:
                cb(name, graph)
            except Exception:
                pass

    def _subscribe_workflows(self) -> None:
        """Subscribe this client id. Idempotent."""
        try:
            self._json_request(
                "POST", "api/etn/workflow/subscribe",
                body={"client_id": self._watch_client_id},
                timeout=10.0,
            )
        except ComfyError as e:
            if "Already subscribed" in str(e):
                return
            raise

    def _unsubscribe_workflows(self) -> None:
        try:
            self._json_request(
                "POST", "api/etn/workflow/unsubscribe",
                body={"client_id": self._watch_client_id},
                timeout=10.0,
            )
        except ComfyError:
            pass

    # ------------------------------------------------------------------
    #  Generation — single prompt
    # ------------------------------------------------------------------

    def generate(self, graph: dict, timeout: float = 600.0,
                 on_progress: Optional[Callable[[dict], None]] = None,
                 ) -> GenerationResult:
        """Submit `graph`, block until the prompt finishes, return results.

        Raises ComfyError, ComfyTimeout, ComfyCancelled. Holds
        `self._lock` for the whole call.
        """
        if not _WS_AVAILABLE:
            raise ComfyError(
                "websockets>=12 is required for generate(). "
                "Install with: pip install 'websockets>=12'"
            )
        with self._lock:
            return self._generate_locked(graph, timeout, on_progress)

    def _generate_locked(self, graph: dict, timeout: float,
                         on_progress: Optional[Callable[[dict], None]],
                         ) -> GenerationResult:
        prompt_id = str(uuid.uuid4())
        client_id = str(uuid.uuid4())
        deadline = time.monotonic() + timeout
        ws_url = f"{self.ws_base}?clientId={client_id}"

        result = GenerationResult(prompt_id=prompt_id)
        pending_previews: list[bytes] = []
        event_index = 0
        final_node_seen = False

        try:
            with _ws_connect(
                ws_url,
                max_size=2 ** 30,
                open_timeout=self.connect_timeout,
                close_timeout=5.0,
                additional_headers=self._passthrough_headers,
            ) as ws:
                self._await_ws_ready(ws)
                self._submit(graph, client_id, prompt_id)

                while not final_node_seen:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ComfyTimeout(
                            f"prompt {prompt_id} did not complete "
                            f"within {timeout}s"
                        )
                    try:
                        msg = ws.recv(timeout=min(remaining, 5.0))
                    except TimeoutError:
                        continue
                    except _WSConnectionClosed as e:
                        raise ComfyError(
                            f"WebSocket closed before prompt completed: {e}"
                        ) from e

                    if msg is None:
                        raise ComfyError("WebSocket returned no message")

                    if isinstance(msg, (bytes, bytearray)):
                        img_bytes = decode_preview_frame(bytes(msg))
                        if img_bytes is not None:
                            pending_previews.append(img_bytes)
                        continue

                    try:
                        event = json.loads(msg)
                    except json.JSONDecodeError:
                        continue

                    ev_type = event.get("type")
                    ev_data = event.get("data", {}) or {}
                    ev_prompt = ev_data.get("prompt_id")

                    if ev_prompt is not None and ev_prompt != prompt_id:
                        continue

                    if ev_type == "executed":
                        self._handle_executed(
                            event, pending_previews, result, event_index,
                        )
                        event_index += 1
                    elif ev_type == "execution_error":
                        err_msg = ev_data.get(
                            "exception_message", "execution_error",
                        )
                        tb = ev_data.get("traceback", "")
                        raise ComfyError(
                            f"ComfyUI execution error: {err_msg}\n{tb}"
                        )
                    elif ev_type == "execution_interrupted":
                        raise ComfyCancelled(
                            f"prompt {prompt_id} was interrupted"
                        )
                    elif ev_type == "executing":
                        self._fire_progress(on_progress, event)
                        if ev_data.get("node") is None:
                            final_node_seen = True
                    elif ev_type == "progress":
                        self._fire_progress(on_progress, event)

        except (ComfyError, ComfyTimeout, ComfyCancelled):
            raise
        except Exception as e:
            raise ComfyError(f"generate() failed: {e}") from e

        if not result.images:
            raise ComfyError(
                "ComfyUI completed the prompt but produced no image that "
                "this client can retrieve. The workflow's output node may "
                "be a stock SaveImage or PreviewImage, which writes to "
                "disk but does not send the image over the WebSocket. "
                "Add an ETN_KritaOutput node (with the final image "
                "connected to its input) to receive the image in Krita."
            )
        if all(len(b) == 0 for b in result.images):
            raise ComfyError(
                "ComfyUI returned a cached execution with no image data: "
                "the workflow's inputs are identical to the last run, so "
                "the output node was skipped. Change at least one input "
                "(a parameter, the selection, or the canvas) and try "
                "again."
            )

        return result

    # ------------------------------------------------------------------
    #  Generation — batch of prompts
    # ------------------------------------------------------------------

    def generate_batch(self,
                       graphs: list[dict],
                       timeout: float = 600.0,
                       on_result: Optional[Callable[[int, GenerationResult],
                                                    None]] = None,
                       should_stop: Optional[Callable[[], bool]] = None,
                       ) -> list[GenerationResult]:
        """Submit multiple prompts and collect their results.

        `graphs` is a list of ComfyUI prompt dicts. They are submitted
        in order; ComfyUI queues them and executes them sequentially on
        a single worker. The function reads events from one WebSocket
        until every prompt has either produced a result or failed, or
        the total timeout expires.

        `on_result(index, result)` is called as each prompt completes,
        on the calling thread.

        `should_stop()` is consulted at the top of every read iteration
        AND between lock-acquisition attempts. When it returns True,
        all prompts that have not yet completed are marked cancelled
        (`result.error = CANCELLED`), ComfyUI is asked to interrupt
        the current prompt and drop the queued ones, and the function
        returns.

        If the stop condition fires while this call is waiting for
        `self._lock` (because another batch is currently executing),
        the function returns immediately without ever submitting: no
        WebSocket is opened, no prompt reaches ComfyUI, nothing needs
        to be interrupted there. This is what lets a cancel reach a
        batch that is queued behind another one.

        Returns a list of `GenerationResult` in the same order as
        `graphs`. Prompts that failed have their `.error` set.

        Unlike `generate()`, this does not raise on failure of an
        individual prompt; the caller inspects each result's `.error`.
        It does raise on WebSocket-level failures that abort the whole
        batch.
        """
        if not _WS_AVAILABLE:
            raise ComfyError(
                "websockets>=12 is required for generate_batch(). "
                "Install with: pip install 'websockets>=12'"
            )

        # Interruptible lock acquisition. When this returns False the
        # stop condition fired during the wait, or the overall timeout
        # expired before the lock became available. Either way, no
        # prompt was submitted: return a cancelled result for each.
        if not self._acquire_lock_interruptibly(should_stop, timeout):
            results: list[GenerationResult] = []
            for i, _ in enumerate(graphs):
                r = GenerationResult(prompt_id=str(uuid.uuid4()),
                                     error=CANCELLED)
                results.append(r)
                if on_result is not None:
                    try:
                        on_result(i, r)
                    except Exception:
                        pass
            return results

        try:
            return self._generate_batch_locked(
                graphs, timeout, on_result, should_stop,
            )
        finally:
            self._lock.release()

    def _generate_batch_locked(self,
                               graphs: list[dict],
                               timeout: float,
                               on_result: Optional[Callable[
                                   [int, GenerationResult], None]],
                               should_stop: Optional[Callable[[], bool]],
                               ) -> list[GenerationResult]:
        if not graphs:
            return []

        client_id = str(uuid.uuid4())
        ws_url = f"{self.ws_base}?clientId={client_id}"
        deadline = time.monotonic() + timeout

        prompts: list[dict] = []
        pending_previews: list[bytes] = []

        def _finish(entry: dict,
                    error_msg: Optional[str] = None) -> int:
            if entry["done"]:
                return 0
            r = entry["result"]
            if error_msg is not None:
                r.error = error_msg
            entry["done"] = True
            if on_result is not None:
                try:
                    on_result(entry["index"], r)
                except Exception:
                    pass
            return 1

        try:
            with _ws_connect(
                ws_url,
                max_size=2 ** 30,
                open_timeout=self.connect_timeout,
                close_timeout=5.0,
                additional_headers=self._passthrough_headers,
            ) as ws:
                self._await_ws_ready(ws)

                for i, graph in enumerate(graphs):
                    prompt_id = str(uuid.uuid4())
                    result = GenerationResult(prompt_id=prompt_id)
                    entry = {
                        "index": i,
                        "prompt_id": prompt_id,
                        "result": result,
                        "done": False,
                        "event_index": 0,
                    }
                    try:
                        self._submit(graph, client_id, prompt_id)
                    except ComfyError as e:
                        result.error = f"submit failed: {e}"
                    prompts.append(entry)
                    if result.error:
                        _finish(entry)

                done_count = sum(1 for p in prompts if p["done"])
                total = len(prompts)

                while done_count < total:
                    if should_stop is not None and should_stop():
                        cancel_ids = []
                        for p in prompts:
                            if p["done"]:
                                continue
                            cancel_ids.append(p["prompt_id"])
                            done_count += _finish(p, error_msg=CANCELLED)
                        self.interrupt()
                        if cancel_ids:
                            self.cancel_queued(cancel_ids)
                        break

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        for p in prompts:
                            if not p["done"]:
                                done_count += _finish(
                                    p,
                                    error_msg=f"timed out after "
                                              f"{timeout:.0f}s",
                                )
                        break

                    try:
                        msg = ws.recv(timeout=min(remaining, 1.0))
                    except TimeoutError:
                        continue
                    except _WSConnectionClosed as e:
                        for p in prompts:
                            if not p["done"]:
                                done_count += _finish(
                                    p, error_msg=f"WebSocket closed: {e}",
                                )
                        break

                    if msg is None:
                        continue

                    if isinstance(msg, (bytes, bytearray)):
                        img_bytes = decode_preview_frame(bytes(msg))
                        if img_bytes is not None:
                            pending_previews.append(img_bytes)
                        continue

                    try:
                        event = json.loads(msg)
                    except json.JSONDecodeError:
                        continue

                    ev_type = event.get("type")
                    ev_data = event.get("data", {}) or {}
                    ev_pid = ev_data.get("prompt_id")

                    target = next(
                        (p for p in prompts if p["prompt_id"] == ev_pid),
                        None,
                    )
                    if target is None or target["done"]:
                        continue

                    if ev_type == "executed":
                        self._handle_executed(
                            event, pending_previews,
                            target["result"], target["event_index"],
                        )
                        target["event_index"] += 1
                    elif ev_type == "execution_error":
                        err_msg = ev_data.get(
                            "exception_message", "execution_error",
                        )
                        tb = ev_data.get("traceback", "")
                        done_count += _finish(
                            target, error_msg=f"{err_msg}\n{tb}".strip(),
                        )
                    elif ev_type == "execution_interrupted":
                        done_count += _finish(
                            target, error_msg="interrupted",
                        )
                    elif ev_type == "executing":
                        if ev_data.get("node") is None:
                            r = target["result"]
                            err = None
                            if not r.images:
                                err = (
                                    "no image produced (the workflow's "
                                    "output node may be a stock "
                                    "SaveImage or PreviewImage, which "
                                    "writes to disk but does not send "
                                    "the image over the WebSocket)"
                                )
                            elif all(len(b) == 0 for b in r.images):
                                err = "cached execution with no image data"
                            done_count += _finish(target, error_msg=err)

        except (ComfyError, ComfyTimeout, ComfyCancelled):
            raise
        except Exception as e:
            raise ComfyError(f"generate_batch() failed: {e}") from e

        return [p["result"] for p in prompts]

    # ------------------------------------------------------------------
    #  Shared internals
    # ------------------------------------------------------------------

    def _await_ws_ready(self, ws) -> None:
        """Block until the server's first JSON message arrives."""
        try:
            ws.recv(timeout=3.0)
        except TimeoutError:
            return
        except Exception:
            return

    def _submit(self, graph: dict, client_id: str, prompt_id: str) -> None:
        """POST /prompt and verify the server echoed our prompt_id."""
        body = {
            "prompt": graph,
            "client_id": client_id,
            "prompt_id": prompt_id,
        }
        resp = self._json_request("POST", "prompt", body=body, timeout=30.0)
        returned = resp.get("prompt_id")
        if returned != prompt_id:
            raise ComfyError(
                f"Prompt id mismatch: sent {prompt_id!r}, "
                f"server returned {returned!r}. "
                f"ComfyUI may have rejected the graph; "
                f"full response: {resp!r}"
            )

    @staticmethod
    def _fire_progress(cb: Optional[Callable[[dict], None]],
                       event: dict) -> None:
        if cb is None:
            return
        try:
            cb(event)
        except Exception:
            pass

    def _handle_executed(self, event: dict, pending_previews: list[bytes],
                         result: GenerationResult,
                         event_index: int) -> None:
        """Consume one `executed` event.

        Only entries whose `source` is "websocket" or "http" are for us.
        ComfyUI's stock output nodes (SaveImage, PreviewImage) emit an
        `executed` event whose `images` entries carry `filename`,
        `subfolder` and `type` keys but no `source`; those are
        filesystem saves, not something this client fetches.
        """
        data = event.get("data", {}) or {}
        output = data.get("output") or {}

        images = output.get("images") or []
        infos = output.get("info") or []
        texts = output.get("text") or []

        shared_info = JobInfo()
        if infos and isinstance(infos[0], dict):
            info = infos[0]
            shared_info = JobInfo(
                name=str(info.get("name", "")),
                offset_x=int(info.get("offset_x", 0)),
                offset_y=int(info.get("offset_y", 0)),
                batch_mode=str(info.get("batch_mode", "default")),
                resize_canvas=bool(info.get("resize_canvas", False)),
            )

        for img_entry in images:
            source = (img_entry.get("source")
                      if isinstance(img_entry, dict) else None)

            if source == "websocket":
                if pending_previews:
                    result.images.append(pending_previews.pop(0))
                else:
                    result.images.append(b"")
                    result.warnings.append(
                        "ComfyUI sent an `executed` event for a WebSocket "
                        "image but no preview frame arrived on this "
                        "connection."
                    )
            elif source == "http":
                img_id = (img_entry.get("id")
                          if isinstance(img_entry, dict) else None)
                if img_id:
                    try:
                        result.images.append(self.download_image(img_id))
                    except ComfyError:
                        result.images.append(b"")
                else:
                    result.images.append(b"")
            else:
                continue

            result.infos.append(shared_info)
            result.event_index.append(event_index)

        node_key = str(data.get("node", ""))
        for txt in texts:
            if not isinstance(txt, dict):
                continue
            result.texts.append(TextOutput(
                key=node_key,
                name=str(txt.get("name", "")),
                text=str(txt.get("text", "")),
                content_type=str(txt.get("content-type", "text/plain")),
            ))


# ============================================================================
#  Process-wide shared client
# ============================================================================

_shared_lock = threading.Lock()
_shared_client: Optional[ComfyClient] = None


def get_client(host: str = "127.0.0.1", port: int = 8188) -> ComfyClient:
    """Return the process-wide ComfyClient, creating it on first use."""
    global _shared_client
    with _shared_lock:
        if (_shared_client is None
                or _shared_client.host != host
                or _shared_client.port != port):
            if _shared_client is not None:
                try:
                    _shared_client.disconnect()
                except Exception:
                    pass
            _shared_client = ComfyClient(host, port)
        return _shared_client


def reset_client() -> None:
    """Drop the shared client. Next get_client() rebuilds."""
    global _shared_client
    with _shared_lock:
        if _shared_client is not None:
            try:
                _shared_client.disconnect()
            except Exception:
                pass
        _shared_client = None
