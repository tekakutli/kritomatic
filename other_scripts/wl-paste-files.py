#!/usr/bin/env python3
"""wl-paste-files — paste files/directories from the Wayland clipboard
(a text/uri-list, e.g. copied in Nautilus/Files) into a target directory.

Usage: wl-paste-files [DIR] [--move]
"""

import argparse
import os
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlparse


GNOME_MIME = "x-special/gnome-copied-files"
URI_MIME   = "text/uri-list"


def die(msg, rc=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(rc)


def clipboard_types():
    try:
        r = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        die("wl-paste not found. Install with: sudo apt install wl-clipboard")
    except subprocess.CalledProcessError:
        return []
    return [t.strip() for t in r.stdout.splitlines() if t.strip()]


def clipboard_read(mime):
    try:
        r = subprocess.run(
            ["wl-paste", "--no-newline", "--type", mime],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        die(f"wl-paste exited with status {e.returncode}", e.returncode)
    return r.stdout.decode("utf-8", "replace")


def parse_uri_list(text):
    """RFC 2483: CRLF-separated lines, lines beginning with '#' are comments."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def parse_gnome_copied_files(text):
    """First line is 'copy' or 'cut'; the rest is a text/uri-list."""
    lines = text.replace("\r\n", "\n").split("\n")
    op = "copy"
    if lines and lines[0].strip() in ("copy", "cut"):
        op = lines[0].strip()
        lines = lines[1:]
    return op, parse_uri_list("\n".join(lines))


def uri_to_path(uri):
    p = urlparse(uri)
    if p.scheme != "file":
        return None
    if p.netloc not in ("", "localhost"):
        return None
    return unquote(p.path)


def copy_entry(src, dst):
    """Copy one entry, preserving symlinks the way a GUI file manager does."""
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    elif os.path.isdir(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="wl-paste-files",
        description=(
            "Paste files/directories that were copied to the Wayland clipboard "
            "by a GUI file manager into the given directory."
        ),
    )
    parser.add_argument(
        "directory", nargs="?", default=".",
        help="destination directory (default: current directory)",
    )
    parser.add_argument(
        "--move", action="store_true",
        help="move instead of copy (e.g. to honour a GUI 'cut' selection)",
    )
    args = parser.parse_args(argv)

    if shutil.which("wl-paste") is None:
        die("wl-paste not found. Install with: sudo apt install wl-clipboard")

    dest_dir = os.path.abspath(args.directory)
    if not os.path.isdir(dest_dir):
        die(f"not a directory: {args.directory}")

    types = clipboard_types()
    op = "copy"
    uris = []

    if GNOME_MIME in types:
        op, uris = parse_gnome_copied_files(clipboard_read(GNOME_MIME))
    elif URI_MIME in types:
        uris = parse_uri_list(clipboard_read(URI_MIME))
    else:
        die("clipboard does not contain file URIs "
            f"(no {GNOME_MIME!r} or {URI_MIME!r})")

    if not uris:
        die("clipboard contains no file URIs")

    paths = []
    for u in uris:
        p = uri_to_path(u)
        if p is None:
            print(f"warning: skipping non-local URI: {u}", file=sys.stderr)
            continue
        if not os.path.lexists(p):
            die(f"no such file or directory: {p}")
        paths.append(p)

    if not paths:
        die("no local files in clipboard")

    # Pre-flight name-collision check so we never half-paste.
    plan = []
    for src in paths:
        name = os.path.basename(src.rstrip(os.sep)) or src
        dst = os.path.join(dest_dir, name)
        if os.path.lexists(dst):
            die(f"destination already exists: {dst}")
        plan.append((src, dst))

    for src, dst in plan:
        try:
            if args.move:
                shutil.move(src, dst)
            else:
                copy_entry(src, dst)
        except OSError as e:
            die(f"failed to {'move' if args.move else 'copy'} "
                f"{src} -> {dst}: {e}")
        print(dst)

    if op == "cut" and not args.move:
        print(
            "note: the clipboard was a 'cut' selection from the GUI, but the "
            "source files were left in place. Re-run with --move to actually "
            "move them.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
