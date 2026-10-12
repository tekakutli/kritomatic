#!/usr/bin/env python3
"""
bg_remove_prompted.py

Remove the background from an image using a ComfyUI RMBG workflow with a
text prompt for guided segmentation.

This script:
  1. Uploads the input image to a running ComfyUI instance.
  2. Loads an API-format prompted workflow from
     comfy_workflow_to_api_converted/comfy_workflow_rmbg_prompt.json
     (next to this script), discovers its {{handle_id}} tokens via
     _meta.handles, and fills them.
  3. Prunes SaveImage nodes according to DEFAULT_MODE, identifying each
     pipeline branch by the class_type of the node feeding into its
     SaveImage rather than by hardcoded node ids.
  4. Queues the workflow, waits for completion, and downloads the
     result(s).
  5. Saves outputs next to the input with a "_nobg" suffix (and _v1/_v2
     in "both" mode).

By default, the image path is taken from IMAGE_PATH below. If a
positional argument is given on the command line, it overrides
IMAGE_PATH.

The segmentation mode is set only via the DEFAULT_MODE variable below;
there is no command-line flag for it.

Usage:
    python bg_remove_prompted.py [image_path] [--prompt TEXT]
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
IMAGE_PATH = "path/to/image.png"  # Default input image; overridden by positional arg
DEFAULT_PROMPT = "person"         # Default text prompt if none provided
DEFAULT_MODE = "v2"               # Segmentation mode: "v2", "v1", or "both"
# =================================

COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW = (
    SCRIPT_DIR / "comfy_workflow_to_api_converted" / "comfy_workflow_rmbg_prompt.json"
)


# ---------------------------------------------------------------------------
# Handle value assignment
# ---------------------------------------------------------------------------

def build_handle_values(handles, image_filename, text_prompt):
    """
    Assign each handle a value based on its declared type.

    This tool understands two handle types: 'image_filename' (filled
    with the uploaded name) and 'string' (filled with the text prompt).
    Anything else means the workflow needs an input this script does
    not produce, and it fails loudly rather than submitting a partial
    workflow.
    """
    values = {}
    for hid, spec in handles.items():
        t = spec["type"]
        if t == "image_filename":
            values[hid] = image_filename
        elif t == "string":
            values[hid] = text_prompt
        else:
            raise RuntimeError(
                f"Unsupported handle type '{t}' for handle '{hid}' "
                f"(node {spec['node_id']}.{spec['field']})"
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
# Mode selection (graph surgery, unrelated to handles)
# ---------------------------------------------------------------------------

# Which upstream class_type produces which branch's output. A SaveImage
# node whose "images" input points at a node of one of these classes
# belongs to that branch. Matching by class_type rather than by node id
# keeps mode selection working even if node ids change when the
# workflow is rebuilt in the ComfyUI editor.
BRANCH_BY_UPSTREAM_CLASS = {
    "SegmentV2": "v2",
    "Segment":   "v1",
}


def prepare_workflow(mode):
    """
    Load the workflow and prune SaveImage nodes according to mode.

    A SaveImage's "images" input points at an upstream node id; the
    upstream node's class_type identifies which pipeline branch the
    SaveImage belongs to. Nodes feeding from the branch we do NOT want
    are deleted, which is how the unwanted half of the graph stops
    producing output. This operates on the API graph structure, so it
    lives apart from the handle discovery/fill step.
    """
    if not DEFAULT_WORKFLOW.exists():
        print(f"Error: Workflow not found at {DEFAULT_WORKFLOW}")
        return None

    with open(DEFAULT_WORKFLOW, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    if mode == "both":
        print("  Mode both: Keeping both Segmentation V2 and V1")
        return workflow

    if mode not in ("v1", "v2"):
        print(f"Error: Unknown mode '{mode}'")
        return None

    prune_ids = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "SaveImage":
            continue
        images_in = node.get("inputs", {}).get("images")
        if not (isinstance(images_in, list) and images_in):
            continue
        upstream_id = images_in[0]
        upstream = workflow.get(upstream_id)
        if not isinstance(upstream, dict):
            continue
        branch = BRANCH_BY_UPSTREAM_CLASS.get(upstream.get("class_type"))
        if branch and branch != mode:
            prune_ids.append(node_id)

    for node_id in prune_ids:
        del workflow[node_id]

    if prune_ids:
        print(f"  Mode {mode}: Keeping only {mode.upper()} "
              f"(removed SaveImage node(s): {', '.join(prune_ids)})")
    else:
        print(f"  Mode {mode}: No SaveImage nodes pruned "
              f"(no {('v1' if mode == 'v2' else 'v2').upper()} branch found)")

    return workflow


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def remove_background(image_path, text_prompt, mode):
    workflow = prepare_workflow(mode)
    if workflow is None:
        return None

    handles = discover_handles(workflow)
    if not handles:
        print("Error: Workflow declares no handles to fill.")
        return None

    uploaded_filename = upload_image(image_path)

    try:
        values = build_handle_values(handles, uploaded_filename, text_prompt)
        fill_handles(workflow, values)
    except RuntimeError as e:
        print(f"Error: {e}")
        return None

    prompt_id = queue_prompt(workflow)
    history = wait_for_completion(prompt_id)

    images = get_output_images(history)
    if not images:
        print("Error: No output images generated")
        return None

    input_path = Path(image_path)
    output_paths = []

    for i, image_info in enumerate(images):
        if mode == "both":
            suffix = "_nobg_v2.png" if i == 0 else "_nobg_v1.png"
        else:
            suffix = "_nobg.png"

        output_path = input_path.parent / f"{input_path.stem}{suffix}"

        if download_image(image_info, output_path):
            output_paths.append(str(output_path))
            print(f"✓ Downloaded output {i+1}: {output_path}")
        else:
            print(f"Error: Failed to download output image {i+1}")

    if output_paths:
        print(f"✓ Background removed successfully using prompt: '{text_prompt}' "
              f"(mode: {mode})")
        print(f"  Generated {len(output_paths)} output image(s)")
        return output_paths
    else:
        print("Error: Failed to download any output images")
        return None


def process_image(image_path, text_prompt=None, mode=None):
    if not os.path.exists(image_path):
        print(f"Error: Input file not found: {image_path}")
        return None

    if text_prompt is None:
        text_prompt = DEFAULT_PROMPT
        print(f"Using default prompt: '{text_prompt}'")

    if mode is None:
        mode = DEFAULT_MODE
        print(f"Using mode from DEFAULT_MODE: '{mode}'")

    print(f"Removing background from: {image_path}")
    output_paths = remove_background(image_path, text_prompt, mode)

    if output_paths:
        print(f"\n✓ Successfully processed!")
        print(f"  Output files:")
        for path in output_paths:
            print(f"    - {path}")
        return output_paths
    else:
        print(f"✗ Failed to process image")
        return None


def print_usage():
    print("Usage: bg_remove_prompted.py [image_path] [--prompt <text>]")
    print("\nThis script will:")
    print("  1. Remove background using ComfyUI RMBG with text prompt")
    print("  2. Return resulting image(s) with '_nobg' suffix")
    print(f"\nIf no image_path is given, the IMAGE_PATH variable in the script is used.")
    print(f"Default prompt: '{DEFAULT_PROMPT}'")
    print(f"Mode (set via DEFAULT_MODE variable in the script): '{DEFAULT_MODE}'")
    print("\nModes (edit DEFAULT_MODE in the script to change):")
    print("  v2   - Use only Segmentation V2 (faster, better quality)")
    print("  v1   - Use only Segmentation V1 (older algorithm)")
    print("  both - Use both versions and return both results")
    print("\nExamples:")
    print("  # Use IMAGE_PATH variable and defaults")
    print("  bg_remove_prompted.py")
    print("\n  # Override image path")
    print("  bg_remove_prompted.py image.png")
    print("\n  # Custom prompt")
    print("  bg_remove_prompted.py image.png --prompt 'a person standing'")


def main():
    parser = argparse.ArgumentParser(
        description="Remove background from image using ComfyUI RMBG",
        add_help=False
    )
    parser.add_argument("image_path", nargs="?", default=None,
                        help="Path to the input image (overrides IMAGE_PATH variable)")
    parser.add_argument("--prompt", "-p", default=None,
                        help=f'Text prompt for segmentation (default: "{DEFAULT_PROMPT}")')
    parser.add_argument("--help", "-h", action="help",
                        help="Show this help message and exit")

    try:
        args = parser.parse_args()
    except SystemExit:
        print_usage()
        sys.exit(1)

    image_path = args.image_path if args.image_path else IMAGE_PATH
    output_paths = process_image(image_path, args.prompt)
    sys.exit(0 if output_paths else 1)


if __name__ == "__main__":
    main()
