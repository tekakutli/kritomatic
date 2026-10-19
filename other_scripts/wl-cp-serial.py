#!/usr/bin/env python3
"""wl-cp-serial — queue multiple files, paste one at a time.

Each file stays on the clipboard until you advance manually.
Advance with:
  wl-cp-serial --next        (or send SIGUSR1 to the worker PID)

Usage:
  wl-cp-serial FILE [FILE...]      queue files and daemonize
  wl-cp-serial --next              advance to the next file
  wl-cp-serial --stop              abort the queue
  wl-cp-serial --foreground ...    run in the terminal (debug)
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

# ---------------------------------------------------------------- helpers

def notify(summary, body=""):
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "-a", "wl-cp-serial", summary, body],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

def pid_file_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in display)
    return Path(runtime) / f"wl-cp-serial.{safe}.pid"

def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def stop_existing(quiet=False) -> int:
    pf = pid_file_path()
    if not pf.exists():
        if not quiet:
            print("wl-cp-serial: no running queue")
        return 0
    try:
        pid = int(pf.read_text().strip())
    except (ValueError, OSError):
        pf.unlink(missing_ok=True)
        return 0
    if is_alive(pid):
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for _ in range(40):
            if not is_alive(pid):
                break
            time.sleep(0.05)
        if is_alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if not quiet:
            print(f"wl-cp-serial: stopped (pid {pid})")
    pf.unlink(missing_ok=True)
    return 0

# ---------------------------------------------------------------- worker

def worker_main(uris, pf, quiet):
    """Runs inside the daemonized grandchild."""

    flag_path = Path(
        os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
    ) / "wl-cp-serial-active"
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(str(os.getpid()))

    def cleanup(signum, frame):
        flag_path.unlink(missing_ok=True)
        pf.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    index = 0
    total = len(uris)
    current_proc = None

    def start_current():
        nonlocal current_proc
        if current_proc and current_proc.poll() is None:
            current_proc.terminate()
            try:
                current_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                current_proc.kill()
        if index >= total:
            return False
        uri = uris[index]
        payload = (uri + "\r\n").encode()
        current_proc = subprocess.Popen(
            ["wl-copy", "-t", "text/uri-list"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        current_proc.stdin.write(payload)
        current_proc.stdin.close()
        if not quiet:
            notify(
                "wl-cp-serial",
                f"File {index+1}/{total} on clipboard — press Ctrl+V to paste",
            )
        return True

    # SIGUSR1 advances to the next file.
    def advance(signum, frame):
        nonlocal index
        index += 1
        if not start_current():
            flag_path.unlink(missing_ok=True)
            pf.unlink(missing_ok=True)
            if not quiet:
                notify("wl-cp-serial", "All files processed.")
            sys.exit(0)

    signal.signal(signal.SIGUSR1, advance)

    if not start_current():
        flag_path.unlink(missing_ok=True)
        pf.unlink(missing_ok=True)
        return

    while True:
        signal.pause()  # wait for SIGUSR1 or SIGTERM

# ---------------------------------------------------------------- entry point

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="wl-cp-serial",
        description="Queue files for serialized pasting. Advance manually with --next.",
    )
    parser.add_argument("paths", nargs="*", metavar="FILE")
    parser.add_argument("--next", action="store_true",
                        help="advance the running queue to the next file")
    parser.add_argument("--stop", action="store_true",
                        help="stop a running queue")
    parser.add_argument("--foreground", action="store_true",
                        help="do not daemonize; block in this terminal")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress notifications")
    args = parser.parse_args(argv)

    if args.stop:
        return stop_existing(quiet=args.quiet)

    if args.next:
        pf = pid_file_path()
        if not pf.exists():
            print("wl-cp-serial: no running queue", file=sys.stderr)
            return 1
        try:
            pid = int(pf.read_text().strip())
        except (ValueError, OSError):
            print("wl-cp-serial: invalid PID file", file=sys.stderr)
            return 1
        if not is_alive(pid):
            print("wl-cp-serial: queue process is dead", file=sys.stderr)
            pf.unlink(missing_ok=True)
            return 1
        try:
            os.kill(pid, signal.SIGUSR1)
        except ProcessLookupError:
            print("wl-cp-serial: could not signal worker", file=sys.stderr)
            return 1
        return 0

    if not args.paths:
        parser.error("no files given (or use --next / --stop)")

    if shutil.which("wl-copy") is None:
        print("error: wl-copy not found. Install: sudo apt install wl-clipboard",
              file=sys.stderr)
        return 1

    uris = []
    for p in args.paths:
        if not os.path.exists(p):
            print(f"error: no such file or directory: {p}", file=sys.stderr)
            return 1
        uris.append("file://" + quote(os.path.abspath(p)))

    stop_existing(quiet=True)
    pf = pid_file_path()
    pf.parent.mkdir(parents=True, exist_ok=True)

    if args.foreground:
        pf.write_text(str(os.getpid()))
        if not args.quiet:
            print(f"wl-cp-serial: {len(uris)} file(s) queued — use `wl-cp-serial --next` to advance")
        worker_main(uris, pf, args.quiet)
        return 0

    # Double-fork daemonize.
    pid = os.fork()
    if pid > 0:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if pf.exists() and pf.read_text().strip():
                break
            time.sleep(0.02)
        if not pf.exists():
            print("error: failed to start queue", file=sys.stderr)
            return 1
        time.sleep(0.1)
        if not args.quiet:
            print(f"wl-cp-serial: {len(uris)} file(s) queued")
        return 0

    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    try:
        os.setsid()
    except OSError:
        pass

    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    pf.write_text(str(os.getpid()))
    worker_main(uris, pf, args.quiet)
    os._exit(0)

if __name__ == "__main__":
    sys.exit(main())
