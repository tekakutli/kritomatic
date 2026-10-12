#!/usr/bin/env python3
"""
comfy_workflow_to_api_converter.py

Convert a normal (UI-format) ComfyUI workflow into API format using the
comfyui-workflow-to-api-converter-endpoint, then replace every
user-supplied input with a {{handle_id}} token and record the handle
specification in each node's _meta.handles block.

Output defaults to a subdirectory named 'comfy_workflow_to_api_converted'
located next to this script. The output filename matches the input
filename exactly — no suffixes are added or stripped.

Prerequisites:
    - ComfyUI running with the custom node
      'comfyui-workflow-to-api-converter-endpoint' installed and loaded.
    - pip install requests
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Map: node class_type -> list of input fields that should become handles.
# Extend this dict to support more node types as your workflows grow.
HANDLE_FIELDS = {
    "LoadImage": ["image"],
    "ttN text": ["text"],
}

# Map: (class_type, field) -> semantic type label for the handle.
HANDLE_TYPES = {
    ("LoadImage", "image"): "image_filename",
    ("ttN text", "text"): "string",
}

OUTPUT_SUBDIR_NAME = "comfy_workflow_to_api_converted"
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def short(path: Path) -> str:
    """Show a path relative to CWD when possible, else as-is."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Conversion via the endpoint
# ---------------------------------------------------------------------------

def convert_via_endpoint(workflow: dict, base_url: str) -> dict:
    url = urljoin(base_url, "/workflow/convert")
    try:
        r = requests.post(url, json=workflow, timeout=60)
    except requests.exceptions.ConnectionError:
        sys.exit(f"Error: could not connect to ComfyUI at {base_url}. "
                 f"Is it running?")
    if r.status_code != 200:
        sys.exit(f"Error: converter endpoint returned {r.status_code}:\n{r.text}")
    return r.json()


# ---------------------------------------------------------------------------
# Handle annotation
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    """Lowercase, replace any non-alphanumeric run with '_', trim '_'."""
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def _make_handle_id(node_id: str, node: dict, field: str, scheme: str) -> str:
    cls = node["class_type"]
    if scheme == "auto":
        return f"{_slug(cls)}_{field}_{node_id}"
    if scheme == "title":
        title = node.get("_meta", {}).get("title", cls)
        return f"{_slug(title)}_{field}"
    if scheme == "field":
        return field
    raise ValueError(f"Unknown id scheme: {scheme}")


def annotate_handles(api: dict, scheme: str = "auto"):
    seen = {}
    records = []

    for node_id, node in api.items():
        fields = HANDLE_FIELDS.get(node.get("class_type"))
        if not fields:
            continue

        meta = node.setdefault("_meta", {})
        handles = meta.setdefault("handles", {})

        for field in fields:
            hid = _make_handle_id(node_id, node, field, scheme)
            if hid in seen:
                sys.exit(
                    f"Error: handle id collision — '{hid}' is claimed by "
                    f"both node {seen[hid]} and node {node_id}. "
                    f"Try --id-scheme auto."
                )
            seen[hid] = node_id

            token = "{{" + hid + "}}"
            node.setdefault("inputs", {})[field] = token

            htype = HANDLE_TYPES.get((node["class_type"], field), "string")
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


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(records, out_path: Path) -> None:
    out_dir = out_path.parent
    out_name = out_path.name

    print(f"  Output   {short(out_dir)}/")
    print(f"           {out_name}")
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


# ---------------------------------------------------------------------------
# Output location
# ---------------------------------------------------------------------------

def default_output_dir() -> Path:
    here = Path(__file__).resolve().parent
    out_dir = here / OUTPUT_SUBDIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def resolve_output_path(input_path: Path, user_output) -> Path:
    input_name = input_path.name

    if user_output is None:
        return default_output_dir() / input_name

    user_output = Path(user_output)

    if user_output.suffix == "":
        user_output.mkdir(parents=True, exist_ok=True)
        return user_output / input_name

    user_output.parent.mkdir(parents=True, exist_ok=True)
    return user_output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(
        prog="comfy_workflow_to_api_converter.py",
        description=(
            "Convert a normal ComfyUI workflow to API format via "
            "comfyui-workflow-to-api-converter-endpoint, inject "
            "{{handle_id}} tokens for user-supplied inputs, and save the "
            f"result to ./{OUTPUT_SUBDIR_NAME}/ by default."
        ),
    )
    p.add_argument("--input", required=True, type=Path,
                   help="Path to the normal (UI-format) workflow JSON.")
    p.add_argument("--output", type=Path, default=None,
                   help=(f"Output file or directory. Default: "
                         f"<script_dir>/{OUTPUT_SUBDIR_NAME}/<input_filename>"))
    p.add_argument("--url", default=DEFAULT_COMFY_URL,
                   help=f"Base URL of the ComfyUI server "
                        f"(default: {DEFAULT_COMFY_URL}).")
    p.add_argument("--id-scheme", choices=["auto", "title", "field"],
                   default="auto",
                   help=("How handle ids are generated: "
                         "auto = <class>_<field>_<node_id> (default); "
                         "title = <slug(node title)>_<field>; "
                         "field = <field>."))
    p.add_argument("--quiet", action="store_true",
                   help="Print only the final output path.")
    args = p.parse_args()

    if not args.input.is_file():
        sys.exit(f"Error: input file not found: {args.input}")

    out_path = resolve_output_path(args.input, args.output)

    if not args.quiet:
        print(f"Converting {short(args.input)} ...", flush=True)

    workflow = load_json(args.input)
    api = convert_via_endpoint(workflow, args.url)
    api, records = annotate_handles(api, scheme=args.id_scheme)
    save_json(api, out_path)

    if args.quiet:
        print(short(out_path))
        return

    print()
    print_report(records, out_path)


if __name__ == "__main__":
    main()
