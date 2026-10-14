"""
cli.py — convert a ComfyUI UI-format workflow to API format.

The conversion itself is a POST to `/workflow/convert`, an endpoint
provided by the `comfyui-workflow-to-api-converter-endpoint` custom
node. Two things happen per conversion:

  1. The UI-format JSON is POSTed to the endpoint, which returns the
     equivalent prompt-format (API) JSON.

  2. Every user-supplied input listed in HANDLE_FIELDS is replaced
     with a `{{handle_id}}` token and the specification is recorded in
     the node's `_meta.handles` block. That lets an external tool
     fill the inputs at runtime.

Output lands under `<repo>/data/convert/`, sibling of the panel's
`data/comfy_panel/`. The filename mirrors the source JSON exactly, so
a source at `<anywhere>/foo.json` becomes `data/convert/foo.json`.

The panel CLI uses `convert_if_ui()` to convert a UI-format workflow
in place before handing it to the daemon, so `kritomatic comfyui
panel run /path/to/foo.json` accepts a UI-format file directly. The
standalone `kritomatic comfyui convert` subcommand exposes the same
conversion for direct use.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from kritomatic.helpful_argparse import HelpfulArgumentParser


# ============================================================================
#  Paths
# ============================================================================

# <repo>/src/kritomatic/comfy_convert/cli.py
#   .parent             → .../comfy_convert
#   .parent.parent      → .../kritomatic
#   .parent.parent.parent        → .../src
#   .parent.parent.parent.parent → <repo>
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CONVERT_DIR = _REPO_ROOT / "data" / "convert"

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
CONVERT_ENDPOINT_PATH = "/workflow/convert"


# ============================================================================
#  Handle configuration
# ============================================================================
#
# Copied verbatim from the standalone comfy_workflow_to_api_converter.py
# script so a conversion done here matches a conversion done there.
# Extend these dicts to add support for more node types.

HANDLE_FIELDS = {
    "LoadImage": ["image"],
    "ttN text": ["text"],
}

HANDLE_TYPES = {
    ("LoadImage", "image"): "image_filename",
    ("ttN text", "text"): "string",
}


class ConversionError(Exception):
    """Any failure while converting or fetching the endpoint."""


# ============================================================================
#  HTTP
# ============================================================================

def _post_json(url: str, payload: dict, timeout: float = 60.0) -> dict:
    """POST `payload` as JSON to `url`; return the parsed JSON response.

    Raises ConversionError on network failure, non-2xx status, or a
    response body that is not JSON.
    """
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read()[:500].decode("utf-8", errors="replace")
        except Exception:
            pass
        raise ConversionError(
            f"Endpoint returned {e.code}"
            + (f": {snippet}" if snippet else "")
        ) from e
    except urllib.error.URLError as e:
        raise ConversionError(
            f"Could not reach {url}: {e.reason}. "
            f"Is ComfyUI running with the "
            f"comfyui-workflow-to-api-converter-endpoint node installed?"
        ) from e
    except Exception as e:
        raise ConversionError(f"Request failed: {e}") from e

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConversionError(
            f"Endpoint returned non-JSON response: {raw[:120]!r}"
        ) from e


# ============================================================================
#  Format detection
# ============================================================================

def is_ui_format(workflow: object) -> bool:
    """True iff the JSON looks like a ComfyUI UI-format workflow.

    The UI format has a top-level `nodes` array and a top-level `links`
    array. The API format is a dict keyed by node id, each value
    carrying `class_type` and `inputs`. The two never overlap: an API
    workflow has no `nodes`/`links` keys, and a UI workflow's top level
    carries neither `class_type` nor `inputs`.
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

    Returns (annotated_api, records), where records is one dict per
    injected handle, in graph iteration order. That list is what the
    CLI's report prints.
    """
    seen: dict[str, str] = {}
    records: list[dict] = []

    for node_id, node in api.items():
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

    endpoint = url.rstrip("/") + CONVERT_ENDPOINT_PATH
    api = _post_json(endpoint, workflow)
    if not isinstance(api, dict) or not api:
        raise ConversionError(
            f"Endpoint returned an empty or non-object response for "
            f"{source_path}."
        )

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
        ``(source_path, [], False)``. No network call is made.

      * If it is UI format, the workflow is converted and the result
        is written to ``<repo>/data/convert/<source_name>``. The file
        is overwritten on each call, so a source edited between runs
        produces a fresh conversion. Returns
        ``(converted_path, records, True)``.

    Raises ConversionError when the file cannot be read or the
    endpoint refuses the request. The caller decides whether that is
    fatal. The panel CLI treats it as fatal — the alternative
    (falling through to a daemon-side load of the UI file) fails
    later with a less helpful message.
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
        help=f'Base URL of the ComfyUI server '
             f'(default: {DEFAULT_COMFY_URL}).',
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
            "Convert a ComfyUI UI-format workflow to API format via "
            "the comfyui-workflow-to-api-converter-endpoint, inject "
            "{{handle_id}} tokens for user-supplied inputs, and save "
            f"the result to {CONVERT_DIR}/."
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
