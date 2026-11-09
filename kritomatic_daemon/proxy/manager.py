"""Supervisor for the comfy-api-proxy child process.

The proxy is a separate aiohttp service that puts the Comfy API v2 in
front of ComfyUI. The daemon never imports it; it spawns it in a
dedicated venv and talks to it over HTTP on 127.0.0.1:8189.

Why a dedicated venv
====================
The proxy depends on aiohttp, pydantic, blake3, and PyYAML — heavyweight
packages orthogonal to Krita's runtime. Krita's bundled Python is
fragile; installing them there risks breaking Krita on a version
conflict. A venv also isolates the proxy's upgrade cycle from the
daemon's, and lets `install()` pick a Python 3.10+ from PATH rather
than whatever Krita ships (which is often older).

Layout
======
    ~/.local/share/kritomatic/comfy-api-proxy/       # or %LOCALAPPDATA% on Windows
    ├── venv/                # dedicated Python env
    ├── pid.json             # {pid, host, port, comfyui}
    └── proxy.log            # stdout + stderr of the child

Lifecycle
=========
    install(force)        one-time venv creation + pip install (slow, ~30s)
    install_async(force)  kick off install() on a background thread and
                          return immediately; status() reports progress
    start(...)            spawn the child detached (survives daemon restart),
                          wait for /api/v2/health, write pid.json
    stop()                SIGTERM, wait, SIGKILL if needed, clear pid.json
    status()              read pid.json, verify PID is ours, health-probe,
                          report install progress; always returns the
                          {success, message, data} envelope
    logs(n)               tail proxy.log
    uninstall()           stop + remove venv

The child runs with start_new_session=True (POSIX) or
DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP (Windows) so it is not killed
when the daemon's parent thread exits. On daemon restart, status()
re-discovers a still-running proxy via pid.json.

Async install
=============
`install()` takes ~30s (venv + pip). The CLI's command transport has a
~2s read timeout. Blocking the call would cause the client to time out
on a call that actually succeeds server-side, leaving the operator with
no visibility into whether it worked. `install_async()` runs the same
work on a background thread, returns immediately, and lets the caller
poll `status()` for the real outcome. State is module-level: a second
`install_async()` while one is running returns "installing" without
spawning a duplicate thread.

Zero external dependencies: only stdlib (`urllib.request`, `socket`,
`subprocess`, `signal`, `threading`). The manager is importable outside
Krita.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
#  Install state (module-level)
# ---------------------------------------------------------------------------
#
# `comfy_proxy_install` runs the slow venv+pip dance on a background
# thread (see `install_async` below); `status()` reports this so the CLI
# can poll it without ever blocking for 30s on a 2s transport read
# timeout.

_INSTALL_LOCK = threading.Lock()
_INSTALL_STATE: dict[str, Any] = {
    "state": "idle",       # idle | installing | done | error
    "message": "",
    "data": None,
}


# ---------------------------------------------------------------------------
#  Paths
# ---------------------------------------------------------------------------

_PROXY_PKG_DIR = Path(__file__).resolve().parent
_PINNED_VERSION_FILE = _PROXY_PKG_DIR / "PINNED_VERSION"


def _data_root() -> Path:
    """Per-user data directory for the proxy venv + state.

    Deliberately NOT Qt's QStandardPaths (which resolves to Krita's own
    data dir when imported inside Krita). We want an independent dir
    that any Python on the machine can find, so manager.py stays
    importable outside Krita for testing.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA")
                    or (Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME")
                    or (Path.home() / ".local" / "share"))
    return base / "kritomatic" / "comfy-api-proxy"


def _venv_dir() -> Path:
    return _data_root() / "venv"


def _venv_python() -> Path:
    if os.name == "nt":
        return _venv_dir() / "Scripts" / "python.exe"
    return _venv_dir() / "bin" / "python"


def _venv_pip() -> Path:
    if os.name == "nt":
        return _venv_dir() / "Scripts" / "pip.exe"
    return _venv_dir() / "bin" / "pip"


def _proxy_bin() -> Path:
    if os.name == "nt":
        return _venv_dir() / "Scripts" / "comfy-api-proxy.exe"
    return _venv_dir() / "bin" / "comfy-api-proxy"


def _pid_file() -> Path:
    return _data_root() / "pid.json"


def _log_file() -> Path:
    return _data_root() / "proxy.log"


def pinned_version() -> str:
    try:
        return _PINNED_VERSION_FILE.read_text().strip()
    except OSError:
        return "0.1.0"


