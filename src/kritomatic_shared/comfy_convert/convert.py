"""
convert.py — convert a ComfyUI UI-format workflow to API format.

The algorithm, extracted from what used to be `src/kritomatic/
comfy_convert/cli.py`. Two callers, both here for the same reason:

  * the CLI's `kritomatic comfyui convert` subcommand;
  * the daemon's `comfy_load`, which auto-converts on UI-format input
    (see `kritomatic_daemon/handlers/comfyui_workflow.py`).

Neither owns the algorithm. The daemon reaches this module via a
`sys.path` bootstrap in `kritomatic_daemon/utils/comfy_convert.py`;
the CLI reaches it via pip install (`find_packages(where='src')`
picks up `src/kritomatic_shared/` automatically). Same source files
either way.

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

CORRECTION PASSES
=================
`comfy-cli run --workflow <path> --print-prompt` maps
`widgets_values` POSITIONALLY against the current node schema.  When
a node's widget schema has changed since the workflow was authored
— a widget added, removed, or reordered — every value after the
change point lands in the wrong input slot.  The classic symptom is
a stale string value dropped into a newly-introduced INT field, so
ComfyUI rejects the prompt at validation time with "Failed to
convert an input value to a INT value: <field>, <value>".

Two correction passes run against comfy-cli's output before
annotation:

  1. `_apply_ui_widget_overrides` — for each API node, prefer the
     UI workflow's `widgets_values_named` map (keyed by input name,
     immune to positional drift) over `comfy-cli`'s positional
     assignment.  Fields absent from the named map are left as
     comfy-cli produced them.

  2. `_apply_schema_validation` — fetch ComfyUI's `/object_info`
     and replace any remaining *type*-mismatched widget value with
     the schema's default.  Catches the case (1) cannot: an input
     that did not exist when the workflow was authored, and so is
     absent from `widgets_values_named`, but now has a stale
     positionally-mapped value in the API output.

Pass (2) fixes type mismatches only.  It deliberately does NOT
replace COMBO values whose scalar choice is not present in the
choices list.  Two important classes live there:

  · `@`-prefixed daemon resource markers (`@thecanvas`,
    `@theselection`, `@thecontext`).  `comfy-cli` preserves the
    string verbatim; the daemon's `prepare_workflow` substitutes a
    `core/ASSET` reference before ComfyUI sees it.  Replacing these
    with the schema default would hide them from the daemon's
    resource-slot extraction, leaving the workflow with no assets
    and the docker with no Resources section.

  · Model or filename values that were selected when the workflow
    was authored but are not installed on the current ComfyUI.  The
    user selected those values; silently replacing them with a
    different installed file would produce a run the user did not
    ask for.  ComfyUI's own prompt validation reports missing
    choices with a clear error at submit time.

The converter's job is to not corrupt what `comfy-cli` produced, not
to be a validator.  Type-level repair is safe and clearly bounded;
choice-level repair is not.

Pass (2) is best-effort: `url` must be reachable for the
`/object_info` fetch to succeed.  When it isn't, the conversion
completes with a warning and pass (1)'s corrections only;
re-converting with ComfyUI running applies (2).  Bumping
`CONVERTER_VERSION` invalidates every cached conversion, so
existing sidecars do not mask this fix.

Two callers, two shapes of the output graph
===========================================
`convert_workflow` writes the API graph in one of two forms, chosen
by the `annotate` flag:

  annotate=True  (the CLI default)
      Every `LoadImage` / `LoadImageMask` input is replaced with a
      `{{handle_id}}` token and the original value is recorded under
      `_meta.handles`. The output is a *template*: a human fills the
      handles in before submitting. This is what
      `kritomatic comfyui convert` produces.

  annotate=False  (the daemon's auto-conversion path)
      Inputs are left verbatim. In particular, `@`-prefixed resource
      references (`@thecanvas`, `@theselection`, `@thecontext`) and
      hardcoded filenames (`116129138_p2_master1200.jpg`) survive
      unchanged. The daemon does its own substitution at
      `prepare_workflow` time — `@`-refs become `core/ASSET`
      references after reading pixels from Krita; hardcoded filenames
      are left for ComfyUI to resolve against its own `input/`
      directory.

The distinction matters. `annotate_handles` replaces the *entire*
value of the handle field, which would hide `@`-prefixes from the
daemon's `_extract_resource_slots` (it looks for the `@` character
specifically) and leave the daemon with nothing to substitute — the
`{{token}}` would reach ComfyUI verbatim and it would reject the
prompt. `annotate_handles` also explicitly skips `@`-prefixed inputs
so the CLI's annotated output remains consistent: an `@`-prefixed
input in a UI file is already a "handle" of a different kind, one
that only the daemon understands.

Output lands under <repo>/data/convert/, sibling of the panel's
data/comfy_panel/. The filename mirrors the source JSON exactly, so
a source at <anywhere>/foo.json becomes data/convert/foo.json.

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

The `--url` parameter on `convert_workflow` / `convert_if_ui` is
used by the schema-validation pass to fetch `/object_info`.
Passing `url=""` disables it explicitly; otherwise an unreachable
server is a soft failure (logged, conversion proceeds without
schema validation).

Requirements:
  * comfy-cli in the repo venv:
        <repo>/.venv/bin/pip install comfy-cli
  * A running ComfyUI server is NOT required for the conversion
    itself.  `--print-prompt` exits after printing; it does not
    submit anything.  Only the schema-validation pass queries
    ComfyUI, and it degrades gracefully when ComfyUI is down.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


# ============================================================================
#  Paths
# ============================================================================

# <repo>/src/kritomatic_shared/comfy_convert/convert.py
#   .parent                          → .../comfy_convert
#   .parent.parent                   → .../kritomatic_shared
#   .parent.parent.parent            → .../src
#   .parent.parent.parent.parent     → <repo>
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CONVERT_DIR = _REPO_ROOT / "data" / "convert"

_REPO_VENV = _REPO_ROOT / ".venv"

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"


# ============================================================================
#  Handle configuration
# ============================================================================
#
# Extend these dicts to support more node types.

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
#  Correction passes
# ============================================================================

def _warn(msg: str) -> None:
    """Emit a non-fatal warning to stderr."""
    print(f"[convert] warning: {msg}", file=sys.stderr)


def _is_edge(value) -> bool:
    """True iff `value` looks like a ComfyUI prompt-format edge.

    Shape: two-element list, first element is a string (node id),
    second is an int (output slot).  `bool` is excluded because
    `isinstance(True, int)` is True in Python, and a two-element
    list of a str and a bool is never a real edge.

    Literal values — ints, floats, strings, bools, longer lists,
    nested lists — return False.
    """
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _collect_ui_nodes(ui_workflow: dict) -> dict:
    """Flat {api_node_id: ui_node_dict} covering top-level and one
    level of subgraph-internal nodes.

    The API graph keys nodes by `"<id>"` for top-level nodes and
    `"<subgraph_id>:<inner_id>"` for nodes inside a subgraph.  This
    map mirrors that so a single lookup handles both.

    Nested subgraphs (subgraph within subgraph) are not covered.
    Extend the `definitions.subgraphs[*].definitions.subgraphs`
    recursion if a future workflow needs them; the outer pass's
    name-override still works for top-level and single-level
    subgraph nodes, and schema validation catches type mismatches
    everywhere regardless.
    """
    out: dict = {}
    for node in ui_workflow.get("nodes", []) or []:
        nid = node.get("id")
        if nid is None:
            continue
        out[str(nid)] = node

    subgraphs = (
        (ui_workflow.get("definitions") or {}).get("subgraphs") or []
    )
    for sg in subgraphs:
        sg_id = sg.get("id")
        if not sg_id:
            continue
        for node in sg.get("nodes", []) or []:
            nid = node.get("id")
            if nid is None:
                continue
            out[f"{sg_id}:{nid}"] = node

    return out


def _apply_ui_widget_overrides(api_graph: dict, ui_nodes: dict) -> None:
    """Overwrite non-edge inputs from `widgets_values_named`.

    `comfy-cli` maps `widgets_values` positionally.  When a node's
    widget schema has changed since the workflow was authored, the
    positional mapping lands values in the wrong slots.  The UI
    workflow's `widgets_values_named` map is keyed by input name
    and so is immune to positional drift; it is authoritative for
    every field it contains.

    Only fields already present in the API node are overridden.
    Fields absent from the API node are left alone: either they
    don't exist in the current schema (re-adding them would fail
    ComfyUI validation), or `comfy-cli` dropped them for a reason
    the converter can't see.  The schema-validation pass fills in
    missing defaults for fields that *should* be present.
    """
    for api_nid, api_node in api_graph.items():
        if not isinstance(api_node, dict):
            continue
        ui_node = ui_nodes.get(str(api_nid))
        if ui_node is None:
            continue
        named = ui_node.get("widgets_values_named")
        if not isinstance(named, dict):
            continue
        inputs = api_node.get("inputs") or {}
        for field, value in named.items():
            if field not in inputs:
                continue
            if _is_edge(inputs[field]):
                continue
            inputs[field] = value
        api_node["inputs"] = inputs


def _fetch_object_info(url: str, timeout: float = 5.0) -> Optional[dict]:
    """GET ComfyUI's `/object_info`.  None on any failure."""
    if not url:
        return None
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/object_info", method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return None


