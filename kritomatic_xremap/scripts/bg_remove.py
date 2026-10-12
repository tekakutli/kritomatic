#!/usr/bin/env python3
"""
bg_remove.py

Remove the background from an image using a ComfyUI RMBG workflow
that uses {{handle_id}} tokens for user-supplied inputs.

This script:
  1. Uploads the input image to a running ComfyUI instance.
  2. Loads an API-format RMBG workflow from
     comfy_workflow_to_api_converted/comfy_workflow_rmbg.json (next to
     this script) and discovers its {{handle_id}} tokens via
     _meta.handles.
  3. Fills every declared handle with the uploaded filename, verifies
     no unresolved tokens remain, and queues the workflow.
  4. Waits for completion and saves the result next to the input with
     a "_nobg" suffix.

By default, the image path is taken from the IMAGE_PATH variable below.
If a single positional argument is given on the command line, it
overrides IMAGE_PATH.

Usage:
    python bg_remove.py [image_path]

This script does not use flags.
"""

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

# ComfyUI configuration
COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_WORKFLOW = (
    SCRIPT_DIR / "comfy_workflow_to_api_converted" / "comfy_workflow_rmbg.json"
)

# === DEFAULT INPUT ===
IMAGE_PATH = "path/to/image.png"  # <-- change this to your default image file
# =====================


# ---------------------------------------------------------------------------
# ComfyUI API
# ---------------------------------------------------------------------------

def upload_image(file_path):
    """Upload an image to ComfyUI and return the filename it was stored as."""
    url = f"{COMFYUI_URL}/upload/image"
    file_path_obj = Path(file_path)
    unique_name = f"{file_path_obj.stem}_{int(time.time())}{file_path_obj.suffix}"

    with open(file_path, "rb") as f:
        files = {"image": (unique_name, f, "image/png")}
        data = {"overwrite": "true"}
        response = requests.post(url, files=files, data=data)

    if response.status_code != 200:
        raise RuntimeError(f"Upload failed: {response.status_code} {response.text}")
    return response.json()["name"]


def queue_prompt(workflow):
    """Send a workflow to ComfyUI and return its prompt id."""
    response = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})
    if response.status_code != 200:
        raise RuntimeError(f"Queue failed: {response.status_code} {response.text}")
    return response.json()["prompt_id"]


def wait_for_completion(prompt_id):
    """Block until the prompt appears in /history."""
    while True:
        response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if response.status_code == 200:
            history = response.json()
            if prompt_id in history:
                return history[prompt_id]
        time.sleep(1)


def get_output_images(history):
    """Collect every image entry from the history's outputs."""
    images = []
    for node_out in history.get("outputs", {}).values():
        if "images" in node_out:
            images.extend(node_out["images"])
    return images


def download_image(image_info, save_path):
    """Fetch one output image from /view and write it to disk."""
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
# Main flow
# ---------------------------------------------------------------------------

def remove_background(image_path):
    """Run the RMBG workflow with the input image slotted into its handles."""
    if not DEFAULT_WORKFLOW.exists():
        print(f"Error: Workflow not found at {DEFAULT_WORKFLOW}")
        return None

    with open(DEFAULT_WORKFLOW, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # 1. Find out what the workflow expects.
    handles = discover_handles(workflow)
    if not handles:
        print("Error: Workflow declares no handles to fill.")
        return None

    # 2. Upload the image once.
    uploaded_filename = upload_image(image_path)

    # 3. Build the value map. This tool supports only workflows whose
    #    handles are all of type 'image_filename'; anything else means
    #    the workflow is asking for a kind of input bg_remove cannot
    #    supply, and it should fail loudly rather than half-submit.
    values = {}
    for hid, spec in handles.items():
        if spec["type"] == "image_filename":
            values[hid] = uploaded_filename
        else:
            print(f"Error: Unsupported handle type '{spec['type']}' "
                  f"for handle '{hid}' "
                  f"(node {spec['node_id']}.{spec['field']})")
            return None

    # 4. Substitute and validate.
    try:
        fill_handles(workflow, values)
    except RuntimeError as e:
        print(f"Error: {e}")
        return None

    # 5. Queue and wait.
    prompt_id = queue_prompt(workflow)
    history = wait_for_completion(prompt_id)

    images = get_output_images(history)
    if not images:
        print("Error: No output images generated")
        return None

    input_path = Path(image_path)
    output_path = input_path.parent / f"{input_path.stem}_nobg.png"

    if download_image(images[0], output_path):
        print("✓ Background removed successfully")
        return str(output_path)
    else:
        print("Error: Failed to download output image")
        return None


def process_image(image_path):
    """Validate the input path, then run the removal."""
    if not os.path.exists(image_path):
        print(f"Error: Input file not found: {image_path}")
        return None

    print(f"Removing background from: {image_path}")
    output_path = remove_background(image_path)

    if output_path:
        print("✓ Successfully processed!")
        print(f"  Output: {output_path}")
        return output_path
    else:
        print("✗ Failed to process image")
        return None


def print_usage():
    print("Usage: bg_remove.py [image_path]")
    print("\nThis script will:")
    print("  1. Remove background using ComfyUI RMBG")
    print("  2. Return the resulting image with '_nobg' suffix")
    print("\nIf no image_path is given, the IMAGE_PATH variable in the script is used.")
    print("\nExample:")
    print("  bg_remove.py image.png")


def main():
    if len(sys.argv) > 2:
        print_usage()
        sys.exit(1)

    image_path = sys.argv[1] if len(sys.argv) == 2 else IMAGE_PATH
    output_path = process_image(image_path)
    sys.exit(0 if output_path else 1)


if __name__ == "__main__":
    main()
