#!/usr/bin/env python3
"""
Dump files (filtered by extension) into a single markdown file.

Usage:
    dump.py [-a AFFIX] [-r] [-t]

All configuration (directory, output path, extensions, default affix,
default recursion, tree-only, excluded directories, included/excluded
filename substrings) is done via the variables at the top of this file.
The only CLI flags are -a/--affix, -r/--recursive and -t/--tree-only,
which override their DEFAULT_* counterparts when provided.

The scanned directory is always recorded at the top of the output, and
its basename is appended to the output filename (e.g. project_dump.md
becomes project_dump_myproject.md when scanning ".../myproject").
When recursion is enabled (-r or DEFAULT_RECURSIVE = True), a directory
tree of the dumped files is also prepended.

With -t/--tree-only the file contents are omitted: only the directory
tree is written. This flag implies recursion.
"""

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# CONFIGURATION — edit these
# ---------------------------------------------------------------------------
DEFAULT_DIR        = "."              # directory to scan
DEFAULT_OUT        = "project_dump.md" # output markdown file
# Common extensions:
#   .py       Python
#   .sh       Bash / shell
#   .json     JSON
#   .yaml/.yml YAML
#   .service  systemd unit files
#   .md       Markdown
#   .toml     TOML
DEFAULT_EXTENSIONS = [".py", ".sh", "yaml", ".md", ".conf"]
DEFAULT_AFFIX      = None             # e.g. "_test" (substring matched in filename)
DEFAULT_RECURSIVE  = False            # descend into subdirectories
DEFAULT_TREE_ONLY  = False            # only emit the directory tree, no file contents
# Directory names to skip entirely (exact match on the directory basename,
# at any depth). Add your own as needed.
# DEFAULT_EXCLUDE_DIRS = [".git", "__pycache__", ".venv", "venv", "node_modules"]
DEFAULT_EXCLUDE_DIRS = [".git", "__pycache__", ".venv", "venv", "node_modules", "kritomatic_xremap", "other_scripts"]

# Filename substrings whitelist: if NON-EMPTY, a file is only kept when its
# filename contains AT LEAST ONE of these substrings (partial match,
# case-sensitive). Leave empty ([]) to disable this filter entirely.
# e.g. ["_config", "settings"] to only dump config-like files.
DEFAULT_INCLUDE_FILE_SUBSTRINGS = ["cable", "pg", "room"]

# Filename substrings blacklist: if a filename contains ANY of these
# (partial match, case-sensitive), the file is skipped.
# e.g. [".min.", "_test", ".bak"]
DEFAULT_EXCLUDE_FILE_SUBSTRINGS = ["project_dump"]
# ---------------------------------------------------------------------------

HEADER = "=" * 70

# Sentinel key used inside the tree dict to hold the files of a directory.
_FILES = "\0files"


def normalize_ext(ext: str) -> str | None:
    ext = ext.strip()
    if not ext:
        return None
    if not ext.startswith("."):
        ext = "." + ext
    return ext


def find_files(
    root: Path,
    extensions: list[str],
    affix: str | None,
    exclude_dirs: set[str],
    include_file_substrings: list[str],
    exclude_file_substrings: list[str],
    recursive: bool = True,
) -> list[Path]:
    results: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # prune excluded directories in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]

        # if not recursing, prevent os.walk from descending any further
        if not recursive:
            dirnames[:] = []

        for name in filenames:
            if extensions and not any(name.endswith(e) for e in extensions):
                continue
            if affix and affix not in name:
                continue
            # whitelist: if set, filename must contain at least one of these
            if include_file_substrings and not any(
                s in name for s in include_file_substrings
            ):
                continue
            if exclude_file_substrings and any(
                s in name for s in exclude_file_substrings
            ):
                continue
            results.append(Path(dirpath) / name)

    results.sort()
    return results


def build_tree(rel_paths: list[Path]) -> dict:
    """Build a nested dict from relative paths.

    Directories map to sub-dicts; the files of a directory live under the
    special `_FILES` key as a list of names.
    """
    tree: dict = {}
    for p in rel_paths:
        parts = p.parts
        if not parts:
            continue
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault(_FILES, []).append(parts[-1])
    return tree


def _render_tree(node: dict, prefix: str) -> list[str]:
    lines: list[str] = []
    dirs = sorted(k for k in node if k != _FILES)
    files = sorted(node.get(_FILES, []))
    entries: list[tuple[str, bool]] = (
        [(d, True) for d in dirs] + [(f, False) for f in files]
    )

    for i, (name, is_dir) in enumerate(entries):
        last = i == len(entries) - 1
        connector = "└── " if last else "├── "
        lines.append(prefix + connector + name + ("/" if is_dir else ""))
        if is_dir:
            extension = "    " if last else "│   "
            lines.extend(_render_tree(node[name], prefix + extension))
    return lines