def _schema_default(field):
    """Extract the default value from one `/object_info` field entry.

    Mirrors `_schema_default` in
    `kritomatic_daemon/utils/comfyui_tooling.py` but kept separate
    because the two modules sit on opposite sides of the sys.path
    bootstrap and share no import path.

    Returns None when no default can be inferred.  The caller treats
    None as "cannot recover; leave the value alone" rather than
    guessing.
    """
    if not isinstance(field, list) or not field:
        return None

    field_type = field[0]
    options = (
        field[1]
        if len(field) > 1 and isinstance(field[1], dict)
        else {}
    )

    if "default" in options:
        return options["default"]

    # Legacy combo: the type slot is itself a list of allowed values.
    if isinstance(field_type, list) and field_type:
        return field_type[0]

    if field_type == "INT":
        return 0
    if field_type == "FLOAT":
        return 0.0
    if field_type == "STRING":
        return ""
    if field_type == "BOOLEAN":
        return False
    if field_type == "COMBO":
        choices = options.get("options") or options.get("choices")
        if isinstance(choices, list) and choices:
            return choices[0]

    return None


def _matches_schema(value, field) -> bool:
    """True iff `value` is a type-valid widget value for `field`.

    Checks the *type* of the value, not choice-list membership.
    Choice membership is deliberately not checked; see the module
    docstring's CORRECTION PASSES section for why.

    In short: a scalar value in a COMBO field may be a daemon-managed
    `@`-resource marker or a model/filename the user selected but
    that isn't installed locally.  Replacing either silently would
    hide a real problem; ComfyUI's own prompt validation reports both
    clearly at submit time.  Type mismatches — a STRING in an INT
    field, a float in a BOOLEAN field, a list where a scalar is
    expected — are unambiguously wrong and safe to replace with the
    schema default.

    Range (min/max) is not checked: an out-of-range INT is ComfyUI's
    problem to clamp, and clamping it here would override a
    deliberate user setting.
    """
    if not isinstance(field, list) or not field:
        return True

    field_type = field[0]

    # Both legacy (list-of-choices) and newer ("COMBO") combo
    # encodings.  Scalar values pass; containers and None do not.
    if isinstance(field_type, list) or field_type == "COMBO":
        return not isinstance(value, (list, dict, type(None)))

    if field_type == "INT":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "FLOAT":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    if field_type == "STRING":
        return isinstance(value, str)
    if field_type == "BOOLEAN":
        return isinstance(value, bool)

    # Unknown type: be permissive.
    return True


