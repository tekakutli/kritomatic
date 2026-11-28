# playgrounds/kritomatic_nodes/kritomatic_workflows.py
"""HTTP route for saving workflows into the kritomatic repo.

Registers `POST /kritomatic/save_workflow` on ComfyUI's PromptServer.
Body: `{ "filename": "name.json", "workflow": {...} }`.

The workflow is the UI-format graph (exactly what ComfyUI's "Export"
produces, i.e. `app.graph.serialize()` in the frontend). It is written
as pretty-printed JSON to:

    <kritomatic repo root>/data/comfy_panel/workflows/<filename>

Repo root is resolved from this file's real path. Because `install.py`
symlinks `<repo>/playgrounds/kritomatic_nodes` into ComfyUI's
`custom_nodes/`, `Path(__file__).resolve()` follows the symlink back to
`<repo>/playgrounds/kritomatic_nodes/`, and `.parents[2]` is `<repo>`.
`install.py` enforces this layout; no other assumptions are made.

Daemon notification
===================
After a successful write, this module fires a **best-effort** command
at the kritomatic daemon's command socket (`127.0.0.1:12346`):

    {"type": "comfy_reload_if_active", "filename": "<name>.json"}

The daemon responds with a single line.  If the saved file matches the
currently active workflow, the daemon re-reads it, re-extracts slots,
and republishes the `state` / `panel` events — so the docker (or web
panel) sees the new graph structure within ~100 ms of the save, without
needing a manual re-Load.

The notification runs on a background thread so the ComfyUI event loop
is never blocked by a slow or absent daemon.  Failures (daemon not
running, connection refused, timeout, malformed reply) are logged to
the ComfyUI console and otherwise ignored: the save itself succeeded,
and the notification is a convenience, not part of the save's
contract.  If the daemon is down, its next `comfy_status` poll still
shows the old workflow — the user can Load manually from the docker's
workflow picker, exactly as before this fix.

Registration is a side effect of import, matching how `Magi/nodes.py`
registers `POST /magi/crop_embed`.
"""

from __future__ import annotations

import json
import re
import socket
import threading
from pathlib import Path


# ---------------------------------------------------------------------------
# Path resolution
#
# __file__:      <repo>/playgrounds/kritomatic_nodes/kritomatic_workflows.py
# .parents[0]:   <repo>/playgrounds/kritomatic_nodes
# .parents[1]:   <repo>/playgrounds
# .parents[2]:   <repo>                            ← repo root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = _REPO_ROOT / "data" / "comfy_panel" / "workflows"

# Filenames are restricted to a conservative character class. The
# caller supplies a plain name; we append `.json` if missing. Path
# separators and leading dots are rejected outright — the file must
# land directly in `_WORKFLOWS_DIR` with no traversal.
_FILENAME_RE = re.compile(r"^[A-Za-z0-9 _\-.]+$")

# Daemon command socket.  Mirrors the constant in
# `kritomatic_daemon/panel/connection.py` (COMMAND_HOST / COMMAND_PORT).
# Duplicated here so this module has no runtime dependency on the
# kritomatic_daemon package (which may not be importable from ComfyUI's
# Python), and so the value can be inspected locally without chasing
# an import.
_DAEMON_HOST = "127.0.0.1"
_DAEMON_PORT = 12346
_DAEMON_TIMEOUT_S = 2.0

_ROUTE_REGISTERED = False


def _json(data, status: int = 200):
    from aiohttp import web
    return web.json_response(data, status=status)


