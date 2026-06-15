#!/usr/bin/env python3
"""
ComfyUI img2img CLI tool - Barebones version
Usage: python comfy_img2img.py <input_image_path>
"""

import json
import requests
import os
import sys
import time
from pathlib import Path

# --- Configuration ---
COMFYUI_URL = "http://127.0.0.1:8188"
# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent
DEFAULT_WORKFLOW = SCRIPT_DIR / "RMBG_api.json"

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
    if len(sys.argv) != 2:
        print("Usage: python comfy_img2img.py <input_image_path>", file=sys.stderr)
        sys.exit(1)

    input_image_path = sys.argv[1]

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