def _apply_schema_validation(api_graph: dict,
                             object_info: dict) -> None:
    """Replace widget values that don't match their schema type.

    For each non-edge input:

      · If the value matches the schema type (and is not a
        `@`-prefixed daemon resource marker), leave it alone.
      · Otherwise replace it with the schema's default.  This is
        what fixes the `crop_padding = "http://..."` class of bug:
        the value is a stale widget mapped positionally into a slot
        whose type changed.  The schema default is always valid; a
        genuine user value of the wrong type was never going to
        execute.

    Missing inputs are left alone: ComfyUI fills them from schema
    defaults for optional widgets, or rejects the prompt for
    required ones.  Inventing values for absent fields is outside
    the converter's remit.

    Inputs whose schema default cannot be inferred (a required
    field with no default and no choice list) are left alone;
    ComfyUI's own validation error names them more usefully than
    guessing here would.
    """
    for api_node in api_graph.values():
        if not isinstance(api_node, dict):
            continue
        cls = api_node.get("class_type")
        if not isinstance(cls, str) or not cls:
            continue
        spec = object_info.get(cls)
        if not isinstance(spec, dict):
            continue
        inputs_spec = spec.get("input") or {}
        all_fields: dict = {}
        for category in ("required", "optional"):
            fields = inputs_spec.get(category) or {}
            if isinstance(fields, dict):
                all_fields.update(fields)

        inputs = api_node.get("inputs") or {}
        for name, field in all_fields.items():
            if name not in inputs:
                continue
            value = inputs[name]
            if _is_edge(value):
                continue
            # Daemon-managed resource markers.  Never replace, in
            # either COMBO or STRING slots; `prepare_workflow`
            # substitutes them before ComfyUI sees them.
            if isinstance(value, str) and value.startswith("@"):
                continue
            if _matches_schema(value, field):
                continue
            default = _schema_default(field)
            if default is None:
                continue
            _warn(
                f"{cls}.{name}: value {value!r} "
                f"(type {type(value).__name__}) does not match "
                f"schema type {field[0]!r}; replaced with schema "
                f"default {default!r}.  Fix the source workflow's "
                f"widget values or re-save it in ComfyUI to silence "
                f"this warning."
            )
            inputs[name] = default
        api_node["inputs"] = inputs


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

    Skips inputs whose current value is a `@`-prefixed resource
    reference. Those are daemon-managed: `comfy_apply_config` +
    `prepare_workflow` substitute a `core/ASSET` reference at
    generation time. Annotating them would (a) hide the `@`-prefix
    from `_extract_resource_slots`, which looks for it specifically,
    and (b) require the daemon to reverse-engineer the token back to
    a resource name at submit time. Neither is the intent of handle
    annotation.

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
        inputs = node.setdefault("inputs", {})

        for field in fields:
            existing = inputs.get(field)
            if isinstance(existing, str) and existing.startswith("@"):
                # Daemon-managed resource, not a user handle.
                continue

            hid = _make_handle_id(node_id, node, field, scheme)
            if hid in seen:
                raise ConversionError(
                    f"handle id collision — {hid!r} is claimed by both "
                    f"node {seen[hid]} and node {node_id}. "
                    f"Try --id-scheme auto."
                )
            seen[hid] = node_id

            token = "{{" + hid + "}}"
            inputs[field] = token

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
                     annotate: bool = True,
                     ) -> tuple[Path, list[dict]]:
    """Read a UI-format workflow, convert it, optionally annotate
    handles, save.

    The conversion is `comfy-cli run --workflow <path> --print-prompt`,
    which is authoritative for UI→API translation.  Two correction
    passes then run against its output before annotation:

      · `_apply_ui_widget_overrides` — prefers the UI workflow's
        `widgets_values_named` map (authoritative, keyed by input
        name) over `comfy-cli`'s positional mapping.  Fixes nodes
        whose widget schema has shifted since the workflow was
        authored.

      · `_apply_schema_validation` — fetches ComfyUI's
        `/object_info` and replaces any remaining *type*-mismatched
        widget value with the schema default.  Best-effort: skipped
        (with a warning) if ComfyUI is unreachable at conversion
        time.

    `url` is used only for the `/object_info` fetch.  Pass `url=""`
    to skip schema validation explicitly; otherwise an unreachable
    server is a soft failure and the conversion proceeds with
    pass (1)'s corrections only.

    `source_path` must exist and be UI-format JSON. `output_path` is
    where the converted file is written; its directory is created if
    needed. Returns (output_path, handle_records).

    `annotate=False` writes the raw converted graph without injecting
    `{{handle_id}}` tokens. The daemon's auto-conversion path uses
    this: it wants both `@`-prefixed resource references and hardcoded
    filenames preserved verbatim, because it does its own substitution
    at `prepare_workflow` time and ComfyUI resolves hardcoded filenames
    against its own `input/` directory. When `annotate=True` (the CLI
    default), handle records come back populated; when `False`, the
    records list is always empty.

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

    ui_nodes = _collect_ui_nodes(workflow)
    _apply_ui_widget_overrides(api, ui_nodes)

    object_info = _fetch_object_info(url) if url else None
    if object_info is not None:
        _apply_schema_validation(api, object_info)
    else:
        _warn(
            f"could not reach ComfyUI at {url!r} for schema "
            f"validation; the converted graph may still contain "
            f"stale widget values for nodes whose widget schema "
            f"has changed since the workflow was authored.  Start "
            f"ComfyUI and re-run `kritomatic comfyui convert` to "
            f"fix."
        )

    records: list[dict] = []
    if annotate:
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
#  Cache (sidecar + stat validation)
# ============================================================================
#
# Re-converting on every comfy_load is wasteful: comfy-cli takes about a
# second, and the workflow only changes when the user edits it. The
# cache lives in convert_if_ui (the "convert if needed" entry point).
# convert_workflow (the "convert now" worker) is unconditional, because
# `kritomatic comfyui convert` is an explicit request to convert and
# should never serve a cache.
#
# Validation is by sidecar, not by mtime alone. A sidecar of the form
#   data/convert/foo.json.meta.json
# records enough to prove the cached output was produced from *this*
# source under *this* contract:
#
#   - converter_version: hand-bumped integer. Bump when the parsing
#     contract in `_parse_api_graph_from_stdout` changes shape (e.g. a
#     comfy-cli release that emits a different stdout wrapper), or
#     when a correction pass is added or changed such that existing
#     cached graphs would need to be re-corrected. One integer bump
#     invalidates every cache entry at once, which is the alternative
#     to hunting down every stale output on disk.
#   - source_path: absolute. /a/foo.json and /b/foo.json share a
#     basename but must not share a cache slot, or one gets served
#     the other's graph.
#   - source_mtime, source_size: the source's identity at conversion
#     time. Recording both and requiring both to match is strictly
#     stronger than "output mtime > source mtime" — some editors
#     preserve mtime across saves, and the mtime heuristic cannot
#     catch that.
#   - annotate, id_scheme: the two flags that change the *content* of
#     the converted file. A caller asking for a different mode than
#     the cache holds must re-convert.
#   - records: handle records, so a cache hit can return them without
#     re-running annotate_handles.
#
# A missing, unreadable, or mismatched sidecar counts as a miss and
# triggers a re-convert. That is the safe default: pre-cache
# deployments have no sidecar, so their first comfy_load after an
# upgrade re-converts once, writes the sidecar, and from then on hits
# the cache.

CONVERTER_VERSION = 3   # bumped: COMBO values preserved; @-prefix skipped


def _cache_sidecar_path(out_path: Path) -> Path:
    """Sidecar path: data/convert/foo.json → data/convert/foo.json.meta.json.

    Suffixing rather than replacing the extension keeps the sidecar
    visually paired with the file it describes, which matters for a
    directory the user might inspect by hand.
    """
    return out_path.with_name(out_path.name + ".meta.json")


def _read_cache_sidecar(out_path: Path,
                        source_path: Path,
                        id_scheme: str,
                        annotate: bool,
                        ) -> Optional[dict]:
    """Return the sidecar dict if the cache is valid, else None.

    Validity requires all of:

      * the converted file exists,
      * the sidecar exists and parses as a JSON object,
      * its converter_version matches CONVERTER_VERSION,
      * its annotate and id_scheme match what the caller asked for,
      * its source_path matches the resolved source,
      * its source_mtime and source_size match the source's current
        stat.

    Any failure returns None; the caller re-converts. An output file
    in data/convert/ with no sidecar is treated as a miss — it is a
    leftover from a pre-cache conversion (or a hand-placed file), not
    a trusted entry.
    """
    if not out_path.is_file():
        return None
    sidecar_path = _cache_sidecar_path(out_path)
    if not sidecar_path.is_file():
        return None
    try:
        meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None

    if meta.get("converter_version") != CONVERTER_VERSION:
        return None
    if meta.get("annotate") is not annotate:
        return None
    if meta.get("id_scheme") != id_scheme:
        return None
    if meta.get("source_path") != str(source_path):
        return None

    try:
        src_stat = source_path.stat()
    except OSError:
        return None
    if meta.get("source_mtime") != src_stat.st_mtime:
        return None
    if meta.get("source_size") != src_stat.st_size:
        return None

    return meta


def _write_cache_sidecar(out_path: Path,
                         source_path: Path,
                         id_scheme: str,
                         annotate: bool,
                         records: list,
                         ) -> None:
    """Write the sidecar next to a freshly written converted file.

    Best-effort: a failure to write the sidecar does not fail the
    conversion. The next call will treat the cache as a miss (no
    sidecar) and re-convert, which is exactly the pre-cache behaviour.
    """
    try:
        src_stat = source_path.stat()
    except OSError:
        return
    payload = {
        "converter_version": CONVERTER_VERSION,
        "annotate": annotate,
        "id_scheme": id_scheme,
        "source_path": str(source_path),
        "source_mtime": src_stat.st_mtime,
        "source_size": src_stat.st_size,
        "records": list(records),
    }
    try:
        _cache_sidecar_path(out_path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


# ============================================================================
#  High-level entry point
# ============================================================================

def convert_if_ui(source_path: Path,
                  *,
                  url: str = DEFAULT_COMFY_URL,
                  id_scheme: str = "auto",
                  annotate: bool = True,
                  ) -> tuple[Path, list[dict], bool]:
    """Return the API-format path for `source_path`, converting if needed.

    Behaviour:

      * If `source_path` is already API format, returns it unchanged:
        ``(source_path, [], False)``. No subprocess is spawned, no
        cache is consulted.

      * If it is UI format, the workflow is converted (once) and the
        result is written to ``<repo>/data/convert/<source_name>``,
        with a sidecar at ``<source_name>.meta.json``. A subsequent
        call with the same source, same annotate/id_scheme flags, and
        an unchanged source file returns the cached result without
        spawning a subprocess. Both the fresh and the cached case
        return ``(converted_path, records, True)``.

    `annotate` propagates to `convert_workflow`: `True` (the CLI
    default) injects `{{handle_id}}` tokens; `False` (the daemon's
    auto-conversion path) leaves `@`-prefixed resources and hardcoded
    filenames verbatim. `annotate` and `id_scheme` are part of the
    cache key — an entry written under one mode is never served to a
    caller asking for the other.

    `convert_workflow` (the lower-level worker) is deliberately
    uncached: `kritomatic comfyui convert` calls it directly, and an
    explicit convert request should always convert.

    Raises ConversionError when the file cannot be read or the
    conversion fails. The caller decides whether that is fatal. Both
    callers (CLI's `panel run`, daemon's `comfy_load`) treat it as
    fatal — the alternative (falling through to a load of the UI file)
    fails later with a less helpful message.
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

    meta = _read_cache_sidecar(out_path, source_path, id_scheme, annotate)
    if meta is not None:
        records = meta.get("records")
        if not isinstance(records, list):
            records = []
        return out_path, records, True

    converted, records = convert_workflow(
        source_path, out_path,
        url=url, id_scheme=id_scheme, annotate=annotate,
    )
    _write_cache_sidecar(
        converted, source_path, id_scheme, annotate, records,
    )
    return converted, records, True
