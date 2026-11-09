"""comfyui_proxy_client.py — HTTP client for the Comfy API v2, served by
comfy-api-proxy on 127.0.0.1:8189.

Transport layer for the migration away from comfyui-tooling-nodes. Knows
nothing about Krita, nothing about graph rewriting, nothing about pasting.
Speaks exactly the endpoints the proxy exposes:

    GET  /api/v2/health                       — process liveness probe
    POST /api/v2/assets                       — multipart upload, deduped
    GET  /api/v2/assets/{id}/content          — raw bytes (Range-capable)
    POST /api/v2/jobs                         — submit (idempotent)
    GET  /api/v2/jobs/{id}                    — poll (authoritative)
    POST /api/v2/jobs/{id}/cancel             — atomic per-id cancel
    GET  /api/v2/jobs                         — list (optional; debugging)

Deliberately *not* in scope for this client:

  * GET /object_info — not exposed by the proxy; the daemon's existing
    ComfyClient still talks to ComfyUI on 8188 for that. Two clients,
    two ports, two concerns.
  * GET /api/v2/jobs/{id}/events — SSE. The v2 contract makes polling
    authoritative and SSE a live enhancement; the daemon's own event
    bus already carries the live state. Poll instead.
  * Anything that touches /api/etn/* — that is the tooling-nodes
    transport this migration removes.

Design
======
  * Pure stdlib: `urllib.request`, `socket`, `uuid`, `json`. No
    websockets, no aiohttp, no requests. The daemon's existing
    ComfyClient has the same zero-dependency philosophy for HTTP; this
    client extends it to the v2 surface.
  * No connection pooling. `urllib.request.urlopen()` per call. The
    proxy is local; the per-call overhead is negligible and it removes
    a whole class of "stale connection" bugs.
  * Thread-safe by construction. The client holds no per-request state
    (no lock, no pool, no cursor). N threads can share one instance.
  * Structured errors. `ProxyHTTPError` carries the parsed error
    envelope (`code`, `message`, `details`, `status`) so the daemon
    can route on `error.code` (e.g. `"idempotency_key_reuse"` →
    don't retry, `"upstream_unreachable"` → retryable) instead of
    string-matching.

core/ASSET references
=====================
`upload_asset()` returns the proxy's `Asset` dict. The daemon uses
`asset["id"]` (a UUID) to build `core/ASSET` references in the graph:

    {"__type": "core/ASSET", "info": {"id": "<uuid>"}}

The proxy resolves those to filenames before ComfyUI sees the graph
(see `_rewrite_asset_refs` in the proxy's `app.py`). The daemon never
has to know the resolved filename; it just embeds the UUID.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional


# ---------------------------------------------------------------------------
#  Errors
# ---------------------------------------------------------------------------

class ProxyError(Exception):
    """Base for every error this client raises."""


class ProxyUnreachable(ProxyError):
    """The proxy did not answer at all (connection refused, timeout, DNS)."""


class ProxyHTTPError(ProxyError):
    """The proxy answered with a status >= 400.

    Carries the parsed error envelope when the body is the v2 shape:

        {"error": {"code": "...", "message": "...", "details": {...}}}

    `.status` is the HTTP status, `.code` the v2 error code (or None if
    the body was not the v2 envelope, e.g. a 502 from a misbehaving
    reverse proxy). `.details` is passed through verbatim.
    """

    def __init__(self, status: int, *, code: Optional[str],
                 message: str, details: Optional[dict] = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"{status} {code or 'error'}: {message}")


# ---------------------------------------------------------------------------
#  Client
# ---------------------------------------------------------------------------

class ProxyV2:
    """HTTP client for one comfy-api-proxy instance.

    Construction is cheap and idempotent — the client does no I/O until
    a method is called. The default (host=127.0.0.1, port=8189) matches
    what `comfy_proxy_start` spawns.
    """

    def __init__(self,
                 host: str = "127.0.0.1",
                 port: int = 8189,
                 *,
                 timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        # IPv6 literal in the base URL must be bracketed.
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        self.base_url = f"http://{netloc}"

    @classmethod
    def from_pid_file(cls) -> "ProxyV2":
        """Construct pointing at the currently running proxy.

        Reads the daemon's pid file (managed by comfy_proxy_start); raises
        ProxyUnreachable when no proxy is running. Use this in commands
        that want the current instance without the caller having to know
        host/port.
        """
        from ..proxy import manager
        state = manager.status().get("data") or {}
        if not state.get("running"):
            raise ProxyUnreachable("proxy is not running; run comfy_proxy_start")
        return cls(host=state["host"], port=int(state["port"]))

    # -- transport --------------------------------------------------------

    def _url(self, path: str) -> str:
        return self.base_url + "/" + path.lstrip("/")

    def _do_request(self,
                    method: str,
                    path: str,
                    *,
                    data: Optional[bytes] = None,
                    headers: Optional[dict[str, str]] = None,
                    timeout: Optional[float] = None
                    ) -> tuple[int, dict, bytes]:
        """One HTTP request. Returns (status, headers, body).

        HTTP error statuses do NOT raise here — the caller inspects
        `status` and converts to ProxyHTTPError with the parsed envelope.
        Only transport-level failures raise.
        """
        req = urllib.request.Request(self._url(path),
                                     data=data, method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(
                req, timeout=timeout if timeout is not None else self.timeout
            ) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, dict(e.headers or {}), body
        except urllib.error.URLError as e:
            raise ProxyUnreachable(
                f"{method} {self._url(path)}: {e.reason}"
            ) from e
        except (socket.timeout, TimeoutError) as e:
            raise ProxyUnreachable(
                f"{method} {self._url(path)}: timeout"
            ) from e
        except Exception as e:  # noqa: BLE001 — anything else is transport
            raise ProxyUnreachable(
                f"{method} {self._url(path)}: {e}"
            ) from e

    @staticmethod
    def _parse_error(status: int, body: bytes) -> ProxyHTTPError:
        """Parse the v2 error envelope from a non-2xx body."""
        try:
            parsed = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
            env = parsed["error"]
            return ProxyHTTPError(
                status,
                code=env.get("code"),
                message=env.get("message", ""),
                details=env.get("details"),
            )
        # Non-v2 body (e.g. a 502 page from a reverse proxy). Report status
        # plus a short snippet so the operator has *something*.
        snippet = body[:200].decode("utf-8", errors="replace")
        return ProxyHTTPError(status, code=None,
                              message=snippet or f"HTTP {status}")

    def _json(self, method: str, path: str,
              *, data: Optional[bytes] = None,
              headers: Optional[dict[str, str]] = None,
              timeout: Optional[float] = None) -> Any:
        status, _hdrs, body = self._do_request(
            method, path, data=data, headers=headers, timeout=timeout,
        )
        if status >= 400:
            raise self._parse_error(status, body)
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            snippet = body[:200].decode("utf-8", errors="replace")
            raise ProxyHTTPError(
                status, code=None,
                message=f"expected JSON, got {snippet!r} ({e})",
            ) from e

    # -- health -----------------------------------------------------------

    def health(self) -> dict:
        """GET /api/v2/health. Returns `{"status": "healthy", "upstream": ...}`.

        Proxy liveness only — does not probe ComfyUI. ComfyUI's reachability
        is `comfy_connect`'s concern (and the proxy's own /api/v2/* calls
        raise `upstream_unreachable` when ComfyUI is down).
        """
        return self._json("GET", "/api/v2/health", timeout=5.0)

    # -- assets -----------------------------------------------------------

    def upload_asset(self,
                     data: bytes,
                     file_path: str,
                     content_type: str,
                     *,
                     tags: Optional[list[str]] = None,
                     expected_hash: Optional[str] = None
                     ) -> dict:
        """POST /api/v2/assets. Returns the `Asset` dict.

        `file_path` is the proxy-relative path the daemon wants the asset
        associated with (e.g. ``"input/thecanvas.png"`` for an input
        upload, or ``"checkpoints/foo.safetensors"`` for a model-directory
        placement). The proxy classifies by top-level segment and routes
        accordingly; the filename shown to LoadImage is derived from this.

        `expected_hash` (``"blake3:<hex>"``) is optional. When supplied,
        the proxy verifies it against the server-computed hash; a mismatch
        rejects with 409 ``hash_mismatch``. When omitted, the proxy
        hashes the bytes itself (recommended unless you already have a
        blake3 library on the daemon side).

        Returns ``{"id": "<uuid>", "hash": "...", ...}``. The daemon uses
        ``.id`` to build ``core/ASSET`` references; nothing else here is
        needed for graph injection.
        """
        boundary = f"----kritomatic{uuid.uuid4().hex}"
        # A filename for the multipart part is required by the format but
        # ignored by the proxy (it uses the `file_path` field to derive the
        # real name). Use the basename for clarity in any proxy-side logs.
        part_filename = file_path.rsplit("/", 1)[-1] or "file.bin"

        parts: list[bytes] = []

        # -- file part --
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{part_filename}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        parts.append(data)
        parts.append(b"\r\n")

        # -- file_path field --
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            b'Content-Disposition: form-data; name="file_path"\r\n\r\n'
        )
        parts.append(file_path.encode())
        parts.append(b"\r\n")

        # -- content_type field (belt-and-braces; the file part also carries it) --
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            b'Content-Disposition: form-data; name="content_type"\r\n\r\n'
        )
        parts.append(content_type.encode())
        parts.append(b"\r\n")

        if expected_hash:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                b'Content-Disposition: form-data; name="expected_hash"\r\n\r\n'
            )
            parts.append(expected_hash.encode())
            parts.append(b"\r\n")

        for tag in (tags or []):
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                b'Content-Disposition: form-data; name="tags"\r\n\r\n'
            )
            parts.append(tag.encode())
            parts.append(b"\r\n")

        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)

        return self._json(
            "POST", "/api/v2/assets",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            timeout=max(self.timeout, 120.0),  # uploads can be large
        )

    def download_asset(self, asset_id: str,
                       *, timeout: Optional[float] = None) -> bytes:
        """GET /api/v2/assets/{id}/content. Returns raw bytes.

        Raises ``ProxyHTTPError(404, code="output_unavailable")`` when the
        asset is registered but its bytes are gone upstream — a definitive
        miss, not a transient one. Callers should not retry in that case.
        """
        status, _hdrs, body = self._do_request(
            "GET", f"/api/v2/assets/{asset_id}/content",
            timeout=timeout if timeout is not None else max(self.timeout, 120.0),
        )
        if status >= 400:
            raise self._parse_error(status, body)
        return body

    def get_asset(self, asset_id: str) -> dict:
        """GET /api/v2/assets/{id}. Returns the Asset metadata dict.

        Not required for generation — the daemon tracks what it uploaded.
        Useful for debugging and for post-restart rediscovery.
        """
        return self._json("GET", f"/api/v2/assets/{asset_id}")

    # -- jobs -------------------------------------------------------------

    def submit_job(self,
                   workflow: dict,
                   *,
                   extra_data: Optional[dict] = None,
                   idempotency_key: Optional[str] = None,
                   metadata: Optional[str] = None,
                   priority: Optional[int] = None,
                   timeout: Optional[float] = None) -> dict:
        """POST /api/v2/jobs. Returns the submitted `Job` dict.

        `workflow` is an API-format graph — the same shape ComfyUI's
        "Save (API Format)" produces. Any ``core/ASSET`` references in it
        are resolved by the proxy to the filenames ComfyUI expects; the
        daemon embeds those references when it uploads inputs (see
        ``upload_asset``).

        ``extra_data`` is the closed, typed field for partner-node auth.
        Only ``{"api_key_comfy_org": "<key>"}`` is accepted by the proxy
        (``400 invalid_request`` for anything else). The proxy forwards it
        to ComfyUI's `/prompt` verbatim and never stores it.

        ``idempotency_key`` — a client-generated UUID (recommended by the
        contract). Single-use: a reused key returns ``422
        idempotency_key_reuse``. Omit for retry-on-failure-with-fresh-attempt
        semantics; supply for retry-safe-without-double-submit.

        ``metadata`` (≤1 KiB UTF-8) and ``priority`` (advisory int,
        −1 000 000…1 000 000) are proxy-local extensions echoed on
        ``GET /jobs/{id}``. Never forwarded to ComfyUI.

        Raises ``ProxyHTTPError`` with ``.code``:
          * ``"invalid_workflow"`` — ComfyUI rejected the graph
            (``.details.node_errors`` carries per-node detail)
          * ``"missing_asset"`` — an unresolvable ``core/ASSET`` id
          * ``"idempotency_key_reuse"`` — the key was already used
          * ``"upstream_unreachable"`` (503) — ComfyUI could not be reached
          * ``"queue_full"`` (429, ``.details.Retry-After``)
        """
        body: dict[str, Any] = {"workflow": workflow}
        if extra_data:
            body["extra_data"] = extra_data
        if metadata is not None:
            body["metadata"] = metadata
        if priority is not None:
            body["priority"] = priority

        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        return self._json(
            "POST", "/api/v2/jobs",
            data=json.dumps(body).encode(),
            headers=headers,
            timeout=timeout if timeout is not None else self.timeout,
        )

    def get_job(self, job_id: str, *,
                timeout: Optional[float] = None) -> dict:
        """GET /api/v2/jobs/{id}. Returns the current `Job` dict.

        Authoritative — every field the daemon needs is here:
          * ``status`` — ``queued`` / ``running`` / ``succeeded`` /
            ``canceling`` / ``canceled`` / ``failed`` / ``expired``
          * ``outputs`` — array of `Output` entries, populated
            incrementally; each has ``node_id``, ``name``, ``type``
            (``"image"``/``"video"``/...), ``id`` (asset UUID), ``url``
          * ``error`` — ``{code, message, node_id, class_type, traceback}``
            when ``status == "failed"``
          * ``progress``, ``queue_position``, ``created_at``, ``expires_at``

        Poll this at 250–500 ms during the first few seconds for a
        responsive inpaint, then back off to 1 s.
        """
        return self._json("GET", f"/api/v2/jobs/{job_id}",
                          timeout=timeout if timeout is not None else self.timeout)

    def cancel_job(self, job_id: str, *,
                   timeout: Optional[float] = None) -> dict:
        """POST /api/v2/jobs/{id}/cancel. Returns the Job dict.

        Idempotent at the proxy. A still-running job reports
        ``status: "canceling"`` until the interrupt lands; a terminal job
        is returned unchanged. ``422`` is not expected for a valid id;
        ``503`` if ComfyUI became unreachable.
        """
        return self._json("POST", f"/api/v2/jobs/{job_id}/cancel",
                          timeout=timeout if timeout is not None else self.timeout)

    def list_jobs(self,
                  *,
                  status: Optional[list[str]] = None,
                  limit: Optional[int] = None,
                  timeout: Optional[float] = None) -> dict:
        """GET /api/v2/jobs. Returns ``{"jobs": [...], "truncated": bool}``.

        Proxy-local view — what *this* proxy instance recorded, resolved
        against ComfyUI. Not for primary use: the daemon's own batch
        records are the source of truth. Useful for reconciliation and
        post-restart discovery.
        """
        qs: list[str] = []
        if status:
            qs.append("status=" + ",".join(status))
        if limit is not None:
            qs.append(f"limit={limit}")
        path = "/api/v2/jobs"
        if qs:
            path += "?" + "&".join(qs)
        return self._json("GET", path,
                          timeout=timeout if timeout is not None else self.timeout)


# ---------------------------------------------------------------------------
#  Smoke test
# ---------------------------------------------------------------------------
#
# Run with:  python -m kritomatic_daemon.utils.comfyui_proxy_client
# Or from the daemon's venv:  python -c "from kritomatic_daemon.utils.comfyui_proxy_client import ProxyV2; print(ProxyV2().health())"

if __name__ == "__main__":
    import sys

    client = ProxyV2()
    try:
        result = client.health()
    except ProxyUnreachable as e:
        print(f"proxy unreachable: {e}", file=sys.stderr)
        print("Start it with: kritomatic comfyui comfy_proxy_start",
              file=sys.stderr)
        sys.exit(1)
    print(f"OK — proxy at {client.base_url} reports: {result}")