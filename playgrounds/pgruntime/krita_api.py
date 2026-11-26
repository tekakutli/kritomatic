"""
pgruntime.krita_api — the higher-level `KritaClient` wrapper shared by
the two playgrounds that talk to daemon commands outside the standard
`/krita_status` / `/active_state` / `/resource_image` set: `board` and
`text_boxes`.

Both playgrounds talk to the daemon through `KritaClient` (from
`src/kritomatic/client.py`), not the raw TCP `daemon_client` the other
seven use.  The client surface is different (`connect` / `execute` /
`close` / `send_batch`), the commands are different (document open /
activate / dump / paste-transforms / crop), and the two playgrounds
don't register with the daemon's playground registry at all.

Nonetheless, five pieces are near-identical across the two:

    · `_find_repo_src()` — walk up from a module's file to find
      `src/kritomatic`, so `from kritomatic.client import KritaClient`
      can succeed regardless of the playground's launch directory
    · the `KritaClient` import bootstrap — try, on ImportError add
      `src/` to `sys.path`, retry
    · `_call(cmd_type, **kwargs)` — one command round-trip,
      returning `(data, err)` with `err` as the failure indicator
    · `open_file(path, add_view)` — wrap `open_document`
    · `activate_document(name)` — wrap `activate_document`
    · `pick_files(...)` — the tkinter file dialog, parametrised by
      title and filetypes

Extracting them here means `board` and `text_boxes` stop diverging:
board already has two bug fixes (documented in its `_call` and
`activate_document` docstrings) that text_boxes doesn't, and
text_boxes's `activate_document` currently returns
`{success: False, message: "unknown"}` on every *successful*
activation because it tests `data is None` instead of
`err is not None` (the daemon returns no `data` field for
`activate_document`).  Standardising on the shared implementation
fixes that bug as a byproduct of the port.

The surface is deliberately narrow.  Playground-specific commands
(`dump_all_text_shapes`, `patch_layer_text`,
`get_all_document_thumbnails`, `paste_document_transform_as_layer`,
`create_document`, ...) stay in the playground modules.  If a third
playground ever needs the shared pieces, it can import them the same
way; if it needs to construct a `KritaClient` directly (board's
`paste_transforms` does, to build a batch), `KritaClient` is
re-exported here.

`pick_files` imports `tkinter`, a server-side GUI library.  It's
wrapped in `try/except` that returns `{success: False, message:
"tkinter not installed; ..."}` on a headless box, exactly matching
the current per-playground behaviour.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path


def find_repo_src() -> Path:
    """Walk up from this module's file to find `src/kritomatic`.

    Raises `RuntimeError` if not found within ten directory levels —
    a repo-layout error worth surfacing loudly rather than masking
    with a best-effort default.
    """
    p = Path(__file__).resolve().parent
    for _ in range(10):
        cand = p / "src"
        if (cand / "kritomatic").is_dir():
            return cand
        if p == p.parent:
            break
        p = p.parent
    raise RuntimeError(
        "Could not locate src/kritomatic from " + str(Path(__file__).resolve())
    )


def _load_krita_client():
    """Import `KritaClient` from `src/kritomatic/client.py`, adding
    the repo's `src/` to `sys.path` on ImportError (which happens
    when a playground is launched from a directory that doesn't
    already have the source tree on `PYTHONPATH`)."""
    try:
        from kritomatic.client import KritaClient
    except ImportError:
        src = find_repo_src()
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from kritomatic.client import KritaClient
    return KritaClient


KritaClient = _load_krita_client()


# A single lock guards the native file picker against a second
# invocation spawning a second dialog before the first closes.
# Shared across both playgrounds' pickers.
_picker_lock = threading.Lock()


def call(cmd_type: str, *, log_prefix: str = "playground", **kwargs):
    """Send one command to the daemon via `KritaClient`.

    Returns `(data, None)` on success and `(None, message)` on
    failure.

    **On success `data` may be `{}` or `None`** — the daemon's
    dispatcher writes `data: None` for commands that do not return a
    payload (`activate_document` is the canonical case).  Callers
    must therefore test for failure with `err is not None`, never
    with `data is None`.  This is the bug class that
    `board_krita.activate_document` fixed; `text_boxes_krita`'s
    equivalent still has the old `data is None` check and is fixed
    by importing from this module.
    """
    client = KritaClient()
    if not client.connect():
        print(f"[{log_prefix}] {cmd_type}: daemon not reachable", flush=True)
        return None, "Kritomatic daemon not reachable"
    try:
        resp = client.execute(cmd_type, **kwargs)
    finally:
        client.close()
    if not resp:
        print(f"[{log_prefix}] {cmd_type}: no response", flush=True)
        return None, "no response from daemon"
    if resp.get("status") != "success":
        msg = resp.get("message", "unknown daemon error")
        print(f"[{log_prefix}] {cmd_type}: FAILED: {msg}", flush=True)
        return None, msg
    return resp.get("data", {}), None


def open_file(path: str, add_view: bool = True,
              *, log_prefix: str = "playground") -> dict:
    """Open a `.kra` (or other file) in Krita via `open_document`.

    Returns `{success, message, name}` — `name` is the document name
    Krita assigned, or `""` if the response carried no name field
    (which happens on some Krita builds; callers should fall back to
    the path).
    """
    try:
        data, err = call("open_document",
                         log_prefix=log_prefix,
                         file_path=path, add_view=add_view)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if err is not None:
        return {"success": False, "message": err or "unknown"}
    return {
        "success": True,
        "message": f"opened {path}",
        "name": (data or {}).get("name", ""),
    }


def activate_document(name: str,
                      *, log_prefix: str = "playground") -> dict:
    """Bring `name` (a document name or file path) to the front.

    Returns `{success, message}`.  See `call`'s docstring for why
    the correct failure check is `err is not None`, not
    `data is None` — the daemon returns no `data` payload for
    `activate_document`, so a successful activation yields
    `(None, None)`.
    """
    try:
        data, err = call("activate_document",
                         log_prefix=log_prefix,
                         name=name)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if err is not None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, "message": f"Activated {name}"}


def pick_files(*, title: str, filetypes: list,
               log_prefix: str = "playground") -> dict:
    """Open the OS-native file picker.

    Returns `{success, paths, message}` — `paths` is a list of
    absolute path strings, or `[]` on user cancel.  Returns
    `{success: False, message: ...}` on a system without tkinter,
    so the caller can surface a helpful error and offer a
    file-upload fallback.

    `filetypes` is a list of `(label, pattern)` tuples passed
    straight through to `filedialog.askopenfilenames`.
    """
    with _picker_lock:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            print(f"[{log_prefix}] pick_files: tkinter not available", flush=True)
            return {"success": False,
                    "message": "tkinter not installed; add paths via Load board"}

        try:
            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            paths = filedialog.askopenfilenames(
                title=title, filetypes=filetypes,
            )
            root.destroy()
            return {"success": True, "paths": [str(p) for p in paths]}
        except Exception as e:
            try:
                root.destroy()
            except Exception:
                pass
            print(f"[{log_prefix}] pick_files: {e}", flush=True)
            return {"success": False, "message": str(e)}