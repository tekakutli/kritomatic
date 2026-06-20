#!/home/tekakutli/code/kritomatic-auxiliary/bin/python
"""
Remove background from image using ComfyUI RMBG
"""

import subprocess
import sys
import os
import re
import json
import requests
import time
import tempfile
from pathlib import Path
from collections import Counter
from PIL import Image

# ComfyUI configuration
COMFYUI_URL = "http://127.0.0.1:8188"
SCRIPT_DIR = Path(__file__).parent
DEFAULT_WORKFLOW = SCRIPT_DIR / "RMBG_api.json"

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

def remove_background(image_path):
    """Remove background using ComfyUI RMBG workflow"""
    # Check if workflow exists
    if not DEFAULT_WORKFLOW.exists():
        print(f"Error: Workflow not found at {DEFAULT_WORKFLOW}")
        return None

    # Load workflow
    with open(DEFAULT_WORKFLOW, 'r', encoding='utf-8') as f:
        workflow = json.load(f)

    # Upload image with unique name
    uploaded_filename = upload_image(image_path)

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
        print("Error: No output images generated")
        return None

    # Save output next to input with "_nobg" suffix
    input_path = Path(image_path)
    output_path = input_path.parent / f"{input_path.stem}_nobg.png"

    # Download and save
    if download_image(images[0], output_path):
        print(f"✓ Background removed successfully")
        return str(output_path)
    else:
        print("Error: Failed to download output image")
        return None

def process_image(image_path):
    """Main function: remove background only"""
    # Check if input file exists
    if not os.path.exists(image_path):
        print(f"Error: Input file not found: {image_path}")
        return None

    # Remove background
    print(f"Removing background from: {image_path}")
    output_path = remove_background(image_path)

    if output_path:
        print(f"✓ Successfully processed!")
        print(f"  Output: {output_path}")
        return output_path
    else:
        print(f"✗ Failed to process image")
        return None

def print_usage():
    """Print usage information"""
    print("Usage: remove_background.py <image_path>")
    print("\nThis script will:")
    print("  1. Remove background using ComfyUI RMBG")
    print("  2. Return the resulting image with '_nobg' suffix")
    print("\nExample:")
    print("  remove_background.py image.png")

def main():
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    image_path = sys.argv[1]

    # Process the image
    output_path = process_image(image_path)

    if output_path:
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
