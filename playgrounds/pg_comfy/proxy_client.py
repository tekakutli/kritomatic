"""ProxyV2 client, copied from kritomatic_daemon.utils.comfyui_proxy_client.

The playground runs standalone (only `playgrounds/` on sys.path) and
therefore cannot import `kritomatic_daemon` — that package imports
Krita at import time. This copy is the same pattern the playground
already uses for `pg_comfy/tooling.py`.

Keep this file in sync with the daemon's copy. The only consumer is
`inpaint_server.handle_inpaint`, which uses `upload_asset`,
`submit_job`, `get_job`, and `download_asset`.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional


class ProxyError(Exception):
    """Base for every error this client raises."""


class ProxyUnreachable(ProxyError):
    """The proxy did not answer at all (connection refused, timeout, DNS)."""


class ProxyHTTPError(ProxyError):
    """The proxy answered with a status >= 400.

    Carries the parsed error envelope when the body is the v2 shape.
    `.status` is the HTTP status, `.code` the v2 error code, `.details`
    passed through verbatim.
    """

    def __init__(self, status: int, *, code: Optional[str],
                 message: str, details: Optional[dict] = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        super().__init__(f"{status} {code or 'error'}: {message}")


class ProxyV2:
    """HTTP client for one comfy-api-proxy instance."""

    def __init__(self,
                 host: str = "127.0.0.1",
                 port: int = 8189,
                 *,
                 timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        self.base_url = f"http://{netloc}"

    @classmethod
    def from_pid_file(cls) -> "ProxyV2":
        """Construct pointing at the currently running proxy.

        Reads the daemon's pid file. Since the playground runs outside
        the daemon process, this reads the same file on disk — the
        daemon and the playground share the same `~/.local/share/
        kritomatic/comfy-api-proxy/pid.json`.
        """
        from pathlib import Path
        import os
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA")
                        or (Path.home() / "AppData" / "Local"))
        else:
            base = Path(os.environ.get("XDG_DATA_HOME")
                        or (Path.home() / ".local" / "share"))
        pid_file = base / "kritomatic" / "comfy-api-proxy" / "pid.json"
        if not pid_file.is_file():
            raise ProxyUnreachable(
                "proxy pid file not found; run comfy_proxy_start first"
            )
        try:
            data = json.loads(pid_file.read_text())
        except (OSError, ValueError) as e:
            raise ProxyUnreachable(f"could not read proxy pid file: {e}") from e
        return cls(host=data.get("host", "127.0.0.1"),
                   port=int(data.get("port", 8189)))

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
        except Exception as e:  # noqa: BLE001
            raise ProxyUnreachable(
                f"{method} {self._url(path)}: {e}"
            ) from e

    @staticmethod
    def _parse_error(status: int, body: bytes) -> ProxyHTTPError:
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

    def health(self) -> dict:
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
        status, _hdrs, body = self._do_request(
            "GET", f"/api/v2/assets/{asset_id}/content",
            timeout=timeout if timeout is not None else max(self.timeout, 120.0),
        )
        if status >= 400:
            raise self._parse_error(status, body)
        return body

    # -- jobs -------------------------------------------------------------

    def submit_job(self, workflow: dict, *,
                   idempotency_key: Optional[str] = None,
                   timeout: Optional[float] = None) -> dict:
        body = {"workflow": workflow}
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
        return self._json("GET", f"/api/v2/jobs/{job_id}",
                          timeout=timeout if timeout is not None else self.timeout)

    def cancel_job(self, job_id: str, *,
                   timeout: Optional[float] = None) -> dict:
        return self._json("POST", f"/api/v2/jobs/{job_id}/cancel",
                          timeout=timeout if timeout is not None else self.timeout)
