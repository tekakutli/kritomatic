#!/usr/bin/env python3
"""
Add or remove a script entry in merger.py's SCRIPTS dict.

The placeholder is derived from the filename:
    text_as_image.py  ->  __TEXT_AS_IMAGE_SCRIPT__

Usage:
    ./inject_script.py --list
    ./inject_script.py text_as_image.py
    ./inject_script.py --remove text_as_image.py
"""

import argparse
import ast
import sys
from pathlib import Path


def derive_placeholder(filename: str) -> str:
    """text_as_image.py -> __TEXT_AS_IMAGE_SCRIPT__"""
    return f"__{Path(filename).stem.upper()}_SCRIPT__"


def find_merger(explicit: Path | None) -> Path:
    if explicit:
        p = explicit.resolve()
        if not p.is_file():
            sys.exit(f"ERROR: merger.py not found at {p}")
        return p
    here = Path(__file__).resolve().parent
    for candidate in (here / "merger.py", here.parent / "merger.py"):
        if candidate.is_file():
            return candidate
    sys.exit("ERROR: could not locate merger.py; pass --merger PATH")


def find_scripts_node(source: str) -> ast.Assign:
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SCRIPTS":
                    if not isinstance(node.value, ast.Dict):
                        sys.exit("ERROR: SCRIPTS in merger.py is not a dict literal")
                    return node
    sys.exit("ERROR: SCRIPTS dict not found in merger.py")


def read_items(node: ast.Assign) -> list[tuple[str, str]]:
    items = []
    for k, v in zip(node.value.keys, node.value.values):
        if not (isinstance(k, ast.Constant) and isinstance(v, ast.Constant)):
            sys.exit("ERROR: SCRIPTS keys/values must be string literals")
        items.append((k.value, v.value))
    return items


def render_block(items: list[tuple[str, str]]) -> str:
    lines = ["SCRIPTS = {\n"]
    for placeholder, filename in items:
        lines.append(f'    "{placeholder}": "{filename}",\n')
    lines.append("}\n")
    return "".join(lines)


def rewrite_merger(merger_path: Path, items: list[tuple[str, str]]) -> None:
    source = merger_path.read_text(encoding="utf-8")
    node = find_scripts_node(source)
    start = node.lineno - 1
    end = node.end_lineno
    lines = source.splitlines(keepends=True)
    new_source = "".join(lines[:start]) + render_block(items) + "".join(lines[end:])
    merger_path.with_suffix(".py.bak").write_text(source, encoding="utf-8")
    merger_path.write_text(new_source, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add or remove a script entry in merger.py's SCRIPTS dict.",
    )
    parser.add_argument("filename", nargs="?",
                        help="Filename inside scripts/, e.g. text_as_image.py")
    parser.add_argument("--remove", action="store_true",
                        help="Remove the entry instead of adding it.")
    parser.add_argument("--list", action="store_true",
                        help="List current SCRIPTS entries and exit.")
    parser.add_argument("--merger", type=Path, default=None,
                        help="Path to merger.py (default: auto-detect).")
    args = parser.parse_args()

    merger_path = find_merger(args.merger)

    if args.list:
        source = merger_path.read_text(encoding="utf-8")
        for placeholder, filename in read_items(find_scripts_node(source)):
            print(f"{placeholder:<40s} scripts/{filename}")
        return 0

    if not args.filename:
        parser.error("a filename is required (or use --list)")

    placeholder = derive_placeholder(args.filename)

    source = merger_path.read_text(encoding="utf-8")
    items = read_items(find_scripts_node(source))
    existing = dict(items)

    if args.remove:
        if placeholder not in existing:
            sys.exit(f"ERROR: {placeholder} not present in SCRIPTS")
        items = [(p, f) for p, f in items if p != placeholder]
        rewrite_merger(merger_path, items)
        print(f"✓ removed {placeholder}")
        return 0

    if not (merger_path.parent / "scripts" / args.filename).is_file():
        print(f"WARNING: scripts/{args.filename} does not exist yet",
              file=sys.stderr)

    if placeholder in existing:
        if existing[placeholder] == args.filename:
            print(f"no change: {placeholder} already maps to {args.filename}")
            return 0
        items = [(p, args.filename if p == placeholder else f)
                 for p, f in items]
        message = f"updated {placeholder}: {existing[placeholder]} -> {args.filename}"
    else:
        items.append((placeholder, args.filename))
        message = f"added {placeholder} -> {args.filename}"

    rewrite_merger(merger_path, items)
    print(f"✓ {message}")
    print()
    print("YAML binding for kritomatic_xremap.yaml:")
    print("  KEY:")
    print(f'    launch: ["python", "{placeholder}"]')

    return 0


if __name__ == "__main__":
    sys.exit(main())
