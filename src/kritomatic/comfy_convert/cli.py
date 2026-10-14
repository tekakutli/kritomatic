"""
cli.py — convert a ComfyUI UI-format workflow to API format.

Conversion is performed by comfy-cli, the official ComfyUI command-line
tool maintained by Comfy-Org. `comfy run --workflow <path>` accepts a
UI-format workflow and converts it to API format automatically; adding
`--print-prompt` makes it print the converted API JSON to stdout
instead of submitting it to a running ComfyUI server.

This replaces two previous approaches:

  * POSTing to the comfyui-workflow-to-api-converter-endpoint custom
    node. That endpoint had a bug in its bypassed-node handling: for a
    bypassed node with two or more outputs of the same type (e.g.
    ControlNetApplyAdvanced with two CONDITIONING outputs), it resolved
    every output slot to the same input, collapsing positive and
    negative conditioning onto the same chain.

  * ComfyScript's transpiler. ComfyScript (maintained by Chaoses-Ib,
    not by Comfy-Org) cannot represent UI-only virtual nodes such as
    `Bookmark (rgthree)`, `Note`, or `PrimitiveNode`, and its error
    message explicitly tells the user to export API format first —
    which is the problem we are trying to solve.

comfy-cli is part of the same project that builds the ComfyUI frontend,
so its converter inherits the frontend's serialization semantics,
including virtual-node stripping. It is the authoritative UI→API
conversion outside the browser.

The handle annotation step (replacing user-supplied inputs with
{{handle_id}} tokens and recording the spec in _meta.handles) is
unchanged; it operates on the API dict after conversion.

Output lands under <repo>/data/convert/, sibling of the panel's
data/comfy_panel/. The filename mirrors the source JSON exactly, so
a source at <anywhere>/foo.json becomes data/convert/foo.json.

The panel CLI uses `convert_if_ui()` to convert a UI-format workflow
in place before handing it to the daemon, so `kritomatic comfyui
panel run /path/to/foo.json` accepts a UI-format file directly. The
standalone `kritomatic comfyui convert` subcommand exposes the same
conversion for direct use.

Dependency resolution
=====================
`comfy-cli` is a separate pip package (`comfy-cli`) that provides the
`comfy` console script. It is installed into the repo's `.venv`
alongside `kritomatic`. Because `kritomatic` may be launched by a pipx
shim or a system Python whose PATH does not include the repo venv's
`bin/`, this module locates the repo venv's `comfy` executable
directly rather than relying on PATH lookup.

If the repo venv is absent (a pipx-only install with no checkout), the
module falls back to whatever `comfy` is on PATH.

Parsing comfy-cli's output
==========================
`comfy run --print-prompt` writes more than the prompt to stdout. Node-
loading warnings ("Node already exists", "Invalid enum value", etc.)
may appear before the JSON, and the JSON itself may be wrapped — the
prompt under a `prompt` key alongside other metadata, or some similar
shape that differs across comfy-cli releases.

Rather than assume a fixed position or a fixed wrapper, the parser
scans for every balanced top-level JSON object in stdout, parses each
candidate, and accepts the first one that either *is* an API graph
(every value a dict with a `class_type`) or *contains* one under a
common wrapper key. This is robust against the shape changing between
releases, and it never mistakes a warning blob for the graph.

Requirements:
  * comfy-cli in the repo venv:
        <repo>/.venv/bin/pip install comfy-cli
  * A running ComfyUI server is NOT required for the conversion itself.
    `--print-prompt` exits after printing; it does not submit anything.

The `--url` argument passed to `kritomatic comfyui convert` is
currently unused by the comfy-cli path. It is retained in the CLI
surface so existing invocations do not break; the conversion is a pure
data transformation and needs no server address.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from kritomatic.helpful_argparse import HelpfulArgumentParser


# ============================================================================
#  Paths
# ============================================================================

# <repo>/src/kritomatic/comfy_convert/cli.py
#   .parent                     → .../comfy_convert
#   .parent.parent              → .../kritomatic
#   .parent.parent.parent       → .../src
#   .parent.parent.parent.parent → <repo>
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CONVERT_DIR = _REPO_ROOT / "data" / "convert"

_REPO_VENV = _REPO_ROOT / ".venv"

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"


# ============================================================================
#  Handle configuration
# ============================================================================
#
# Unchanged from the endpoint-based version. Extend these dicts to
# support more node types.

HANDLE_FIELDS = {
    "LoadImage": ["image"],
    "ttN text": ["text"],
}

HANDLE_TYPES = {
    ("LoadImage", "image"): "image_filename",
    ("ttN text", "text"): "string",
}


class ConversionError(Exception):
    """Any failure while converting or invoking comfy-cli."""


# ============================================================================
#  comfy-cli discovery
# ============================================================================

def _find_comfy_executable() -> Optional[str]:
    """Locate the `comfy` console script.

    Search order:

      1. The repo venv's bin/ directory. This is where `pip install
         comfy-cli` lands when run with the venv activated. Preferred
         because it is the venv kritomatic itself is installed into,
         so the two are guaranteed to share a Python.

      2. Whatever `comfy` is on PATH. Used when the repo venv does not
         exist (pipx-only install) or when comfy-cli was installed
         globally.

    Returns None if neither yields an executable.
    """
    # Repo venv, POSIX.
    venv_comfy = _REPO_VENV / "bin" / "comfy"
    if venv_comfy.is_file() and os.access(venv_comfy, os.X_OK):
        return str(venv_comfy)

    # Repo venv, Windows.
    venv_comfy_win = _REPO_VENV / "Scripts" / "comfy.exe"
    if venv_comfy_win.is_file():
        return str(venv_comfy_win)

    # PATH.
    on_path = shutil.which("comfy")
    if on_path:
        return on_path

    return None


def _describe_missing_comfy() -> str:
    """Message shown when the `comfy` executable cannot be found."""
    lines = [
        "The 'comfy' command is required for UI→API conversion but was",
        "not found.",
        "",
        "Searched:",
        f"  repo venv: {_REPO_VENV / 'bin' / 'comfy'}",
        f"  PATH:      (no 'comfy' executable)",
        "",
        "Install comfy-cli into the repo venv:",
        "",
        f"    {_REPO_VENV}/bin/pip install comfy-cli",
        "",
        "Or install it globally if you prefer:",
        "",
        "    pip install comfy-cli",
    ]
    return "\n".join(lines)


# ============================================================================
#  JSON extraction from mixed stdout
# ============================================================================

def _extract_json_candidates(raw: str) -> list[str]:
    """Return every JSON object substring in `raw` that parses cleanly.

    Uses `json.JSONDecoder.raw_decode` at each `{` position instead of
    a hand-rolled brace counter. The reason is that brace counting
    has to re-derive every rule of JSON's lexer as it scans:

      * braces inside string literals do not nest,
      * a backslash-escaped quote inside a string does not terminate
        the string,
      * an unterminated string is a parse error, not a nesting level,

    and every one of those is a chance to get the count wrong. The
    failure mode is silent — the scan produces a substring that
    *looks* like JSON, `json.loads` rejects it, and the caller reports
    "no API graph found" with a preview that clearly contains one.

    `raw_decode` already knows the rules. The scan here is:

      1. Find the next `{` at or after the current position.
      2. Ask `raw_decode` to parse a JSON value starting there.
      3. On success, record the exact substring that was consumed and
         resume the scan at the position just past its closing brace.
      4. On failure, advance one character and retry from step 1.

    Two consequences are worth naming:

      * A `{` that sits inside a string literal in the surrounding
        text is tried like any other. `raw_decode` typically rejects
        it (the text after it will not parse as JSON) and the scan
        moves on. If it happens to succeed — a pathological input
        where the warning text itself contains a small valid JSON
        object embedded in a quoted string — that object becomes a
        candidate. `_find_api_graph` filters it out on shape, so a
        false positive here costs nothing beyond one parse attempt.

      * Overlapping candidates are not produced. Once `raw_decode`
        succeeds at position `i`, the scan resumes past the end of
        the object it parsed, so a nested object inside an already-
        captured one is never returned as a separate candidate.

    Every returned string is guaranteed to parse as JSON; the caller
    does not need to re-check.
    """
    decoder = json.JSONDecoder()
    candidates: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        start = raw.find("{", i)
        if start == -1:
            break
        try:
            _obj, end = decoder.raw_decode(raw, start)
        except json.JSONDecodeError:
            # Not valid JSON starting here. Advance one character and
            # let the next iteration find the next `{`. This is what
            # makes a stray `{` in warning text harmless.
            i = start + 1
            continue
        candidates.append(raw[start:end])
        i = end
    return candidates


def _looks_like_api_graph(obj) -> bool:
    """True iff `obj` matches ComfyUI's API-graph shape.

    The shape is: a non-empty dict whose every value is a dict that
    carries a `class_type` key. Any deviation — a string value, a
    nested list, an entry without `class_type` — disqualifies the
    object. An empty dict is disqualified too: there is nothing to
    convert and the caller should not treat it as a valid graph.
    """
    if not isinstance(obj, dict) or not obj:
        return False
    for value in obj.values():
        if not isinstance(value, dict):
            return False
        if "class_type" not in value:
            return False
    return True


def _find_api_graph(obj) -> Optional[dict]:
    """Locate the API graph inside a parsed JSON structure.

    Returns the structure itself if it is already an API graph.
    Otherwise looks one level deep under common wrapper keys, then
    recurses into dict values and list items. The recursion is
    depth-first; the first match wins.

    The wrapper-key list is a guess at what comfy-cli might name the
    field holding the prompt. If a future comfy-cli release uses a
    different name, the recursion still finds it — the named keys
    just short-circuit a full traversal.
    """
    if _looks_like_api_graph(obj):
        return obj

    if isinstance(obj, dict):
        for key in ("prompt", "api", "workflow", "graph"):
            sub = obj.get(key)
            if _looks_like_api_graph(sub):
                return sub
        for value in obj.values():
            found = _find_api_graph(value)
            if found is not None:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _find_api_graph(item)
            if found is not None:
                return found

    return None


def _parse_api_graph_from_stdout(raw: str) -> dict:
    """Find and parse the API graph in comfy-cli's stdout.

    Tries every balanced top-level object in order. Returns the first
    whose parsed form resolves to an API graph. Raises ConversionError
    with a preview of the raw output if none do.
    """
    candidates = _extract_json_candidates(raw)

    if not candidates:
        raise ConversionError(
            f"comfy-cli produced no JSON object on stdout.\n"
            f"stdout preview: {raw[:600]!r}"
        )

    parse_errors: list[str] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as e:
            parse_errors.append(str(e))
            continue

        api = _find_api_graph(parsed)
        if api is not None:
            return api

    # Nothing matched. Report what we saw so the user (or a future
    # maintainer) can see the shape and extend the matcher.
    shapes: list[str] = []
    for candidate in candidates[:3]:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                keys = list(parsed.keys())[:12]
                shapes.append(f"dict with keys {keys}")
            elif isinstance(parsed, list):
                shapes.append(f"list of {len(parsed)} item(s)")
            else:
                shapes.append(type(parsed).__name__)
        except json.JSONDecodeError:
            shapes.append("(unparseable)")

    detail_lines = [
        "comfy-cli produced JSON, but none of the objects found match",
        "the ComfyUI API-graph shape (a dict whose every value is a",
        "dict carrying 'class_type').",
        "",
        f"Candidates found: {len(candidates)}",
    ]
    for i, shape in enumerate(shapes):
        detail_lines.append(f"  [{i}] {shape}")
    if parse_errors:
        detail_lines.append("")
        detail_lines.append(
            f"Parse errors on first candidates: {parse_errors[:3]}"
        )
    detail_lines.append("")
    detail_lines.append(f"Raw stdout preview: {raw[:600]!r}")

    raise ConversionError("\n".join(detail_lines))


# ============================================================================
#  comfy-cli invocation
# ============================================================================

def _run_comfy_print_prompt(workflow_path: Path) -> dict:
    """Run `comfy run --workflow <path> --print-prompt` and return the
    API graph parsed from its stdout.

    Raises ConversionError with the subprocess output attached when
    the command fails, the executable is missing, or stdout does not
    contain a recognizable API graph.
    """
    comfy = _find_comfy_executable()
    if comfy is None:
        raise ConversionError(_describe_missing_comfy())

    argv = [
        comfy,
        "run",
        "--workflow", str(workflow_path),
        "--print-prompt",
    ]

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except FileNotFoundError as e:
        raise ConversionError(
            f"Could not execute {comfy}: {e}\n"
            f"Run `{_REPO_VENV}/bin/pip install comfy-cli` to install it."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ConversionError(
            f"comfy-cli did not finish within 120 seconds.\n"
            f"Workflow: {workflow_path}"
        ) from e
    except OSError as e:
        raise ConversionError(
            f"Could not run {comfy}: {e}"
        ) from e

    if result.returncode != 0:
        stderr_snippet = (result.stderr or "").strip()
        stdout_snippet = (result.stdout or "").strip()
        detail = stderr_snippet or stdout_snippet or "(no output)"
        raise ConversionError(
            f"comfy-cli exited with code {result.returncode}.\n"
            f"Command: {' '.join(argv)}\n"
            f"Output:\n{detail}"
        )

    return _parse_api_graph_from_stdout(result.stdout or "")


# ============================================================================
#  Format detection
# ============================================================================

def is_ui_format(workflow: object) -> bool:
    """True iff the JSON looks like a ComfyUI UI-format workflow.

    The UI format has a top-level `nodes` array and a top-level
    `links` array. The API format is a dict keyed by node id, each
    value carrying `class_type` and `inputs`. The two never overlap:
    an API workflow has no `nodes`/`links` keys, and a UI workflow's
    top level carries neither `class_type` nor `inputs`.
    """
    if not isinstance(workflow, dict):
        return False
    return "nodes" in workflow or "links" in workflow


# ============================================================================
#  Handle annotation
# ============================================================================

def _slug(s: str) -> str:
    """Lowercase, replace any non-alphanumeric run with '_', trim '_'."""
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def _make_handle_id(node_id: str, node: dict, field: str,
                    scheme: str) -> str:
    cls = node["class_type"]
    if scheme == "auto":
        return f"{_slug(cls)}_{field}_{node_id}"
    if scheme == "title":
        title = node.get("_meta", {}).get("title", cls)
        return f"{_slug(title)}_{field}"
    if scheme == "field":
        return field
    raise ConversionError(f"Unknown id scheme: {scheme}")


def annotate_handles(api: dict,
                     scheme: str = "auto",
                     ) -> tuple[dict, list[dict]]:
    """Replace handle fields with {{id}} tokens, record the spec.

    Defensive against a malformed `api`: any node whose value is not
    a dict is skipped rather than raising. The upstream caller is
    expected to have validated the graph shape, but a stray non-node
    entry in a wrapper should not crash the annotation.

    Returns (annotated_api, records), where records is one dict per
    injected handle, in graph iteration order. That list is what the
    CLI's report prints.
    """
    seen: dict[str, str] = {}
    records: list[dict] = []

    for node_id, node in api.items():
        if not isinstance(node, dict):
            continue

        fields = HANDLE_FIELDS.get(node.get("class_type"))
        if not fields:
            continue

        meta = node.setdefault("_meta", {})
        handles = meta.setdefault("handles", {})

        for field in fields:
            hid = _make_handle_id(node_id, node, field, scheme)
            if hid in seen:
                raise ConversionError(
                    f"handle id collision — {hid!r} is claimed by both "
                    f"node {seen[hid]} and node {node_id}. "
                    f"Try --id-scheme auto."
                )
            seen[hid] = node_id

            token = "{{" + hid + "}}"
            node.setdefault("inputs", {})[field] = token

            htype = HANDLE_TYPES.get(
                (node["class_type"], field), "string"
            )
            handles[field] = {
                "id": hid,
                "type": htype,
                "token": token,
                "required": True,
                "description": f"{node['class_type']}.{field}",
            }
            records.append({
                "id": hid,
                "type": htype,
                "token": token,
                "node_id": node_id,
                "field": field,
                "class_type": node["class_type"],
            })

    return api, records


# ============================================================================
#  Core conversion
# ============================================================================

def convert_workflow(source_path: Path,
                     output_path: Path,
                     *,
                     url: str = DEFAULT_COMFY_URL,
                     id_scheme: str = "auto",
                     ) -> tuple[Path, list[dict]]:
    """Read a UI-format workflow, convert it, annotate handles, save.

    The `url` parameter is accepted for backward compatibility with
    callers that pass it (the CLI still exposes `--url`), but it is
    not used: comfy-cli's `--print-prompt` conversion is a pure data
    transformation and needs no server address.

    `source_path` must exist and be UI-format JSON. `output_path` is
    where the converted file is written; its directory is created if
    needed. Returns (output_path, handle_records).

    Raises ConversionError on any failure. The caller decides how to
    surface it.
    """
    try:
        workflow = json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConversionError(f"Could not read {source_path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ConversionError(
            f"{source_path} is not valid JSON: {e}"
        ) from e

    if not is_ui_format(workflow):
        raise ConversionError(
            f"{source_path} is not in ComfyUI UI format; nothing to "
            f"convert."
        )

    api = _run_comfy_print_prompt(source_path)
    api, records = annotate_handles(api, scheme=id_scheme)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(api, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return output_path, records


def default_output_dir() -> Path:
    """The directory converted files land in by default."""
    CONVERT_DIR.mkdir(parents=True, exist_ok=True)
    return CONVERT_DIR


# ============================================================================
#  Panel integration
# ============================================================================

def convert_if_ui(source_path: Path,
                  *,
                  url: str = DEFAULT_COMFY_URL,
                  id_scheme: str = "auto",
                  ) -> tuple[Path, list[dict], bool]:
    """Return the API-format path for `source_path`, converting if needed.

    Behaviour:

      * If `source_path` is already API format, returns it unchanged:
        ``(source_path, [], False)``. No subprocess is spawned.

      * If it is UI format, the workflow is converted and the result
        is written to ``<repo>/data/convert/<source_name>``. The file
        is overwritten on each call, so a source edited between runs
        produces a fresh conversion. Returns
        ``(converted_path, records, True)``.

    Raises ConversionError when the file cannot be read or the
    conversion fails. The caller decides whether that is fatal. The
    panel CLI treats it as fatal — the alternative (falling through
    to a daemon-side load of the UI file) fails later with a less
    helpful message.
    """
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ConversionError(f"Could not read {source_path}: {e}") from e
    except json.JSONDecodeError as e:
        raise ConversionError(
            f"{source_path} is not valid JSON: {e}"
        ) from e

    if not is_ui_format(data):
        return source_path, [], False

    out_path = default_output_dir() / source_path.name
    converted, records = convert_workflow(
        source_path, out_path, url=url, id_scheme=id_scheme,
    )
    return converted, records, True


# ============================================================================
#  CLI
# ============================================================================

def populate_convert_args(parser) -> None:
    """Add convert's args to `parser`.

    Shared by run_convert_command (the runtime parser) and by
    registry.py (which builds the same shape for `kritomatic --help`
    and `kritomatic comfyui --help`). Declaring them once keeps the
    two paths in agreement.
    """
    parser.add_argument(
        'input', type=Path,
        help='Path to the UI-format workflow JSON.',
    )
    parser.add_argument(
        '-o', '--output', type=Path, default=None,
        help=(f'Output file or directory. Default: '
              f'{CONVERT_DIR}/<input_filename>.'),
    )
    parser.add_argument(
        '--url', default=DEFAULT_COMFY_URL,
        help=(f'Base URL of the ComfyUI server (default: '
              f'{DEFAULT_COMFY_URL}). Retained for compatibility; '
              f'comfy-cli\'s --print-prompt conversion does not use '
              f'it.'),
    )
    parser.add_argument(
        '--id-scheme', choices=['auto', 'title', 'field'],
        default='auto',
        help=('How handle ids are generated: '
              'auto = <class>_<field>_<node_id> (default); '
              'title = <slug(node title)>_<field>; field = <field>.'),
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help='Print only the final output path.',
    )


def _build_parser() -> HelpfulArgumentParser:
    p = HelpfulArgumentParser(
        prog='kritomatic comfyui convert',
        description=(
            "Convert a ComfyUI UI-format workflow to API format "
            "using comfy-cli, inject {{handle_id}} tokens for "
            f"user-supplied inputs, and save the result to "
            f"{CONVERT_DIR}/."
        ),
    )
    populate_convert_args(p)
    return p


def _resolve_output_path(input_path: Path,
                         user_output: Optional[Path],
                         ) -> Path:
    input_name = input_path.name

    if user_output is None:
        return default_output_dir() / input_name

    user_output = Path(user_output)

    if user_output.suffix == "":
        user_output.mkdir(parents=True, exist_ok=True)
        return user_output / input_name

    user_output.parent.mkdir(parents=True, exist_ok=True)
    return user_output


def _print_report(records: list[dict], out_path: Path) -> None:
    print(f"  Output   {out_path}")
    print()

    if not records:
        print("  No user-supplied inputs — ready to run as-is.")
        return

    n = len(records)
    plural = "" if n == 1 else "s"
    print(f"  {n} handle{plural} to fill:")
    print()

    for r in records:
        print(f'    "{r["token"]}"')
        print(f"      type   {r['type']}")
        print(f"      node   {r['node_id']}.{r['field']}")
        print()


def run_convert_command() -> int:
    """Entry point for `kritomatic comfyui convert`.

    Invoked from main.py when `sys.argv[1:3] == ['comfyui',
    'convert']`. Parses `sys.argv[3:]`.
    """
    parser = _build_parser()
    args = parser.parse_args(sys.argv[3:])

    if not args.input.is_file():
        print(f"Error: input file not found: {args.input}",
              file=sys.stderr)
        return 1

    out_path = _resolve_output_path(args.input, args.output)

    if not args.quiet:
        print(f"Converting {args.input} ...", flush=True)

    try:
        _, records = convert_workflow(
            args.input, out_path,
            url=args.url, id_scheme=args.id_scheme,
        )
    except ConversionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.quiet:
        print(out_path)
        return 0

    print()
    _print_report(records, out_path)
    return 0
