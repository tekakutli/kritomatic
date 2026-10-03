#!/usr/bin/env python3
"""
comfy_img2img.py

ComfyUI img2img CLI tool (barebones).

Uploads an input image to a running ComfyUI instance, loads a workflow JSON
(the file set in DEFAULT_WORKFLOW, next to this script), sets the image on
its LoadImage node, queues the workflow, waits for completion, and saves the
first output next to the input with an "_output" suffix.

By default, the input image path is taken from the IMAGE_PATH variable below.
If a positional argument is given on the command line, it overrides
IMAGE_PATH.

The resolved output path is printed to stdout (so a bash wrapper can capture
it).

Usage:
    python comfy_img2img.py [input_image_path]

Examples:
    # Use IMAGE_PATH variable
    python comfy_img2img.py

    # Override with a file path
    python comfy_img2img.py photo.png
"""

import json
import requests
import os
import sys
import time
from pathlib import Path

# ===== CONFIGURABLE SETTINGS =====
IMAGE_PATH = "path/to/image.png"   # Default input image; overridden by positional arg
# =================================

# --- Configuration ---
COMFYUI_URL = "http://127.0.0.1:8188"
# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent
DEFAULT_WORKFLOW = SCRIPT_DIR / "comfy_workflow_rmbg.json"

def upload_image(file_path):
    """Upload an image to ComfyUI's server and return the filename."""
    url = f"{COMFYUI_URL}/upload/image"

    with open(file_path, 'rb') as f:
        files = {'image': f}
        data = {'overwrite': 'true'}
        response = requests.post(url, files=files, data=data)

    if response.status_code == 200:
        return response.json()['name']
    else:
        raise Exception(f"Upload failed: {response.status_code}")

def queue_prompt(workflow):
    """Send a workflow to ComfyUI and get the prompt ID."""
    response = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})

    if response.status_code == 200:
        return response.json()['prompt_id']
    else:
        raise Exception(f"Queue failed: {response.status_code}")

def wait_for_completion(prompt_id):
    """Wait for a prompt to complete."""
    while True:
        response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if response.status_code == 200:
            history = response.json()
            if prompt_id in history:
                return history[prompt_id]
        time.sleep(1)

def get_output_images(history):
    """Extract output images from workflow history."""
    images = []
    for node_out in history.get('outputs', {}).values():
        if 'images' in node_out:
            images.extend(node_out['images'])
    return images

def download_image(image_info, save_path):
    """Download an image from ComfyUI."""
    filename = image_info.get('filename')
    subfolder = image_info.get('subfolder', '')
    url = f"{COMFYUI_URL}/view?filename={filename}&subfolder={subfolder}&type=output"

    response = requests.get(url)
    if response.status_code == 200:
        with open(save_path, 'wb') as f:
            f.write(response.content)
        return True
    return False

def main():
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='ComfyUI img2img CLI tool')
    parser.add_argument('image_path', nargs='?', default=None,
                       help='Optional input image path (overrides IMAGE_PATH variable)')
    args = parser.parse_args()

    # Use positional argument if provided, otherwise fall back to IMAGE_PATH
    input_image_path = args.image_path or IMAGE_PATH

    # Check if input file exists
    if not os.path.exists(input_image_path):
        print(f"Error: Input file '{input_image_path}' not found", file=sys.stderr)
        sys.exit(1)

    # Check if workflow exists
    if not DEFAULT_WORKFLOW.exists():
        print(f"Error: Workflow not found at {DEFAULT_WORKFLOW}", file=sys.stderr)
        sys.exit(1)

    # Load workflow
    with open(DEFAULT_WORKFLOW, 'r', encoding='utf-8') as f:
        workflow = json.load(f)

    # Upload image
    uploaded_filename = upload_image(input_image_path)

    # Find LoadImage node and set the image
    for node_id, node_data in workflow.items():
        if node_data.get('class_type') == 'LoadImage':
            workflow[node_id]['inputs']['image'] = uploaded_filename
            break

    # Queue workflow
    prompt_id = queue_prompt(workflow)

    # Wait for completion
    history = wait_for_completion(prompt_id)

    # Get output images
    images = get_output_images(history)
    if not images:
        print("Error: No output images generated", file=sys.stderr)
        sys.exit(1)

    # Save output next to input with "_output" suffix
    input_path = Path(input_image_path)
    output_path = input_path.parent / f"{input_path.stem}_output.png"

    # Download and save
    download_image(images[0], output_path)

    # Print output path (for bash wrapper to capture)
    print(output_path)

if __name__ == "__main__":
    main()
