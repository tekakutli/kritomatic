# workbench/workbench_playground.py
"""Workbench — iframe tab container + playground launcher.

    python workbench/workbench_playground.py

Serves the workbench HTML at http://127.0.0.1:8781/workbench.html
and registers with the hub as `role="panel"` (not killable).

Additionally exposes two HTTP routes that the workbench page uses
to discover and start playgrounds without needing a
`pgkit.launcher.Launcher` process:

    POST /catalog   → {"success": true, "playgrounds": [{name, entry}, ...]}
                      Discovered by pkgutil.iter_modules over the
                      playgrounds/ directory.  Excludes packages
                      without a matching `<name>/<name>_playground.py`
                      file (so panels like remote_panel and this
                      workbench are omitted, and so are
                      non-playground directories like pgkit,
                      pgruntime, shared_cable_room).

    POST /start     → spawns `python <entry>` as a subprocess and
                      returns {"success": true, "pid": N}.
                      Detached via start_new_session=True so the
                      workbench tab can be closed without killing
                      the playground.  Does a fast-crash check
                      (proc.wait with a 300 ms timeout): if the
                      subprocess dies in that window (missing
                      dependency, syntax error, port already in use
                      — all happen within the first ~200 ms of
                      Python startup), returns {"success": false,
                      stderr: "..."} so the client can show the
                      failure reason.  If it's still alive at 300 ms
                      it is returned as success; the browser's own
                      registration poll handles the rest.

SUPPRESSING THE SPAWNED PLAYGROUND'S BROWSER TAB
================================================
Every playground's own `main()` calls `kit_serve(..., open_browser=True)`
or `serve_nonblocking(..., open_browser=OPEN_BROWSER)` with
`OPEN_BROWSER = True`.  When the workbench spawns that process, the
child would open its own browser tab on top of the iframe tab the
workbench creates — a duplicate.

`_handle_start` fixes this by setting `PGKIT_NO_BROWSER=1` in the
child's environment.  `pgkit.server._browser_enabled()` reads that
variable at the single place where `webbrowser.open()` is called,
and short-circuits.  See `pgkit/server.py`'s docstring, BROWSER
SUPPRESSION, for the full rationale.

The standalone case (`python outpaint/outpaint_playground.py`) is
unaffected: nothing sets `PGKIT_NO_BROWSER` in the user's shell, so
`_browser_enabled()` returns True and `webbrowser.open()` runs the
real browser.

The workbench's own `main()` keeps `open_browser=True` — the user
ran `python workbench_playground.py` and wants the workbench tab.
"""

import os
import subprocess
import sys
import time
import pkgutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import workbench_html
from pgruntime.ports import port_for
from pgkit.server import serve as kit_serve

HTML_FILE = "workbench.html"
PORT      = port_for("workbench")

# Track subprocesses spawned by /start so a future /stop can reach
# them, and so /start can return the existing pid if a name is
# requested twice.  Not persisted across workbench restarts — the
# hub-registered `playgrounds` list is the source of truth for
# "what's running", and the client filters the catalog against it.
_spawned: dict = {}


def _discover_playgrounds():
    """Return [{name, entry}] for every playground package.

    Uses `pkgutil.iter_modules` over `playgrounds/` — same discovery
    `run_all.py` uses — but does NOT import each entry module.
    Importing can have side effects (registering with the daemon,
    spawning threads) and we only need the file path for subprocess
    invocation.

    The `<name>/<name>_playground.py` existence check filters out
    subpackages that are not playgrounds: `pgkit`, `pgruntime`, and
    `shared_cable_room` are packages but have no playground entry,
    and `remote_panel`/`workbench`/`room`/`cable`/`remote_cli` have
    no `__init__.py` (so `iter_modules` skips them before the check
    even runs).  The result is exactly the set of playgrounds a
    user can meaningfully open in the workbench.
    """
    out = []
    for _, name, ispkg in pkgutil.iter_modules([_PARENT]):
        if not ispkg:
            continue
        entry = os.path.join(_PARENT, name, f"{name}_playground.py")
        if not os.path.isfile(entry):
            continue
        out.append({"name": name, "entry": entry})
    out.sort(key=lambda d: d["name"])
    return out