def _sanitize_filename(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise ValueError("filename is empty")
    if not name.endswith(".json"):
        name = name + ".json"
    if name.startswith("."):
        raise ValueError("filename may not start with a dot")
    if "/" in name or "\\" in name:
        raise ValueError("filename may not contain path separators")
    if not _FILENAME_RE.match(name):
        raise ValueError(
            "filename may only contain letters, digits, spaces, "
            "underscores, hyphens, and dots"
        )
    return name


# ---------------------------------------------------------------------------
#  Daemon notification (fire-and-forget)
# ---------------------------------------------------------------------------

def _notify_daemon_reload(filename: str) -> None:
    """Best-effort notify the daemon that <filename> was just written.

    Runs on its own thread (started by `_spawn_daemon_notify` below),
    so this blocking work never touches ComfyUI's event loop.

    Speaks the same wire protocol as
    `kritomatic_daemon/panel/connection.py::_fetch_one`: bare JSON
    command + newline, read one newline-terminated reply line.  The
    daemon's dispatcher (`handlers/base.py::CommandHandler.handle_command`)
    returns `{"status": "success"|"error", "command": ...,
    "message": ..., "data": ...}` for bare commands.

    Never raises.  Any failure is logged to the ComfyUI console — the
    save already succeeded, so the failure mode of a broken daemon is
    "the docker doesn't update automatically, use the docker's Load
    button", which is exactly the behaviour before this fix.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_DAEMON_TIMEOUT_S)
        sock.connect((_DAEMON_HOST, _DAEMON_PORT))
        command = {
            "type": "comfy_reload_if_active",
            "filename": filename,
            "preserve_config": True,
        }
        sock.sendall(
            (json.dumps(command) + "\n").encode("utf-8")
        )
        buffer = b""
        while b"\n" not in buffer:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buffer += chunk
        if b"\n" not in buffer:
            print(
                f"[kritomatic:workflows] daemon reload: no reply line "
                f"for {filename!r}",
                flush=True,
            )
            return
        line = buffer.split(b"\n", 1)[0]
        try:
            reply = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            print(
                f"[kritomatic:workflows] daemon reload: bad reply "
                f"for {filename!r}: {e}",
                flush=True,
            )
            return
        ok = (isinstance(reply, dict)
              and reply.get("status") == "success")
        detail = (reply.get("message", "")
                  if isinstance(reply, dict) else "")
        if ok:
            print(
                f"[kritomatic:workflows] daemon reload ok: "
                f"{detail or filename}",
                flush=True,
            )
        else:
            print(
                f"[kritomatic:workflows] daemon reload failed for "
                f"{filename!r}: {detail or 'unknown error'}",
                flush=True,
            )
    except socket.timeout:
        print(
            f"[kritomatic:workflows] daemon reload skipped: "
            f"timeout reaching {_DAEMON_HOST}:{_DAEMON_PORT} "
            f"for {filename!r}",
            flush=True,
        )
    except OSError as e:
        # Connection refused, host unreachable, permission denied —
        # the daemon is not running, which is a normal state (user
        # hasn't opened Krita, or has closed it).
        print(
            f"[kritomatic:workflows] daemon reload skipped: "
            f"{e} for {filename!r}",
            flush=True,
        )
    except Exception as e:  # noqa: BLE001 — this must never escape
        print(
            f"[kritomatic:workflows] daemon reload error for "
            f"{filename!r}: {e}",
            flush=True,
        )
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _spawn_daemon_notify(filename: str) -> None:
    """Start `_notify_daemon_reload` on a background thread.

    Kept separate from `_notify_daemon_reload` so the caller (the
    aiohttp handler) does not need to know whether the notify is
    synchronous or asynchronous — it just calls this and returns.
    """
    threading.Thread(
        target=_notify_daemon_reload,
        args=(filename,),
        name="kritomatic-daemon-notify",
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
#  Route registration
# ---------------------------------------------------------------------------

def _register_route() -> None:
    global _ROUTE_REGISTERED
    if _ROUTE_REGISTERED:
        return

    try:
        from server import PromptServer
    except ImportError:
        return

    if PromptServer.instance is None:
        return

    @PromptServer.instance.routes.post("/kritomatic/save_workflow")
    async def save_workflow(request):
        try:
            body = await request.json()
        except Exception as e:
            return _json(
                {"success": False, "message": f"bad JSON body: {e}"},
                status=400,
            )

        filename_raw = body.get("filename")
        workflow = body.get("workflow")

        if not isinstance(filename_raw, str) or not filename_raw.strip():
            return _json(
                {"success": False, "message": "filename is required"},
                status=400,
            )
        if not isinstance(workflow, dict):
            return _json(
                {"success": False,
                 "message": "workflow must be a JSON object (UI-format map)"},
                status=400,
            )
        # Sanity: UI-format workflows always have a `nodes` list.
        if not isinstance(workflow.get("nodes"), list):
            return _json(
                {"success": False,
                 "message": "workflow.nodes missing or not a list "
                            "(is this UI format, not API format?)"},
                status=400,
            )

        try:
            filename = _sanitize_filename(filename_raw)
        except ValueError as e:
            return _json({"success": False, "message": str(e)}, status=400)

        try:
            _WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
            target = _WORKFLOWS_DIR / filename
            target.write_text(
                json.dumps(workflow, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[kritomatic:workflows] save failed: {e}", flush=True)
            return _json(
                {"success": False, "message": f"save failed: {e}"},
                status=500,
            )

        n_nodes = len(workflow.get("nodes") or [])
        n_links = len(workflow.get("links") or [])
        print(
            f"[kritomatic:workflows] saved {target} "
            f"({n_nodes} nodes, {n_links} links)",
            flush=True,
        )

        # Notify the daemon so it re-reads the file if this was the
        # currently-active workflow.  Fire-and-forget: the ComfyUI
        # event loop must not block on a daemon that may be absent,
        # and the save's success contract does not depend on the
        # notification succeeding.  See the module docstring,
        # "Daemon notification".
        _spawn_daemon_notify(filename)

        return _json({
            "success": True,
            "filename": filename,
            "path": str(target),
            "nodes": n_nodes,
            "links": n_links,
            "daemon_notify": "sent",
        })

    _ROUTE_REGISTERED = True
    print(
        f"[kritomatic:workflows] registered /kritomatic/save_workflow "
        f"(target: {_WORKFLOWS_DIR})",
        flush=True,
    )


_register_route()
