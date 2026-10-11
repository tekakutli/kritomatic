#!/usr/bin/env python3
"""
Add or remove a script entry in merger.py's SCRIPTS dict, and a
commented-out binding stub in kritomatic_xremap.yaml.

Two kinds of input are accepted:

  1. A script filename, e.g. text_as_image.py
     The placeholder is derived from the stem:
         text_as_image.py  ->  __TEXT_AS_IMAGE_SCRIPT__

  2. A shell command, e.g. kritomatic window focus_docker --name "KisLayerBox"
     The command is saved as a minimal bash script under
     scripts/bash_commands/, and the SCRIPTS entry points at that
     generated script. The placeholder is derived from a slugified
     form of the command:
         kritomatic window focus_docker --name "KisLayerBox"
             -> __KRITOMATIC_WINDOW_FOCUS_DOCKER_NAME_KISLAYERBOX_SCRIPT__

The two are told apart by a single rule: input containing whitespace
is a command; input without is a script filename.

Running with no positional argument enters input mode, where the script
prompts for one of the above. This is the preferred way to feed in a
command, since commands contain quotes and spaces that are awkward to
pass as a single shell argument.

In addition to editing merger.py, every successful add also drops a
commented-out two-line stub into kritomatic_xremap.yaml, at the end of
the section named in YAML_TARGET_SECTION. The stub uses `KEY` as the
key name, so the human uncomments it and substitutes the key they want:

      # KEY:
      #   launch: ["bash", "__KRITOMATIC_..._SCRIPT__"]

The insertion is idempotent: if the placeholder already appears anywhere
in the YAML, it is not added again. --remove strips the commented stub
along with the SCRIPTS entry, but deliberately leaves an uncommented
binding alone — once you've committed to a key, the injector no longer
has any business touching that line.

Usage:
    ./inject_script.py                              # input mode (preferred)
    ./inject_script.py --list
    ./inject_script.py --remove                     # input mode
    ./inject_script.py text_as_image.py             # single-token only
    ./inject_script.py --remove text_as_image.py
"""

import argparse
import ast
import hashlib
import re
import sys
from pathlib import Path


# Directory under scripts/ where shell commands are saved as bash scripts.
BASH_COMMANDS_DIR = "bash_commands"

# The YAML file that xremap consumes, sitting next to merger.py.
YAML_FILENAME = "kritomatic_xremap.yaml"

# Which named block inside the YAML gets the commented stub.
YAML_TARGET_SECTION = "Global Krita Shortcuts"

# Indentation of the stubbed block. Matches the indentation of a remap
# key inside a named block (2 spaces for the list item, +2 for `remap:`,
# +2 for the key itself). If the YAML's indentation ever changes, this
# is the one line to update.
YAML_INDENT = "      "


# ---------------------------------------------------------------------------
# Placeholder derivation
# ---------------------------------------------------------------------------

def derive_placeholder(filename: str) -> str:
    """text_as_image.py -> __TEXT_AS_IMAGE_SCRIPT__"""
    return f"__{Path(filename).stem.upper()}_SCRIPT__"


def slugify_command(command: str) -> str:
    """kritomatic window focus_docker --name "KisLayerBox"
         -> kritomatic_window_focus_docker_name_KisLayerBox

    Quotes are stripped, every run of non-alphanumeric characters is
    collapsed to a single underscore, and leading/trailing underscores
    are trimmed. If the result is empty (input was all punctuation), a
    short hash of the raw command is used instead, so the filename is
    still unique and stable across runs.
    """
    stripped = command.strip().strip('"').strip("'")
    stripped = stripped.replace('"', "").replace("'", "")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stripped).strip("_")
    if not slug:
        digest = hashlib.sha1(command.encode("utf-8")).hexdigest()[:8]
        slug = f"cmd_{digest}"
    return slug


def derive_placeholder_for_command(command: str) -> str:
    return f"__{slugify_command(command).upper()}_SCRIPT__"


def looks_like_command(text: str) -> bool:
    """True if the input is a shell command rather than a script filename.

    A bare script filename is a single token with no whitespace; anything
    containing whitespace is taken to be a command. This is deliberately
    simple. A command with no arguments (e.g. just `kritomatic`) would be
    misdetected as a filename; add an argument or use the explicit
    filename form to disambiguate.
    """
    return any(c.isspace() for c in text.strip())


# ---------------------------------------------------------------------------
# Input mode
# ---------------------------------------------------------------------------