def render_tree(files: list[Path], root: Path) -> str:
    """Return a unix-`tree`-style rendering of the given files."""
    rel_paths = [Path(os.path.relpath(f, root)) for f in files]
    tree = build_tree(rel_paths)

    label = str(root)
    if label in ("", "."):
        label = "."

    lines = [label + "/"]
    lines.extend(_render_tree(tree, ""))
    return "\n".join(lines)


def output_path_with_dirname(base_out: Path, scanned_dir: Path) -> Path:
    """Append the basename of `scanned_dir` to `base_out` before its suffix.

    e.g. ("project_dump.md", "/home/user/myproject") -> "project_dump_myproject.md"
    """
    dirname = scanned_dir.name
    if not dirname:  # e.g. scanning filesystem root "/"
        return base_out
    # Sanitize a bit so we don't create weird filenames
    dirname = dirname.replace(os.sep, "_").replace("/", "_")
    return base_out.with_name(f"{base_out.stem}_{dirname}{base_out.suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump files into a markdown file. "
                    "All paths/extensions come from variables in the script; "
                    "only the affix, recursion, and tree-only mode can be "
                    "overridden on the CLI."
    )
    parser.add_argument(
        "-a", "--affix", default=None,
        help=("Only include files whose filename contains this substring. "
              "Pass an empty string to disable. "
              f"(hardcoded default: {DEFAULT_AFFIX!r})"),
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true", default=None,
        help=("Recurse into subdirectories. Also prepends a directory tree "
              "of the dumped files to the output. "
              f"(hardcoded default: {DEFAULT_RECURSIVE})"),
    )
    parser.add_argument(
        "-t", "--tree-only", action="store_true", default=None,
        help=("Only write the directory tree; skip dumping the file "
              "contents. Implies recursion. "
              f"(hardcoded default: {DEFAULT_TREE_ONLY})"),
    )
    args = parser.parse_args()

    root = Path(DEFAULT_DIR)
    out = Path(DEFAULT_OUT)

    extensions = [e for e in (normalize_ext(x) for x in DEFAULT_EXTENSIONS) if e]
    exclude_dirs = {d for d in DEFAULT_EXCLUDE_DIRS if d}
    include_file_substrings = [s for s in DEFAULT_INCLUDE_FILE_SUBSTRINGS if s]
    exclude_file_substrings = [s for s in DEFAULT_EXCLUDE_FILE_SUBSTRINGS if s]

    # Affix: CLI overrides hardcoded default; "" means "no affix"
    affix = args.affix if args.affix is not None else DEFAULT_AFFIX
    if affix == "":
        affix = None

    # Tree-only: CLI forces True, otherwise fall back to the hardcoded default
    tree_only = True if args.tree_only else DEFAULT_TREE_ONLY

    # Recursive: -r forces True, -t also forces True; otherwise the default
    recursive = True if (args.recursive or tree_only) else DEFAULT_RECURSIVE

    files = find_files(
        root,
        extensions,
        affix,
        exclude_dirs,
        include_file_substrings,
        exclude_file_substrings,
        recursive,
    )
    if not files:
        msg = f"No files found in '{root}'"
        if extensions:
            msg += f" with extensions {extensions}"
        if affix:
            msg += f" matching affix '{affix}'"
        if include_file_substrings:
            msg += f" matching any of {include_file_substrings}"
        if not recursive:
            msg += " (non-recursive)"
        print(msg, file=sys.stderr)
        return 1

    total = len(files)

    # Resolve the scanned directory to an absolute path, and derive both the
    # human-readable label and the output filename suffix from it.
    scanned_dir = Path(os.path.abspath(str(root)))
    out = output_path_with_dirname(out, scanned_dir)

    with out.open("w", encoding="utf-8") as fh:
        # Always record which directory was scanned
        fh.write(HEADER + "\n")
        fh.write(f"SCANNED DIRECTORY: {scanned_dir}\n")
        fh.write(HEADER + "\n\n")

        if recursive:
            fh.write(HEADER + "\n")
            fh.write("DIRECTORY TREE\n")
            fh.write(HEADER + "\n")
            fh.write("```\n")
            fh.write(render_tree(files, root) + "\n")
            fh.write("```\n\n")

        # Skip the file contents entirely in tree-only mode
        if not tree_only:
            for i, f in enumerate(files, 1):
                display = str(f)
                if display.startswith("./"):
                    display = display[2:]

                fh.write(HEADER + "\n")
                fh.write(f"FILE {i}/{total}: {display}\n")
                fh.write(HEADER + "\n")
                fh.write("```python\n")

                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except OSError as e:
                    content = f"# ERROR READING FILE: {e}\n"

                fh.write(content)
                if content and not content.endswith("\n"):
                    fh.write("\n")
                fh.write("```\n\n")

    if tree_only:
        print(f"Wrote tree only ({total} file(s)) to {out}", file=sys.stderr)
    else:
        print(f"Wrote {total} file(s) to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
