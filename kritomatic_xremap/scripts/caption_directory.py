#!/usr/bin/env python3
"""
Image Captioning Script for llama.cpp using llama-server
Processes a single image or all images in a directory via persistent API server
Usage:
    First, start the server:
        llama-server -m /path/to/model.gguf --mmproj /path/to/mmproj.gguf -c 8192 -ngl 999

    Then run this script:
        python3 caption_server.py [input_path] [--prompt LABEL] [--append TEXT] [--max-tokens N] [--word-limit W]
"""

import os
import base64
import glob
import sys
import argparse
import requests
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

# ============================================
# CONFIGURATION (EDIT THESE)
# ============================================

SERVER_URL = "http://127.0.0.1:6006/v1/chat/completions"

# ============================================
# PROMPT MAPPING - LONG TEXTS (YOU FILL THESE IN)
# ============================================

PROMPTS = {
    "Descriptive": "Write a long detailed description for this image.",
    "Descriptive (Casual)": "Write a long descriptive caption for this image in a casual tone.",
    "Straightforward": "Write a long straightforward caption for this image. Begin with the main subject and medium. Mention pivotal elements—people, objects, scenery—using confident, definite language. Focus on concrete details like color, shape, texture, and spatial relationships. Show how elements interact. Omit mood and speculative wording. If text is present, quote it exactly. Note any watermarks, signatures, or compression artifacts. Never mention what's absent, resolution, or unobservable details. Vary your sentence structure and keep the description concise, without starting with “This image is...” or similar phrasing.",
    "Stable Diffusion Prompt": "Output a long stable diffusion prompt that is indistinguishable from a real stable diffusion prompt.",
    # "Danbooru tag list": "Generate only comma-separated Danbooru tags (lowercase_underscores). Strict order: `artist:`, `copyright:`, `character:`, `meta:`, then general tags. Include counts (1girl), appearance, clothing, accessories, pose, expression, actions, background. Use precise Danbooru syntax. No extra text. long length.",
    "Danbooru tag list": "Generate a comma-separated list of Danbooru tags for this image. Tags must be lowercase_with_underscores. Place a space after each comma. Do NOT include any prefixes like 'artist:', 'copyright:', 'character:', or 'meta:'. Only output the raw tags. Start with character count (e.g., 1girl, 2boys), then appearance, clothing, accessories, pose, expression, actions, background. Use standard Danbooru syntax. No extra text, no line breaks. Output 20-40 tags. Example: 1girl, solo, long_hair, blue_eyes, sitting, smile, nurse, hat",
    # "e621 tag list": "Write a long comma-separated list of e621 tags in alphabetical order for this image. Start with the artist, copyright, character, species, meta, and lore tags (if any), prefixed by 'artist:', 'copyright:', 'character:', 'species:', 'meta:', and 'lore:'. Then all the general tags.",
    "e621 tag list": "Write a comma-separated list of e621 tags for this image. Do NOT include prefixes like 'general:', 'meta:', or 'lore:'. Do NOT put each tag on a new line. Output as a simple comma-separated list. Example: 1boy, solo, blue_eyes, blonde_hair, sitting",
    # "Booru-like tag list": "Write a long list of Booru-like tags for this image.",
    "Booru-like tag list": "Output only a comma-separated list of tags for this image. Place a space after each comma. Do not include any introductory text, explanations, markdown formatting, line breaks, or section headers. Do not use labels like 'Character & Appearance:', 'Action & Scene:', or 'Art Style & Detailing:'. Just the raw tags. Example: nurse, catgirl, fox girl, anime, female, black hair, white uniform, blush, smile, cute, medical, fetish",
    "Art Critic": "Analyze this image like an art critic would with information about its composition, style, symbolism, the use of color, light, any artistic movement it might belong to, etc. Keep it long.",
    "Product Listing": "Write a long caption for this image as though it were a product listing.",
    "Social Media Post": "Write a long caption for this image as if it were being used for a social media post.",
    "Explicit Adult (NSFW)": "Generate an explicit and vivid caption for this image that accurately describes all visible elements, including sexual or violent actions. Use intense language typical of adult themes. Match the intensity of your description to what is actually shown. Don't sanitize explicit content, but also don't make innocent content sound more vulgar than it is. Ensure the tone aligns with the image's content and highlight specific details such as body positions, clothing, facial expressions, and any explicit acts.",
    "Text Extractor": "Extract and output ONLY the exact text visible in this image. Include text from speech bubbles, signs, labels, watermarks, or any other written content. Preserve capitalization, punctuation, and line breaks where visible. Do not describe the image, add commentary, or include any extra text. Output only the text found. If multiple text elements exist, list them in the order they appear, each on a new line. If no text is visible, output 'NO TEXT FOUND'.",
    "Questionary": "You are a precise image Q&A system. Answer the user's question about this image with the minimum words necessary. Use only factual information visible in the image. If the answer is yes/no, respond with only 'yes' or 'no'. If asking for a quantity, respond with only the number. If asking for a color, object, or attribute, respond with only the name. If the requested information is not visible in the image, respond with 'not visible'. Do not add explanations, complete sentences, or any extra text. Do not describe the image. Answer only what was asked.",
    "Interpretation": "You are an analytical image Q&A system. Answer the user's question about this image with clear, direct statements. Use evidence visible in the image to support your answer. Provide brief explanations when helpful. Use complete but concise sentences. If the answer involves ambiguity or artistic interpretation, acknowledge it briefly. If the requested information is not visible, state 'not visible' and suggest what might be inferred. Answer the question directly before adding any explanation.",
    "LLM": "You are describing this image to another language model that cannot see it. Your goal is to convey enough visual and contextual information so the other LLM understands the scene as if it had seen it. Focus on: the relationship between any text present (speech bubbles, signs, labels) and the visual elements. Explain abstract situations, implied actions, emotional tone, narrative tension, and any subtext that can be inferred. Describe character expressions, body language, spatial relationships, and environmental details. Connect the dots between what is seen and what it means. Assume the other LLM is intelligent but blind to the image. Be thorough but concise. Use natural language.",
}