# ---------------------------------------------------------------------------
#  Health probe
# ---------------------------------------------------------------------------

def _health_url(host: str, port: int) -> str:
    if ":" in host:  # IPv6 literal
        return f"http://[{host}]:{port}/api/v2/health"
    return f"http://{host}:{port}/api/v2/health"


def _health_ok(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(_health_url(host, port),
                                    timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
        return False


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_health(host: str, port: int, proc: subprocess.Popen,
                 timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False  # child exited before binding
        if _health_ok(host, port, timeout=1.0):
            return True
        time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
#  PID identity
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False


def _pid_cmdline(pid: int) -> str | None:
    """Best-effort command line of a PID, or None where it can't be read."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}",
                 "get", "CommandLine"],
                capture_output=True, text=True, timeout=5,
            )
        else:
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True, text=True, timeout=5,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _pid_is_ours(pid: int) -> bool:
    """Is `pid` alive AND (best-effort) actually our proxy — not a recycled PID?

    Falls back to a plain liveness check when the command line can't be
    inspected, so behaviour is never worse than tracking the PID alone.
    """
    if not _pid_alive(pid):
        return False
    cmdline = _pid_cmdline(pid)
    if cmdline is None:
        return True
    return "comfy-api-proxy" in cmdline


def _terminate(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=10)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(50):
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------------------
#  Python discovery for install()
# ---------------------------------------------------------------------------

def _find_python_310() -> str | None:
    """Find a Python 3.10+ interpreter to build the venv from.

    Krita's bundled Python is frequently 3.8 or 3.9 — the proxy requires
    3.10+. Prefer a system Python if one is on PATH; fall back to
    sys.executable (Krita's) only if no newer interpreter is found,
    because a 3.9 venv cannot install the proxy.
    """
    for name in ("python3.12", "python3.11", "python3.10", "python3"):
        path = shutil.which(name)
        if not path:
            continue
        try:
            r = subprocess.run(
                [path, "-c",
                 "import sys; print(sys.version_info >= (3, 10))"],
                capture_output=True, text=True, timeout=5,
            )
            if r.stdout.strip() == "True":
                return path
        except (OSError, subprocess.SubprocessError):
            continue
    return None


# ---------------------------------------------------------------------------
#  install
# ---------------------------------------------------------------------------

def install(force: bool = False) -> dict[str, Any]:
    """Create the venv and pip-install the pinned proxy version.

    Idempotent: returns `already installed` when the binary exists and
    `force` is False. Slow on first run (~30s for venv + pip).

    Prefer `install_async()` from a command handler: this synchronous
    form blocks for the full ~30s and will time out the CLI's transport.
    """
    if _proxy_bin().exists() and not force:
        return {
            "success": True,
            "message": f"already installed at {_proxy_bin()}",
            "data": {"path": str(_proxy_bin()),
                     "version": pinned_version()},
        }

    python = _find_python_310()
    if python is None:
        return {
            "success": False,
            "message": (
                "No Python 3.10+ found on PATH. The comfy-api-proxy "
                "requires Python 3.10 or newer. Install one "
                "(e.g. `sudo apt install python3.11`) and retry, or "
                "install the proxy manually: "
                "`pipx install comfy-api-proxy==" + pinned_version() + "`, "
                "then run `comfy_proxy_start` — the manager will find "
                "the pipx-installed binary on PATH."
            ),
        }

    _data_root().mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [python, "-m", "venv", str(_venv_dir())],
            check=True, timeout=180,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        return {"success": False,
                "message": f"venv creation failed: {e.stderr.decode(errors='replace')[:300]}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "venv creation timed out (180s)"}
    except OSError as e:
        return {"success": False, "message": f"venv creation failed: {e}"}

    version = pinned_version()
    try:
        subprocess.run(
            [str(_venv_pip()), "install", f"comfy-api-proxy=={version}"],
            check=True, timeout=600,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        return {"success": False,
                "message": f"pip install failed: {e.stderr.decode(errors='replace')[:500]}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "pip install timed out (600s)"}
    except OSError as e:
        return {"success": False, "message": f"pip install failed: {e}"}

    if not _proxy_bin().exists():
        return {"success": False,
                "message": f"pip finished but {_proxy_bin()} is missing"}

    return {
        "success": True,
        "message": f"installed comfy-api-proxy {version} at {_proxy_bin()}",
        "data": {"path": str(_proxy_bin()), "version": version},
    }


def install_async(force: bool = False) -> dict[str, Any]:
    """Kick off `install()` on a background thread and return immediately.

    The CLI's command transport has a short read timeout (~2s), but
    venv creation + pip install take ~30s. Rather than blocking the
    call (and having the client time out on a call that actually
    succeeds server-side), start the install in a thread, return
    `state: "installing"`, and let the caller poll `status()` for the
    real result.

    Idempotent: a call while an install is already running returns
    `state: "installing"` without spawning a second thread. A call
    after a successful install returns `state: "done"` with the
    cached result. `force=True` re-runs even after `state: "done"`.
    """
    with _INSTALL_LOCK:
        current = _INSTALL_STATE["state"]
        if current == "installing":
            return {
                "success": True,
                "message": "install already in progress",
                "data": {"state": "installing"},
            }
        if current == "done" and not force:
            return {
                "success": True,
                "message": "already installed",
                "data": dict(_INSTALL_STATE["data"] or {}),
            }
        _INSTALL_STATE["state"] = "installing"
        _INSTALL_STATE["message"] = "creating venv and pip-installing..."
        _INSTALL_STATE["data"] = None

    def _run() -> None:
        try:
            result = install(force=force)
            with _INSTALL_LOCK:
                _INSTALL_STATE["state"] = (
                    "done" if result.get("success") else "error"
                )
                _INSTALL_STATE["message"] = result.get("message", "")
                _INSTALL_STATE["data"] = result.get("data")
        except Exception as e:  # noqa: BLE001 — surface anything as install error
            with _INSTALL_LOCK:
                _INSTALL_STATE["state"] = "error"
                _INSTALL_STATE["message"] = f"install raised: {e}"
                _INSTALL_STATE["data"] = None

    threading.Thread(
        target=_run, name="comfy-proxy-install", daemon=True,
    ).start()

    return {
        "success": True,
        "message": "install started in background; poll comfy_proxy_status",
        "data": {"state": "installing"},
    }


# ---------------------------------------------------------------------------
#  start / stop / status / logs / uninstall
# ---------------------------------------------------------------------------

def _read_pid_file() -> dict[str, Any] | None:
    path = _pid_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict) or int(data.get("pid", -1)) <= 0:
        return None
    return data


def status() -> dict[str, Any]:
    """Report whether the proxy is running and healthy, plus install state.

    Returns the standard `{success, message, data}` envelope so the
    daemon's base handler wraps it correctly (a bare dict without
    `success`/`message`/`data` would be reported as
    `{"status": "error", "data": null}` by the caller).

    The `data` block always carries an `install` snapshot plus the
    runtime fields — a caller can distinguish "still installing" from
    "installed but not running" from "running and healthy" without
    extra state.

    `running` = PID file present AND PID is ours. `healthy` = the
    proxy answers /api/v2/health. Both must be True for the daemon to
    route generation through the proxy.
    """
    with _INSTALL_LOCK:
        raw_data = _INSTALL_STATE["data"]
        install_snapshot = {
            "state": _INSTALL_STATE["state"],
            "message": _INSTALL_STATE["message"],
            # Copy the dict so a caller can't mutate module state.
            "data": dict(raw_data) if isinstance(raw_data, dict) else raw_data,
        }

    state = _read_pid_file()
    if state is None:
        return {
            "success": True,
            "message": "not running",
            "data": {
                "install": install_snapshot,
                "running": False,
                "healthy": False,
            },
        }
    pid = int(state["pid"])
    if not _pid_is_ours(pid):
        # Stale PID file: process is gone or the PID has been recycled.
        # Clear it so the next status() doesn't report a phantom.
        try:
            _pid_file().unlink()
        except OSError:
            pass
        return {
            "success": True,
            "message": f"not running (cleared stale pid {pid})",
            "data": {
                "install": install_snapshot,
                "running": False,
                "healthy": False,
                "cleared_stale_pid": pid,
            },
        }
    host = state.get("host", "127.0.0.1")
    port = int(state.get("port", 8189))
    healthy = _health_ok(host, port, timeout=0.5)
    return {
        "success": True,
        "message": "running" if healthy else "running (unhealthy)",
        "data": {
            "install": install_snapshot,
            "running": True,
            "healthy": healthy,
            "pid": pid,
            "host": host,
            "port": port,
            "comfyui": state.get("comfyui"),
        },
    }


def start(*,
          host: str = "127.0.0.1",
          port: int = 8189,
          comfyui: str = "http://127.0.0.1:8188",
          comfyui_base_dir: str | None = None,
          token: str | None = None) -> dict[str, Any]:
    """Spawn the proxy detached and wait for /api/v2/health.

    Idempotent when an existing proxy already points at the same
    ComfyUI. Errors when one points elsewhere (stop it first), or when
    the port is already taken by another process.
    """
    existing = status()
    existing_data = existing.get("data") or {}
    if existing_data.get("running"):
        existing_comfyui = existing_data.get("comfyui")
        if existing_comfyui and existing_comfyui != comfyui:
            return {
                "success": False,
                "message": (
                    f"proxy already running (pid {existing_data['pid']}) "
                    f"pointed at {existing_comfyui}, not {comfyui}. "
                    f"Run `comfy_proxy_stop` first."
                ),
                "data": existing_data,
            }
        return {
            "success": True,
            "message": f"already running (pid {existing_data['pid']})",
            "data": existing_data,
        }

    binary: str | None = None
    if _proxy_bin().exists():
        binary = str(_proxy_bin())
    else:
        on_path = shutil.which("comfy-api-proxy")
        if on_path:
            binary = on_path
    if binary is None:
        return {
            "success": False,
            "message": (
                "comfy-api-proxy is not installed. Run "
                "`comfy_proxy_install` first, or install it manually "
                "with `pipx install comfy-api-proxy`."
            ),
        }

    if _port_open(host, port):
        return {
            "success": False,
            "message": f"port {port} on {host} is already in use",
        }

    argv = [binary, "run", "--comfyui", comfyui,
            "--host", host, "--port", str(port)]
    if comfyui_base_dir:
        argv += ["--comfyui-base-dir", comfyui_base_dir]
    if token:
        argv += ["--token", token]

    _data_root().mkdir(parents=True, exist_ok=True)
    _log_file().touch(exist_ok=True)
    log_handle = _log_file().open("ab")
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": log_handle,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(argv, **popen_kwargs)
    except OSError as e:
        log_handle.close()
        return {"success": False, "message": f"failed to spawn proxy: {e}"}
    finally:
        log_handle.close()

    if not _wait_health(host, port, proc, timeout=15.0):
        _terminate(proc.pid)
        return {
            "success": False,
            "message": f"proxy failed to become healthy; see {_log_file()}",
            "data": {"pid": proc.pid, "log": str(_log_file())},
        }

    _pid_file().write_text(json.dumps({
        "pid": proc.pid,
        "host": host,
        "port": port,
        "comfyui": comfyui,
    }))

    return {
        "success": True,
        "message": f"started on {host}:{port} (pid {proc.pid})",
        "data": {
            "pid": proc.pid,
            "host": host,
            "port": port,
            "comfyui": comfyui,
        },
    }


def stop() -> dict[str, Any]:
    """Stop the proxy and clear the PID file. Idempotent."""
    state = _read_pid_file()
    if state is None:
        return {"success": True, "message": "not running",
                "data": {"running": False}}
    pid = int(state["pid"])
    if not _pid_is_ours(pid):
        try:
            _pid_file().unlink()
        except OSError:
            pass
        return {"success": True,
                "message": f"pid {pid} is gone or recycled; cleared",
                "data": {"cleared_stale_pid": pid}}
    _terminate(pid)
    try:
        _pid_file().unlink()
    except OSError:
        pass
    return {"success": True, "message": f"stopped (pid {pid})",
            "data": {"pid": pid}}


def logs(lines: int = 100) -> dict[str, Any]:
    """Return the last `lines` lines of the proxy log."""
    path = _log_file()
    if not path.exists():
        return {"success": True, "message": "no log yet",
                "data": {"lines": []}}
    try:
        with path.open("r", errors="replace") as f:
            all_lines = f.readlines()
    except OSError as e:
        return {"success": False, "message": f"could not read log: {e}",
                "data": {"lines": []}}
    take = max(1, min(lines, 5000))
    tail = all_lines[-take:]
    return {
        "success": True,
        "message": f"{len(tail)} line(s) of {len(all_lines)}",
        "data": {"lines": [ln.rstrip("\n") for ln in tail]},
    }


def uninstall() -> dict[str, Any]:
    """Stop the proxy (if running) and remove its venv."""
    stop()
    with _INSTALL_LOCK:
        _INSTALL_STATE["state"] = "idle"
        _INSTALL_STATE["message"] = ""
        _INSTALL_STATE["data"] = None
    if _venv_dir().exists():
        try:
            shutil.rmtree(_venv_dir())
        except OSError as e:
            return {"success": False,
                    "message": f"could not remove venv: {e}",
                    "data": {"running": False}}
    return {"success": True, "message": "uninstalled",
            "data": {"running": False}}