#!/usr/bin/env python3
"""
bg_remove_prompted.py

Remove the background from an image using a ComfyUI RMBG workflow with a
text prompt for guided segmentation.

This script:
  1. Uploads the input image to a running ComfyUI instance.
  2. Loads a prompted RMBG workflow (comfy_workflow_rmbg_prompt.json next to
     this script) and selects the segmentation pipeline based on DEFAULT_MODE.
  3. Queues the workflow, waits for completion, and downloads the result(s).
  4. Saves outputs next to the input with a "_nobg" suffix (and _v1/_v2 in
     "both" mode).

By default, the image path is taken from the IMAGE_PATH variable below.
If a positional argument is given on the command line, it overrides
IMAGE_PATH.

The segmentation mode is set only via the DEFAULT_MODE variable below; there
is no command-line flag for it.

Usage:
    python bg_remove_prompted.py [image_path] [--prompt TEXT]

Examples:
    # Use IMAGE_PATH variable and default prompt/mode
    python bg_remove_prompted.py

    # Override image path
    python bg_remove_prompted.py photo.png

    # Custom prompt (mode still comes from DEFAULT_MODE)
    python bg_remove_prompted.py photo.png --prompt "a dog"
"""

import subprocess
import sys
import os
import re
import json
import requests
import time
import tempfile
import argparse
from pathlib import Path
from collections import Counter
from PIL import Image

# ===== CONFIGURABLE SETTINGS =====
IMAGE_PATH = "path/to/image.png"  # Default input image; overridden by positional arg
DEFAULT_PROMPT = "person"         # Default text prompt if none provided
DEFAULT_MODE = "v2"               # Segmentation mode: "v2", "v1", or "both"
# =================================

# ComfyUI configuration
COMFYUI_URL = "http://127.0.0.1:8188"
SCRIPT_DIR = Path(__file__).parent
DEFAULT_WORKFLOW = SCRIPT_DIR / "comfy_workflow_rmbg_prompt.json"

def upload_image(file_path):
    """Upload an image to ComfyUI's server and return the filename."""
    url = f"{COMFYUI_URL}/upload/image"

    # Create unique filename using timestamp
    file_path_obj = Path(file_path)
    unique_name = f"{file_path_obj.stem}_{int(time.time())}{file_path_obj.suffix}"

    with open(file_path, 'rb') as f:
        files = {'image': (unique_name, f, 'image/png')}
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

def prepare_workflow(mode):
    """
    Prepare the workflow based on mode.
    Disables the unwanted pipeline by removing its SaveImage node.
    """
    # Load workflow
    if not DEFAULT_WORKFLOW.exists():
        print(f"Error: Workflow not found at {DEFAULT_WORKFLOW}")
        return None

    with open(DEFAULT_WORKFLOW, 'r', encoding='utf-8') as f:
        workflow = json.load(f)

    # Mode selection: remove the corresponding SaveImage node
    if mode == "v2":
        # Remove V1's SaveImage node (node 8)
        if "8" in workflow:
            del workflow["8"]
        print("  Mode v2: Keeping only Segmentation V2 (removed V1 SaveImage node)")

    elif mode == "v1":
        # Remove V2's SaveImage node (node 4)
        if "4" in workflow:
            del workflow["4"]
        print("  Mode v1: Keeping only Segmentation V1 (removed V2 SaveImage node)")

    elif mode == "both":
        # Keep both SaveImage nodes (node 4 for V2, node 8 for V1)
        print("  Mode both: Keeping both Segmentation V2 and V1")

    else:
        print(f"Error: Unknown mode '{mode}'")
        return None

    return workflow

def remove_background(image_path, text_prompt, mode):
    """Remove background using ComfyUI RMBG workflow with text prompt"""

    # Prepare workflow based on mode
    workflow = prepare_workflow(mode)
    if workflow is None:
        return None

    # Upload image with unique name
    uploaded_filename = upload_image(image_path)

    # Find and update nodes
    for node_id, node_data in workflow.items():
        # Set the image in LoadImage node
        if node_data.get('class_type') == 'LoadImage':
            workflow[node_id]['inputs']['image'] = uploaded_filename

        # Set the text prompt in ttN text node
        if node_data.get('class_type') == 'ttN text':
            workflow[node_id]['inputs']['text'] = text_prompt

    # Queue workflow
    prompt_id = queue_prompt(workflow)

    # Wait for completion
    history = wait_for_completion(prompt_id)

    # Get output images
    images = get_output_images(history)
    if not images:
        print("Error: No output images generated")
        return None

    # Save output image(s)
    input_path = Path(image_path)
    output_paths = []

    for i, image_info in enumerate(images):
        # Determine suffix based on mode and which node produced it
        # Since we're removing the SaveImage node, we need to determine which output is which
        if mode == "v2":
            suffix = "_nobg.png"
        elif mode == "v1":
            suffix = "_nobg.png"
        elif mode == "both":
            # First output is V2 (node 4), second is V1 (node 8)
            if i == 0:
                suffix = "_nobg_v2.png"
            else:
                suffix = "_nobg_v1.png"
        else:
            suffix = f"_nobg_{i}.png"

        output_path = input_path.parent / f"{input_path.stem}{suffix}"

        if download_image(image_info, output_path):
            output_paths.append(str(output_path))
            print(f"✓ Downloaded output {i+1}: {output_path}")
        else:
            print(f"Error: Failed to download output image {i+1}")

    if output_paths:
        print(f"✓ Background removed successfully using prompt: '{text_prompt}' (mode: {mode})")
        print(f"  Generated {len(output_paths)} output image(s)")
        return output_paths
    else:
        print("Error: Failed to download any output images")
        return None

def process_image(image_path, text_prompt=None, mode=None):
    """Main function: remove background only"""
    # Check if input file exists
    if not os.path.exists(image_path):
        print(f"Error: Input file not found: {image_path}")
        return None

    # Use default prompt if none provided
    if text_prompt is None:
        text_prompt = DEFAULT_PROMPT
        print(f"Using default prompt: '{text_prompt}'")

    # Mode always comes from the DEFAULT_MODE variable
    if mode is None:
        mode = DEFAULT_MODE
        print(f"Using mode from DEFAULT_MODE: '{mode}'")

    # Remove background
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
    """Print usage information"""
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
    # Set up argument parser
    parser = argparse.ArgumentParser(
        description="Remove background from image using ComfyUI RMBG",
        add_help=False  # We'll handle help manually for custom format
    )

    # Define arguments
    parser.add_argument('image_path', nargs='?', default=None,
                       help='Path to the input image (overrides IMAGE_PATH variable)')
    parser.add_argument('--prompt', '-p',
                       help=f'Text prompt for segmentation (default: "{DEFAULT_PROMPT}")',
                       default=None)
    parser.add_argument('--help', '-h',
                       action='help',
                       help='Show this help message and exit')

    # Parse arguments
    try:
        args = parser.parse_args()
    except SystemExit:
        # If parse_args fails, show usage and exit
        print_usage()
        sys.exit(1)

    # Use positional argument if provided, otherwise fall back to IMAGE_PATH
    image_path = args.image_path if args.image_path else IMAGE_PATH

    # Process the image (mode comes from DEFAULT_MODE inside process_image)
    output_paths = process_image(image_path, args.prompt)

    if output_paths:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
