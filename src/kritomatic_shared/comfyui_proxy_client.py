"""
comfyui_proxy_client.py — HTTP client for the Comfy API v2, served by
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
    ComfyClient still talks to ComfyUI on 8188 for that.
  * GET /api/v2/jobs/{id}/events — SSE. The v2 contract makes polling
    authoritative and SSE a live enhancement.
  * Anything that touches /api/etn/* — that is the tooling-nodes
    transport this migration removes.

Design
======
  * Pure stdlib: `urllib.request`, `socket`, `uuid`, `json`. No
    websockets, no aiohttp, no requests.
  * No connection pooling. `urllib.request.urlopen()` per call.
  * Thread-safe by construction. N threads can share one instance.
  * Structured errors. `ProxyHTTPError` carries the parsed error
    envelope (`code`, `message`, `details`, `status`).

Shared between the daemon and the browser playgrounds
=====================================================
This module used to exist twice: once as `kritomatic_daemon/utils/
comfyui_proxy_client.py` (the daemon's copy, whose `from_pid_file()`
imported `..proxy.manager` to do a health check) and once as
`playgrounds/pg_comfy/proxy_client.py` (the playground's copy, whose
`from_pid_file()` read the pid file directly to avoid the daemon
import).  The two copies were functionally identical except for
`from_pid_file()`, and the daemon's copy couldn't be used by the
playground and vice versa.

It is now one module.  `from_pid_file()` reads the pid file the
daemon's `proxy/manager.py` writes on `comfy_proxy_start`, then probes
`/api/v2/health` before returning.  That reproduces the daemon's
defensive check (the caller gets a clean `ProxyUnreachable` if the
proxy is dead) without the playground needing to import
`kritomatic_daemon`.  The playground inherits the same fail-fast
semantics at the cost of one health round trip at construction time.

core/ASSET references
=====================
`upload_asset()` returns the proxy's `Asset` dict. The daemon uses
`asset["id"]` (a UUID) to build `core/ASSET` references in the graph:

    {"__type": "core/ASSET", "info": {"id": "<uuid>"}}

The proxy resolves those to filenames before ComfyUI sees the graph.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
import uuid
from pathlib import Path
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
#  Pid-file location — shared with the daemon's proxy/manager.py
# ---------------------------------------------------------------------------
#
# The daemon's `proxy/manager.py::_data_root()` computes the same
# directory. The duplication is deliberate: `manager.py` needs the
# data root for its venv + log + pid files (and uses it in code paths
# that must stay importable outside Krita), while this module only
# needs the pid file. Keeping the two in sync is a one-line concern;
# making `manager.py` import from this module would require it to
# carry the same sys.path bootstrap every module that touches
# `kritomatic_shared` needs, and `manager.py` is not otherwise
# coupled to shared.

def _pid_file_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA")
                    or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME")
                    or (Path.home() / ".local" / "share"))
    return base / "kritomatic" / "comfy-api-proxy" / "pid.json"


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

        Reads the pid file the daemon's `proxy/manager.py` writes on
        `comfy_proxy_start`, then probes `/api/v2/health` to confirm
        the proxy is actually answering.  A missing pid file, an
        unreadable one, or a failed health probe all raise
        `ProxyUnreachable` — the caller can treat this as "the proxy
        is not usable right now", which is the same failure mode the
        daemon's old `manager.status()` path produced.

        Before this class was shared, the daemon called
        `manager.status()` here (which did the health probe) and the
        playground just read the pid file.  Unifying on the probe
        gives the playground the same fail-fast semantics at the cost
        of one HTTP round trip at construction time.
        """
        pid_file = _pid_file_path()
        if not pid_file.is_file():
            raise ProxyUnreachable(
                "proxy pid file not found; run comfy_proxy_start first"
            )
        try:
            data = json.loads(pid_file.read_text())
        except (OSError, ValueError) as e:
            raise ProxyUnreachable(
                f"could not read proxy pid file: {e}"
            ) from e

        host = data.get("host", "127.0.0.1")
        port = int(data.get("port", 8189))
        client = cls(host=host, port=port)

        try:
            client.health(timeout=0.5)
        except (ProxyUnreachable, ProxyHTTPError) as e:
            raise ProxyUnreachable(
                f"proxy pid file present but proxy unreachable "
                f"at {host}:{port}: {e}"
            ) from e

        return client

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

    def health(self, *, timeout: float = 5.0) -> dict:
        """GET /api/v2/health. Returns `{"status": "healthy", ...}`.

        Proxy liveness only — does not probe ComfyUI. ComfyUI's
        reachability is `comfy_connect`'s concern.

        `timeout` defaults to 5 s for interactive diagnostics; pass a
        smaller value (e.g. 0.5 s) for cheap liveness probes.
        """
        return self._json("GET", "/api/v2/health", timeout=timeout)

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

        ``expected_hash`` (``"blake3:<hex>"``) is optional. When supplied,
        the proxy verifies it against the server-computed hash; a mismatch
        rejects with 409 ``hash_mismatch``. When omitted, the proxy
        hashes the bytes itself.

        Returns ``{"id": "<uuid>", "hash": "...", ...}``. The daemon uses
        ``.id`` to build ``core/ASSET`` references.
        """
        boundary = f"----kritomatic{uuid.uuid4().hex}"
        part_filename = file_path.rsplit("/", 1)[-1] or "file.bin"

        parts: list[bytes] = []

        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{part_filename}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        parts.append(data)
        parts.append(b"\r\n")

        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            b'Content-Disposition: form-data; name="file_path"\r\n\r\n'
        )
        parts.append(file_path.encode())
        parts.append(b"\r\n")

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
            timeout=max(self.timeout, 120.0),
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
        """GET /api/v2/assets/{id}. Returns the Asset metadata dict."""
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
        """POST /api/v2/jobs. Returns the submitted `Job` dict."""
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
        """GET /api/v2/jobs/{id}. Returns the current `Job` dict."""
        return self._json("GET", f"/api/v2/jobs/{job_id}",
                          timeout=timeout if timeout is not None else self.timeout)

    def cancel_job(self, job_id: str, *,
                   timeout: Optional[float] = None) -> dict:
        """POST /api/v2/jobs/{id}/cancel. Returns the Job dict."""
        return self._json("POST", f"/api/v2/jobs/{job_id}/cancel",
                          timeout=timeout if timeout is not None else self.timeout)

    def list_jobs(self,
                  *,
                  status: Optional[list[str]] = None,
                  limit: Optional[int] = None,
                  timeout: Optional[float] = None) -> dict:
        """GET /api/v2/jobs. Returns ``{"jobs": [...], "truncated": bool}``."""
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
