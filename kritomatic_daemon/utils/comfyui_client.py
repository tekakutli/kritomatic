"""comfyui_client.py — HTTP-only client for ComfyUI on 8188.

Used by `comfy_connect` (for `/object_info`) and `comfy_cancel` (for
`/interrupt`). The v2 generation pipeline talks to comfy-api-proxy on
8189 via `comfyui_proxy_client.ProxyV2`, not through this module.

The old WebSocket client — used by the tooling-nodes path for preview
frames and generation events — was removed in Stage 6. Anything that
needed it now uses `ProxyV2.submit_job` / `get_job` / `cancel_job`.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Optional


class ComfyError(Exception):
    """Any transport or ComfyUI-side error."""


# ============================================================================
#  HTTP plumbing
# ============================================================================

def _http_request(method: str, url: str, *,
                  data: Optional[bytes] = None,
                  headers: Optional[dict] = None,
                  timeout: float = 60.0):
    """Issue one HTTP request; return (status, headers, body_bytes).

    Raises ComfyError on network failure. Does NOT raise on HTTP error
    statuses — the caller inspects `status`.
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
    """HTTP-only client for a running ComfyUI server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8188,
                 connect_timeout: float = 10.0):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.connect_timeout = connect_timeout

        self._passthrough_headers = {
            "ngrok-skip-browser-warning": "69420",
            "skip_zrok_interstitial": "69420",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _json_request(self, method: str, path: str,
                      body: Optional[dict] = None,
                      timeout: float = 60.0):
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

    # ------------------------------------------------------------------
    #  Public API
    # ------------------------------------------------------------------

    def object_info(self) -> dict:
        """GET /object_info."""
        return self._json_request("GET", "object_info", timeout=30.0)

    def interrupt(self) -> None:
        """POST /interrupt. Best-effort."""
        try:
            self._json_request("POST", "interrupt", body={}, timeout=10.0)
        except ComfyError:
            pass


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
            _shared_client = ComfyClient(host, port)
        return _shared_client


def reset_client() -> None:
    """Drop the shared client. Next get_client() rebuilds."""
    global _shared_client
    with _shared_lock:
        _shared_client = None