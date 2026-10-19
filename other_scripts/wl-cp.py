#!/usr/bin/env python3
"""wl-cp — copy files/directories to the Wayland clipboard as file URIs,
so you can paste them in Nautilus/Files (or any app using text/uri-list).

Usage: wl-cp FILE [FILE...]
"""

import argparse
import os
import shutil
import subprocess
import sys
from urllib.parse import quote


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="wl-cp",
        description=(
            "Copy one or more files/directories to the Wayland clipboard in "
            "text/uri-list format, ready to be pasted via Ctrl+V in "
            "Nautilus/Files or any other GUI app that accepts file references."
        ),
    )
    parser.add_argument(
        "paths", nargs="+", metavar="FILE",
        help="file(s) or directory(ies) to copy",
    )
    args = parser.parse_args(argv)

    if shutil.which("wl-copy") is None:
        print(
            "error: wl-copy not found. Install with: sudo apt install wl-clipboard",
            file=sys.stderr,
        )
        return 1

    uris = []
    for p in args.paths:
        if not os.path.exists(p):
            print(f"error: no such file or directory: {p}", file=sys.stderr)
            return 1
        # abspath normalises '.'/'..' but does NOT resolve symlinks,
        # matching what Nautilus does when you copy a symlink.
        abs_path = os.path.abspath(p)
        # quote()'s default safe='/' leaves slashes intact and
        # percent-encodes everything else as UTF-8 — exactly what
        # RFC 3986 file URIs need (spaces, '#', '?', non-ASCII, …).
        uris.append("file://" + quote(abs_path))

    # text/uri-list requires CRLF-terminated URIs (incl. after the last one).
    payload = "".join(u + "\r\n" for u in uris).encode()

    try:
        subprocess.run(
            ["wl-copy", "-t", "text/uri-list"],
            input=payload,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"error: wl-copy exited with status {e.returncode}", file=sys.stderr)
        return e.returncode
    except FileNotFoundError:
        # Race: wl-copy disappeared between which() and run().
        print("error: wl-copy not found", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