def prompt_for_input() -> str:
    try:
        return input("Script name or command: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit("ERROR: input cancelled")


# ---------------------------------------------------------------------------
# Bash-command persistence
# ---------------------------------------------------------------------------

def save_bash_command(command: str, merger_path: Path) -> str:
    """Write `command` as a minimal bash script under scripts/bash_commands/.

    Returns the path relative to the scripts/ directory, which is the
    form the SCRIPTS dict stores (its values are resolved as
    scripts/<value> by merger.py's expand_keywords).
    """
    scripts_dir = merger_path.parent / "scripts"
    cmd_dir = scripts_dir / BASH_COMMANDS_DIR
    cmd_dir.mkdir(parents=True, exist_ok=True)

    slug = slugify_command(command)
    script_path = cmd_dir / f"{slug}.sh"

    body = (
        "#!/usr/bin/env bash\n"
        "# Auto-generated by inject_script.py\n"
        "# Source command:\n"
        f"#   {command}\n"
        "\n"
        f"{command}\n"
    )
    script_path.write_text(body, encoding="utf-8")
    script_path.chmod(0o755)

    return f"{BASH_COMMANDS_DIR}/{slug}.sh"


# ---------------------------------------------------------------------------
# merger.py editing
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# YAML editing
# ---------------------------------------------------------------------------

def yaml_path_for(merger_path: Path) -> Path:
    return merger_path.parent / YAML_FILENAME


def find_section_bounds(lines: list[str], section_name: str):
    """Return (start_idx, end_idx) for the block whose `- name:` line
    matches `section_name`.

    start_idx is the index of the `  - name: ...` header line.
    end_idx is the index of the first line after that block that begins
    a new top-level list item (`  - `) or is unindented. If the block is
    the last one in the file, end_idx == len(lines).

    Returns None if the section is not found.
    """
    start_idx = None
    for i, line in enumerate(lines):
        if re.match(r'^  - name:\s*', line) and section_name in line:
            start_idx = i
            break
    if start_idx is None:
        return None

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if re.match(r'^  - ', lines[i]) or re.match(r'^[^\s#]', lines[i]):
            end_idx = i
            break
    return (start_idx, end_idx)


def insertion_point(lines: list[str], bounds: tuple[int, int]) -> int:
    """Where in `lines` the new block should be inserted so it sits right
    after the last content line of the target section.

    Walks back from the section boundary over trailing blank lines and
    comment lines, so the stub lands immediately after the last real
    entry — not floating in the visual gap that precedes the next block.
    """
    start_idx, end_idx = bounds
    insert_at = end_idx
    while insert_at > start_idx + 1:
        prev = lines[insert_at - 1]
        if prev.strip() == "" or prev.lstrip().startswith("#"):
            insert_at -= 1
        else:
            break
    return insert_at


def build_yaml_stub(placeholder: str, launcher: str) -> list[str]:
    """Two commented lines, indented to match a remap key, using `KEY` as
    the placeholder key name."""
    return [
        f"{YAML_INDENT}# KEY:\n",
        f'{YAML_INDENT}#   launch: ["{launcher}", "{placeholder}"]\n',
    ]


def yaml_contains(yaml_path: Path, needle: str) -> bool:
    if not yaml_path.is_file():
        return False
    return needle in yaml_path.read_text(encoding="utf-8")


def insert_yaml_stub(yaml_path: Path, placeholder: str, launcher: str) -> bool:
    """Insert the commented stub into the target section.

    Idempotent: if the placeholder already appears anywhere in the file —
    whether as our commented stub, or as an active binding the user has
    since uncommented — the insertion is skipped. This makes re-running
    the injector safe and also means that once you've wired the key up,
    the injector will not step on your edit.
    """
    if not yaml_path.is_file():
        print(f"WARNING: {yaml_path.name} not found; skipping YAML insertion",
              file=sys.stderr)
        return False

    if yaml_contains(yaml_path, placeholder):
        print(f"no YAML change: {placeholder} already present in {yaml_path.name}")
        return False

    source = yaml_path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)

    bounds = find_section_bounds(lines, YAML_TARGET_SECTION)
    if bounds is None:
        print(f"WARNING: section '{YAML_TARGET_SECTION}' not found in "
              f"{yaml_path.name}; skipping YAML insertion", file=sys.stderr)
        return False

    insert_at = insertion_point(lines, bounds)
    stub = build_yaml_stub(placeholder, launcher)
    new_lines = lines[:insert_at] + stub + lines[insert_at:]
    yaml_path.write_text("".join(new_lines), encoding="utf-8")
    print(f"✓ inserted commented stub in {yaml_path.name} "
          f"(section '{YAML_TARGET_SECTION}')")
    return True


