#!/usr/bin/env python3
"""wl-cp-last — queue the N most recently modified files in a directory
for serialized pasting via wl-cp-serial. N defaults to 2.

Each Ctrl+V pastes one file; the queue advances automatically (see
wl-cp-auto.sh / xremap binding).
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_last_n(directory: Path, n: int, include_hidden: bool = False) -> list[Path]:
    entries = []
    try:
        for entry in os.scandir(directory):
            if not entry.is_file():
                continue
            if not include_hidden and entry.name.startswith("."):
                continue
            try:
                entries.append((entry.stat().st_mtime, Path(entry.path)))
            except OSError:
                pass
    except OSError as e:
        print(f"error: cannot read {directory}: {e}", file=sys.stderr)
        return []
    entries.sort(key=lambda x: x[0])
    return [p for _, p in entries[-n:]]


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="wl-cp-last",
        description="Queue the N most recently modified files in DIR "
                    "for wl-cp-serial (one paste each).",
    )
    ap.add_argument("directory", nargs="?", default=".",
                    help="directory to scan (default: current)")
    ap.add_argument("-n", "--count", type=int, default=2, metavar="N",
                    help="how many of the newest files to queue (default: 2)")
    ap.add_argument("--include-hidden", action="store_true",
                    help="include dotfiles")
    ap.add_argument("--newest-first", action="store_true",
                    help="paste newest first instead of oldest first")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the chosen files, don't queue")
    args = ap.parse_args()

    if args.count < 1:
        print("error: --count must be >= 1", file=sys.stderr)
        return 1

    directory = Path(args.directory).resolve()
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 1

    files = find_last_n(directory, args.count, args.include_hidden)
    if len(files) < args.count:
        print(f"error: need at least {args.count} files in {directory}, "
              f"found {len(files)}", file=sys.stderr)
        return 1

    if args.newest_first:
        files.reverse()

    print(f"Queued {len(files)} file(s) from {directory}:")
    for f in files:
        print(f"  {f.name}")

    if args.dry_run:
        return 0

    if shutil.which("wl-cp-serial") is None:
        print("error: wl-cp-serial not on PATH. "
              "Symlink it into ~/.local/bin.", file=sys.stderr)
        return 1

    try:
        subprocess.run(["wl-cp-serial", *map(str, files)], check=True)
    except subprocess.CalledProcessError as e:
        return e.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