def _prune_dead():
    """Drop entries from _spawned whose subprocess has exited."""
    for k in list(_spawned.keys()):
        p = _spawned[k]
        if p.poll() is not None:
            del _spawned[k]


def _handle_catalog(_payload):
    return {"success": True, "playgrounds": _discover_playgrounds()}


# How long `/start` blocks waiting for the child to crash.  See
# this module's docstring, POST /start.  A hardcoded 1.5 s used to
# live here and made every spawn feel 1.2 s slower than it needed to
# be: the child registers with the hub in 500–900 ms on a warm
# cache, well before the sleep was over, so the browser was idling
# on a sleep that had already served its purpose.
#
# 300 ms is enough to catch the class of failure this check exists
# for:
#   · SyntaxError / ImportError  — fail within ~50–150 ms of spawn
#   · port in use                — fails when serve_nonblocking
#                                  binds, ~150–250 ms
# Slow failures (disk contention, NFS, cold .pyc compile on a slow
# mount) slip past 300 ms, but those are rare and the browser's own
# 20 s registration-timeout banner still surfaces them.
_FAST_CRASH_WINDOW_S = 0.3


def _handle_start(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}
    name = (payload.get("name") or "").strip()
    if not name:
        return {"success": False, "message": "name required"}

    _prune_dead()

    existing = _spawned.get(name)
    if existing is not None and existing.poll() is None:
        return {"success": True, "pid": existing.pid,
                "message": f"{name} already running"}

    catalog = {d["name"]: d for d in _discover_playgrounds()}
    if name not in catalog:
        return {"success": False,
                "message": f"unknown playground {name!r}"}

    entry = catalog[name]["entry"]
    cwd   = os.path.dirname(entry)

    # Suppress the child's browser tab.  See the module docstring,
    # SUPPRESSING THE SPAWNED PLAYGROUND'S BROWSER TAB, and
    # pgkit/server.py's BROWSER SUPPRESSION section for why this
    # env var (and not BROWSER=/bin/true).
    env = os.environ.copy()
    env["PGKIT_NO_BROWSER"] = "1"

    try:
        proc = subprocess.Popen(
            [sys.executable, entry],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
    except Exception as e:
        return {"success": False, "message": f"spawn failed: {e}"}

    # Fast-crash check.  See module docstring and _FAST_CRASH_WINDOW_S.
    # proc.wait() returns immediately if the process exits inside the
    # window, and raises TimeoutExpired otherwise — no polling loop
    # needed.  The pipe deadlock concern (child fills stderr before
    # exiting, nobody reads it, so .wait() blocks forever) doesn't
    # apply here: we bound .wait() with a 300 ms timeout, so even if
    # the child wedges on a full stderr buffer we return success and
    # let the browser's registration timeout be the backstop.
    try:
        proc.wait(timeout=_FAST_CRASH_WINDOW_S)
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is not None:
        try:
            _, err = proc.communicate(timeout=1)
        except Exception:
            err = b""
        return {
            "success": False,
            "message": f"{name} crashed on startup "
                       f"(exit {proc.returncode})",
            "stderr": (err.decode(errors="replace")[:4000] if err else ""),
        }

    _spawned[name] = proc
    return {"success": True, "pid": proc.pid,
            "message": f"started {name}"}


ROUTES = {
    "/catalog": _handle_catalog,
    "/start":   _handle_start,
}


def main():
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(workbench_html.render())
    print(f"Wrote {HTML_FILE}")
    print(f"  open : http://127.0.0.1:{PORT}/{HTML_FILE}")
    kit_serve(
        port         = PORT,
        open_browser = True,
        html_file    = HTML_FILE,
        routes       = ROUTES,
        hub_name     = "workbench",
        hub_role     = "panel",
    )


if __name__ == "__main__":
    main()