# ============================================
# DEFAULT PROMPT - UNCOMMENT THE ONE YOU WANT
# ============================================

# DEFAULT_PROMPT_LABEL = "Descriptive"
# DEFAULT_PROMPT_LABEL = "Descriptive (Casual)"
# DEFAULT_PROMPT_LABEL = "Straightforward"
# DEFAULT_PROMPT_LABEL = "Stable Diffusion Prompt"
# DEFAULT_PROMPT_LABEL = "Danbooru tag list"
DEFAULT_PROMPT_LABEL = "e621 tag list"
# DEFAULT_PROMPT_LABEL = "Booru-like tag list"
# DEFAULT_PROMPT_LABEL = "Art Critic"
# DEFAULT_PROMPT_LABEL = "Product Listing"
# DEFAULT_PROMPT_LABEL = "Social Media Post"
# DEFAULT_PROMPT_LABEL = "Explicit Adult (NSFW)"
# DEFAULT_PROMPT_LABEL = "Text Extractor"
# DEFAULT_PROMPT_LABEL = "Questionary"
# DEFAULT_PROMPT_LABEL = "Interpretation"
# DEFAULT_PROMPT_LABEL = "LLM"

# ============================================
# OPTIONAL SETTINGS
# ============================================

EXTENSIONS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
TEMP = 0.2
DEFAULT_MAX_TOKENS = 300
TIMEOUT = 120

# Server health check settings
HEALTH_CHECK_TIMEOUT = 5

# ============================================
# SERVER FUNCTIONS
# ============================================

def check_server_health() -> bool:
    """Check if llama-server is running and accessible"""
    try:
        response = requests.get("http://127.0.0.1:6006/health", timeout=HEALTH_CHECK_TIMEOUT)
        return response.status_code == 200
    except requests.exceptions.ConnectionError:
        return False
    except Exception:
        return False


def get_prompt_text(prompt_label: str) -> str:
    """Return the long prompt text for a given label"""
    if prompt_label in PROMPTS:
        return PROMPTS[prompt_label]
    else:
        print(f"WARNING: Prompt label '{prompt_label}' not found. Using default '{DEFAULT_PROMPT_LABEL}'.")
        return PROMPTS.get(DEFAULT_PROMPT_LABEL, "")