def remove_yaml_stub(yaml_path: Path, placeholder: str) -> bool:
    """Remove the commented stub for `placeholder`, if present.

    Only commented lines are touched. If the user has uncommented the
    binding and set a real key, that line is left alone — once they've
    committed to a key, the injector no longer owns that line.
    """
    if not yaml_path.is_file():
        return False

    source = yaml_path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)

    new_lines: list[str] = []
    removed = False

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#") and placeholder in line:
            # Drop the sibling `# KEY:` header above, if it is the most
            # recent line we kept and it matches the stub shape. This is
            # the only structural assumption we make: the two lines of a
            # stub are written together and removed together. Anything
            # else above — a different stub's launch line, a section
            # comment, blank lines — fails this check and is preserved.
            if (new_lines
                    and re.match(r'^\s*#\s*KEY:\s*$', new_lines[-1])):
                new_lines.pop()
            removed = True
            continue
        new_lines.append(line)

    if removed:
        yaml_path.write_text("".join(new_lines), encoding="utf-8")
        print(f"✓ removed commented stub from {yaml_path.name}")
    return removed


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add or remove a script entry in merger.py's SCRIPTS dict, "
                    "and drop a commented binding stub into "
                    "kritomatic_xremap.yaml.",
    )
    parser.add_argument(
        "input", nargs="?", default=None,
        help="Script filename or shell command. If omitted, the script "
             "prompts for one interactively.",
    )
    parser.add_argument(
        "--remove", action="store_true",
        help="Remove the entry (and any commented YAML stub) instead of adding.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List current SCRIPTS entries and exit.",
    )
    parser.add_argument(
        "--no-yaml", action="store_true",
        help="Skip the YAML stub insertion / removal. Use when you only "
             "want merger.py touched.",
    )
    parser.add_argument(
        "--merger", type=Path, default=None,
        help="Path to merger.py (default: auto-detect).",
    )
    args = parser.parse_args()

    merger_path = find_merger(args.merger)
    yaml_path = yaml_path_for(merger_path)

    if args.list:
        source = merger_path.read_text(encoding="utf-8")
        for placeholder, filename in read_items(find_scripts_node(source)):
            print(f"{placeholder:<40s} scripts/{filename}")
        return 0

    # Resolve the raw input: positional argument, or interactive prompt.
    raw = args.input if args.input else prompt_for_input()
    if not raw:
        sys.exit("ERROR: no input provided")

    # Classify the input and derive the SCRIPTS entry shape.
    is_command = looks_like_command(raw)
    if is_command:
        relative_path = f"{BASH_COMMANDS_DIR}/{slugify_command(raw)}.sh"
        placeholder = derive_placeholder_for_command(raw)
        yaml_launcher = "bash"
    else:
        relative_path = raw
        placeholder = derive_placeholder(raw)
        yaml_launcher = "python"

    source = merger_path.read_text(encoding="utf-8")
    items = read_items(find_scripts_node(source))
    existing = dict(items)

    # ---- remove ----
    if args.remove:
        if placeholder not in existing:
            sys.exit(f"ERROR: {placeholder} not present in SCRIPTS")
        items = [(p, f) for p, f in items if p != placeholder]
        rewrite_merger(merger_path, items)
        print(f"✓ removed {placeholder}")

        if is_command:
            script_file = merger_path.parent / "scripts" / relative_path
            if script_file.is_file():
                script_file.unlink()
                print(f"✓ removed scripts/{relative_path}")

        if not args.no_yaml:
            remove_yaml_stub(yaml_path, placeholder)
        return 0

    # ---- add ----
    if is_command:
        save_bash_command(raw, merger_path)
        print(f"✓ saved command as scripts/{relative_path}")
    else:
        if not (merger_path.parent / "scripts" / relative_path).is_file():
            print(f"WARNING: scripts/{relative_path} does not exist yet",
                  file=sys.stderr)

    # Rewrite merger.py only if the SCRIPTS entry actually changes.
    # Falling through (rather than returning early) lets the YAML stub
    # still be inserted on a re-run, which is what you want if you added
    # the SCRIPTS entry before this YAML feature existed.
    if placeholder in existing and existing[placeholder] == relative_path:
        print(f"no SCRIPTS change: {placeholder} already maps to {relative_path}")
    else:
        if placeholder in existing:
            items = [(p, relative_path if p == placeholder else f)
                     for p, f in items]
            message = (f"updated {placeholder}: "
                       f"{existing[placeholder]} -> {relative_path}")
        else:
            items.append((placeholder, relative_path))
            message = f"added {placeholder} -> {relative_path}"
        rewrite_merger(merger_path, items)
        print(f"✓ {message}")

    # YAML stub.
    if not args.no_yaml:
        insert_yaml_stub(yaml_path, placeholder, yaml_launcher)

    print()
    print("YAML binding for kritomatic_xremap.yaml:")
    print("  KEY:")
    print(f'    launch: ["{yaml_launcher}", "{placeholder}"]')

    return 0


if __name__ == "__main__":
    sys.exit(main())
