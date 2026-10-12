#!/usr/bin/env python3
"""
comfy_img2img.py

ComfyUI img2img CLI tool (barebones, handle-aware).

Uploads an input image to a running ComfyUI instance, loads an API-format
workflow JSON, discovers its {{handle_id}} tokens via _meta.handles, fills
them, queues the workflow, waits for completion, and saves the first output
next to the input with an "_output" suffix.

Handle filling rules:
  - Handles of type "image_filename" are auto-filled with the uploaded
    filename. There is normally exactly one of these (the LoadImage node).
  - Any handle of any other type causes the tool to refuse before
    contacting ComfyUI, naming the handle. Workflows with non-image
    handles are handled by their dedicated tools, not by this generic
    runner.

The workflow file is set via DEFAULT_WORKFLOW below, or overridden with
--workflow. The default is a plain RMBG workflow, chosen only as a
sensible fallback for a bare invocation; the specialized tool for that
workflow is bg_remove.py, and the prompted variant has its own tool in
bg_remove_prompted.py. The point of this script is to run arbitrary
converted workflows whose input is a single image.

The input image path is taken from IMAGE_PATH below, or from a positional
argument.

The resolved output path is printed to stdout (so a bash wrapper can
capture it). All diagnostics go to stderr.

Usage:
    python comfy_img2img.py [input_image_path] [options]

Examples:
    # Use IMAGE_PATH and DEFAULT_WORKFLOW
    python comfy_img2img.py

    # Override image path
    python comfy_img2img.py photo.png

    # Use a specific converted workflow
    python comfy_img2img.py photo.png --workflow my_workflow.json

    # Discover what a workflow expects before running it
    python comfy_img2img.py --list --workflow my_workflow.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

# Reuse the shared handle helpers from the sibling comfy_handles module.
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from comfy_handles import discover_handles, fill_handles  # noqa: E402

# ===== CONFIGURABLE SETTINGS =====
IMAGE_PATH = "path/to/image.png"   # Default input image; overridden by positional arg
# =================================

COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW = (
    SCRIPT_DIR / "comfy_workflow_to_api_converted" / "comfy_workflow_rmbg.json"
)


def eprint(*args, **kwargs):
    """Print to stderr, so stdout stays clean for the bash wrapper."""
    print(*args, file=sys.stderr, **kwargs)


def short_path(path):
    """Show a path relative to CWD when possible, else as-is."""
    try:
        return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Handle value assignment
# ---------------------------------------------------------------------------

def build_auto_values(handles, uploaded_filename):
    """
    Auto-fill every handle we can determine without user input: every
    handle of type 'image_filename' gets the uploaded image.

    Any handle of a type this function does not know how to fill is a
    refusal, not a skip. The caller (main) decides how to surface that
    refusal, but it must be surfaced: submitting a workflow with an
    unfilled handle would either fail inside ComfyUI with a less
    helpful message, or run with a stale default.
    """
    values = {}
    unsupported = []
    for hid, spec in handles.items():
        if spec["type"] == "image_filename":
            values[hid] = uploaded_filename
        else:
            unsupported.append((hid, spec["type"],
                                spec["node_id"], spec["field"]))
    if unsupported:
        lines = [
            f"handle '{hid}' (type '{t}', node {nid}.{field})"
            for hid, t, nid, field in unsupported
        ]
        raise RuntimeError(
            "This tool only fills 'image_filename' handles. "
            "The workflow also declares:\n  "
            + "\n  ".join(lines)
            + "\nIf the workflow has a dedicated tool in this collection, "
            "use that instead; otherwise add a branch here for the new "
            "handle type."
        )
    return values


# ---------------------------------------------------------------------------
# ComfyUI API
# ---------------------------------------------------------------------------

def upload_image(file_path):
    """Upload an image and return the filename ComfyUI stored it as."""
    url = f"{COMFYUI_URL}/upload/image"
    p = Path(file_path)
    unique_name = f"{p.stem}_{int(time.time())}{p.suffix}"

    with open(file_path, "rb") as f:
        files = {"image": (unique_name, f, "image/png")}
        data = {"overwrite": "true"}
        response = requests.post(url, files=files, data=data)

    if response.status_code != 200:
        raise RuntimeError(f"Upload failed: {response.status_code} {response.text}")
    return response.json()["name"]


def queue_prompt(workflow):
    response = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
    if response.status_code != 200:
        raise RuntimeError(f"Queue failed: {response.status_code} {response.text}")
    return response.json()["prompt_id"]


def wait_for_completion(prompt_id):
    while True:
        response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if response.status_code == 200:
            history = response.json()
            if prompt_id in history:
                return history[prompt_id]
        time.sleep(1)


def get_output_images(history):
    images = []
    for node_out in history.get("outputs", {}).values():
        if "images" in node_out:
            images.extend(node_out["images"])
    return images


def download_image(image_info, save_path):
    filename = image_info.get("filename")
    subfolder = image_info.get("subfolder", "")
    url = (f"{COMFYUI_URL}/view"
           f"?filename={filename}&subfolder={subfolder}&type=output")
    response = requests.get(url)
    if response.status_code == 200:
        with open(save_path, "wb") as f:
            f.write(response.content)
        return True
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_handles(workflow, workflow_path):
    """Print the workflow's handles and exit."""
    handles = discover_handles(workflow)
    eprint(f"Workflow : {workflow_path}")
    if not handles:
        eprint("Handles  : (none — the workflow has no user-supplied inputs)")
        return
    eprint(f"Handles  : {len(handles)}")
    eprint("")
    for hid, spec in handles.items():
        eprint(f"  {hid}")
        eprint(f"    type   {spec['type']}")
        eprint(f"    node   {spec['node_id']}.{spec['field']}")
        eprint("")