def get_mime_type(image_path: str) -> str:
    """Determine mime type based on file extension"""
    ext = Path(image_path).suffix.lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.bmp': 'image/bmp'
    }
    return mime_types.get(ext, 'image/jpeg')


def caption_image_via_server(image_path: str, prompt_text: str,
                              server_url: str = SERVER_URL,
                              temp: float = TEMP,
                              max_tokens: int = DEFAULT_MAX_TOKENS,
                              timeout: int = TIMEOUT) -> str:
    """Send image to llama-server and return caption"""

    # Validate image exists
    if not os.path.exists(image_path):
        return f"ERROR: File not found: {image_path}"

    if not os.access(image_path, os.R_OK):
        return f"ERROR: File not readable: {image_path}"

    try:
        # Read and encode image
        with open(image_path, 'rb') as f:
            b64_image = base64.b64encode(f.read()).decode('utf-8')

        mime_type = get_mime_type(image_path)

        # Build request payload
        payload = {
            "model": "qwen3-vl-8b-abliterated",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}}
                    ]
                }
            ],
            "temperature": temp,
            "max_tokens": max_tokens,
            "stream": False
        }

        # Send request
        response = requests.post(server_url, json=payload, timeout=timeout)

        if response.status_code == 200:
            result = response.json()
            caption = result['choices'][0]['message']['content'].strip()
            return caption if caption else "No caption generated"
        else:
            return f"ERROR: HTTP {response.status_code} - {response.text[:200]}"

    except requests.exceptions.Timeout:
        return f"ERROR: Request timed out after {timeout} seconds"
    except requests.exceptions.ConnectionError:
        return f"ERROR: Cannot connect to llama-server at {server_url}. Is it running?"
    except Exception as e:
        return f"ERROR: {str(e)}"


def find_images(input_dir: str, extensions: List[str], recursive: bool = True) -> List[str]:
    """Find all images in directory matching extensions"""
    images = []
    pattern = "**/*" if recursive else "*"

    for ext in extensions:
        search_pattern = os.path.join(input_dir, pattern + ext)
        found = glob.glob(search_pattern, recursive=recursive)
        images.extend(found)

    return sorted(list(set(images)))


def is_image_file(filepath: str) -> bool:
    """Check if a file is an image based on extension"""
    return any(filepath.lower().endswith(ext) for ext in EXTENSIONS)