def main():
    parser = argparse.ArgumentParser(
        prog="comfy_img2img.py",
        description=(
            "Run an API-format ComfyUI workflow whose input is a single "
            "image. Handles of type 'image_filename' are auto-filled from "
            "the uploaded image; workflows with other handle types are "
            "refused. See bg_remove.py and bg_remove_prompted.py for the "
            "dedicated tools built on the RMBG workflows."
        ),
    )
    parser.add_argument(
        "image_path", nargs="?", default=None,
        help="Optional input image path (overrides IMAGE_PATH variable).",
    )
    parser.add_argument(
        "--workflow", type=Path, default=None,
        help=(f"Workflow JSON to run "
              f"(default: {short_path(DEFAULT_WORKFLOW)})."),
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List the workflow's handles and exit.",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output path (default: <input_stem>_output.png next to the input).",
    )
    args = parser.parse_args()

    input_image_path = args.image_path or IMAGE_PATH
    workflow_path = args.workflow or DEFAULT_WORKFLOW

    if not workflow_path.exists():
        eprint(f"Error: Workflow not found at {workflow_path}")
        sys.exit(1)

    with open(workflow_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    if args.list:
        list_handles(workflow, workflow_path)
        sys.exit(0)

    if not os.path.exists(input_image_path):
        eprint(f"Error: Input file '{input_image_path}' not found")
        sys.exit(1)

    handles = discover_handles(workflow)
    if not handles:
        eprint("Error: Workflow declares no handles to fill.")
        sys.exit(1)

    try:
        uploaded_filename = upload_image(input_image_path)
    except RuntimeError as e:
        eprint(f"Error: {e}")
        sys.exit(1)

    try:
        values = build_auto_values(handles, uploaded_filename)
        fill_handles(workflow, values)
    except RuntimeError as e:
        eprint(f"Error: {e}")
        sys.exit(1)

    try:
        prompt_id = queue_prompt(workflow)
        history = wait_for_completion(prompt_id)
    except RuntimeError as e:
        eprint(f"Error: {e}")
        sys.exit(1)

    images = get_output_images(history)
    if not images:
        eprint("Error: No output images generated")
        sys.exit(1)

    input_path = Path(input_image_path)
    output_path = args.output or (input_path.parent / f"{input_path.stem}_output.png")

    if not download_image(images[0], output_path):
        eprint("Error: Failed to download output image")
        sys.exit(1)

    # stdout is the output path only, for the bash wrapper.
    print(output_path)


if __name__ == "__main__":
    main()