def process_single_image(image_path: str, prompt_text: str,
                         output_path: str = None, print_only: bool = False,
                         prompt_label: str = None,
                         max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Process a single image and return/save the caption"""
    filename = Path(image_path).name

    # Use provided prompt_label or default
    label = prompt_label if prompt_label else DEFAULT_PROMPT_LABEL

    print(f"Processing: {filename}")
    caption = caption_image_via_server(image_path, prompt_text, max_tokens=max_tokens)

    if print_only:
        print("\n" + "=" * 50)
        print("CAPTION:")
        print("=" * 50)
        print(caption)
        print("=" * 50)
    elif output_path:
        with open(output_path, 'w', encoding='utf-8') as out:
            # Write header
            out.write("=" * 60 + "\n")
            out.write(f"Image Caption Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            out.write(f"Model: Server (llama.cpp)\n")
            out.write(f"Input Image: {os.path.abspath(image_path)}\n")
            out.write(f"Prompt Label: {label}\n")
            out.write(f"Temperature: {TEMP}\n")
            out.write(f"Max Tokens: {max_tokens}\n")
            out.write("=" * 60 + "\n\n")
            out.write(f"{caption}\n")
        print(f"Caption saved to: {output_path}")
        print("\n" + "-" * 50)
        print("CAPTION:")
        print("-" * 50)
        print(caption)
        print("-" * 50)
    else:
        print("\n" + "-" * 50)
        print("CAPTION:")
        print("-" * 50)
        print(caption)
        print("-" * 50)

    return caption


def process_directory(input_dir: str, prompt_text: str, output_path: str,
                      prompt_label: str = None,
                      max_tokens: int = DEFAULT_MAX_TOKENS) -> Tuple[int, int]:
    """Process all images in a directory"""
    # Use provided prompt_label or default
    label = prompt_label if prompt_label else DEFAULT_PROMPT_LABEL

    print(f"Scanning for images in: {input_dir}")
    images = find_images(input_dir, EXTENSIONS, recursive=True)

    if not images:
        print(f"No images found with extensions: {', '.join(EXTENSIONS)}")
        return 0, 0

    print(f"Found {len(images)} images")
    print("\nStarting captioning...\n")

    success_count = 0
    failed_count = 0

    with open(output_path, 'w', encoding='utf-8') as out:
        out.write("=" * 60 + "\n")
        out.write(f"Image Captions Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write(f"Model: Server (llama.cpp)\n")
        out.write(f"Input Directory: {input_dir}\n")
        out.write(f"Prompt Label: {label}\n")
        out.write(f"Temperature: {TEMP}\n")
        out.write(f"Max Tokens: {max_tokens}\n")
        out.write("=" * 60 + "\n\n")

        for idx, image_path in enumerate(images, 1):
            filename = Path(image_path).name
            rel_path = os.path.relpath(image_path, input_dir)

            print(f"[{idx}/{len(images)}] Processing: {rel_path}")

            caption = caption_image_via_server(image_path, prompt_text, max_tokens=max_tokens)

            out.write("-" * 50 + "\n")
            out.write(f"FILE: {filename}\n")
            out.write(f"PATH: {rel_path}\n")
            out.write("\n")
            out.write(f"{caption}\n")
            out.write("\n")
            out.flush()

            if caption.startswith("ERROR"):
                failed_count += 1
                print(f"  ✗ Failed: {caption[:100]}")
            else:
                success_count += 1
                preview = caption[:60] + "..." if len(caption) > 60 else caption
                preview = preview.replace('\n', ' ')
                print(f"  ✓ {preview}")

        out.write("=" * 60 + "\n")
        out.write(f"PROCESSING COMPLETE\n")
        out.write(f"Total images: {len(images)}\n")
        out.write(f"Successful: {success_count}\n")
        out.write(f"Failed: {failed_count}\n")
        out.write("=" * 60 + "\n")

    return success_count, failed_count


def print_server_instructions():
    """Print instructions for starting the server"""
    print("\n" + "!" * 60)
    print("ERROR: llama-server is not running!")
    print("!" * 60)
    print("\nPlease start the server in a separate terminal with:")
    print()
    print("  llama-server \\")
    print("    -m /home/tekakutli/code/llama-models/Qwen3-VL-8B-Instruct-abliterated-v2.Q5_K_M.gguf \\")
    print("    --mmproj /home/tekakutli/code/llama-models/Qwen3-VL-8B-Instruct-abliterated-v2.mmproj-f16.gguf \\")
    print("    --host 127.0.0.1 \\")
    print("    --port 6006 \\")
    print("    -c 8192 \\")
    print("    --temp 0.2 \\")
    print("    --cache-type-k q8_0 \\")
    print("    --cache-type-v q8_0 \\")
    print("    -ngl 999 \\")
    print("    --repeat-penalty 1.1 \\")
    print("    --repeat-last-n 64 \\")
    print("    --top-p 0.9 \\")
    print("    --top-k 40 \\")
    print("    --min-p 0.05")
    print()
    print("Once the server is running, run this script again.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='Caption images using llama-server (persistent API)',
        epilog='Examples:\n'
               '  python3 caption_server.py /path/to/image.jpg\n'
               '  python3 caption_server.py /path/to/image.jpg --prompt "Art Critic"\n'
               '  python3 caption_server.py /path/to/image.jpg --prompt "Art Critic" --append "Focus on colors"\n'
               '  python3 caption_server.py /path/to/image.jpg --prompt "Art Critic" --word-limit 200\n'
               '  python3 caption_server.py /path/to/image.jpg --prompt "Art Critic" --max-tokens 500\n'
               '  python3 caption_server.py /path/to/image.jpg --prompt "Art Critic" --word-limit 200 --max-tokens 500\n'
               '  python3 caption_server.py /path/to/images/\n'
               '  python3 caption_server.py /path/to/images/ --prompt "Danbooru tag list"\n'
               '  python3 caption_server.py --status'
    )
    parser.add_argument('input_path', nargs='?', default=None,
                        help='Image file or directory containing images')
    parser.add_argument('--prompt', '-p', type=str, default=None,
                        help=f'Prompt label (default: {DEFAULT_PROMPT_LABEL})')
    parser.add_argument('--append', '-a', type=str, default=None,
                        help='Additional text to append to the prompt (e.g., "Focus on the background")')
    parser.add_argument('--word-limit', '-w', type=int, default=None,
                        help='Keep response under specified number of words (e.g., -w 200)')
    parser.add_argument('--output', '-o', type=str, default=None,
                        help='Output file path (default: auto-generated based on input)')
    parser.add_argument('--print-only', action='store_true',
                        help='Print caption to console only (don\'t save to file)')
    parser.add_argument('--status', '-s', action='store_true',
                        help='Check if server is running and exit')
    parser.add_argument('--max-tokens', '-n', type=int, default=None,
                        help=f'Maximum tokens to generate (default: {DEFAULT_MAX_TOKENS})')

    args = parser.parse_args()

    # Determine max_tokens
    max_tokens = args.max_tokens if args.max_tokens is not None else DEFAULT_MAX_TOKENS

    # Status check mode
    if args.status:
        if check_server_health():
            print("✓ llama-server is running and healthy")
            return 0
        else:
            print("✗ llama-server is not running")
            print_server_instructions()
            return 1

    # Check if server is running before proceeding
    if not check_server_health():
        print_server_instructions()
        return 1

    # Determine prompt label and text
    prompt_label = args.prompt if args.prompt is not None else DEFAULT_PROMPT_LABEL
    prompt_text = get_prompt_text(prompt_label)

    if not prompt_text:
        print(f"ERROR: No prompt text found for label '{prompt_label}'.")
        return 1

    # Append additional text if provided
    if args.append:
        prompt_text = f"{prompt_text}\n\n{args.append}"

    # Add word limit instruction if provided
    if args.word_limit:
        word_instruction = f" Keep your response under {args.word_limit} words."
        prompt_text = f"{prompt_text}{word_instruction}"

    # Handle no input path (interactive single image mode)
    if args.input_path is None:
        print("No input provided. Enter image path: ", end='')
        image_path = input().strip()
        if not image_path:
            print("ERROR: No image path provided.")
            return 1
        args.input_path = image_path

    input_path = os.path.abspath(args.input_path)

    if not os.path.exists(input_path):
        print(f"ERROR: Path not found: {input_path}")
        return 1

    # Print configuration
    print("=" * 60)
    print(f"Server: {SERVER_URL}")
    print(f"Input Path: {input_path}")
    print(f"Prompt Label: {prompt_label}")
    if args.append:
        print(f"Appended Text: {args.append}")
    if args.word_limit:
        print(f"Word Limit: {args.word_limit}")
    print(f"Temperature: {TEMP}")
    print(f"Max Tokens: {max_tokens}")
    print("=" * 60)
    print()

    # Determine if input is a single image or directory
    single_image = os.path.isfile(input_path) and is_image_file(input_path)

    if single_image:
        # Determine output path
        if args.print_only:
            output_path = None
        elif args.output:
            output_path = args.output
        else:
            image_dir = os.path.dirname(input_path)
            image_name = Path(input_path).stem
            output_path = os.path.join(image_dir, f"{image_name}_caption.txt")

        process_single_image(input_path, prompt_text, output_path, args.print_only,
                            prompt_label, max_tokens)

    else:
        # It's a directory (or should be treated as one)
        if not os.path.isdir(input_path):
            print(f"ERROR: Path is not a directory: {input_path}")
            return 1

        if args.print_only:
            print("ERROR: --print-only not supported for directories")
            return 1

        if args.output:
            output_path = args.output
        else:
            output_path = os.path.join(input_path, f"captions_{Path(input_path).name}.txt")

        success, failed = process_directory(input_path, prompt_text, output_path,
                                           prompt_label, max_tokens)

        print("\n" + "=" * 50)
        print("COMPLETE!")
        print(f"Total images processed: {success + failed}")
        print(f"Successful: {success}")
        print(f"Failed: {failed}")
        print(f"Output saved to: {output_path}")
        print("=" * 50)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(130)
