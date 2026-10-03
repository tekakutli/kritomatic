#!/usr/bin/env python3
"""
krita2caption.py

Extract visible text from Krita (.kra) files without opening the application.

A .kra file is a zip archive containing a maindoc.xml that describes the
layer structure, plus per-layer content.svg files that hold the actual
shapes and text. This script:
  1. Parses maindoc.xml to learn each layer's visibility and opacity.
  2. Reads the content.svg of each visible shape/text layer.
  3. Extracts the text content from svg:text, svg:tspan, and svg:flowRoot
     elements.
  4. Writes the results to a file, and optionally also prints them to stdout.

Input is a list of paths, taken from the KRA_PATHS variable or from the
positional arguments (which override the variable). The list must be one of:

  - A single directory (its .kra files are processed, recursively if
    RECURSIVE).
  - A single .kra file.
  - A list of two or more .kra files. Nothing else — no directories, no
    non-.kra files — may appear alongside them.

Output:

  - File output is always produced. The path is chosen as follows:
      1. If OUTPUT_FILE is non-empty: use it verbatim.
      2. Else: derive the path from the input.
         - Single directory input:
             "<directory_name><OUTPUT_SUFFIX_MANY>" inside that directory.
         - Single .kra file input:
             "<file_stem><OUTPUT_SUFFIX_SINGLE>" next to the file.
         - Multiple .kra file input:
             "<random_string><OUTPUT_SUFFIX_MANY>" next to the first file.
           The random string makes each run's output uniquely named.
         Examples:
           ~/krita_projects/       -> ~/krita_projects/krita_projects_captions.txt
           ~/projects/drawing.kra  -> ~/projects/drawing_caption.txt
           [a.kra, b.kra]          -> ~/projects/a3f9b2c1_captions.txt
  - Additionally, if --stdout / -s is passed, the same content is also
    printed to stdout. The flag does not affect file output.

Behavior is controlled by variables in the CONFIGURABLE SETTINGS block:

  - KRA_PATHS:            List of inputs (see above). Overridden by
                          positional arguments.
  - RECURSIVE:            If True, search directories recursively for .kra
                          files.
  - OUTPUT_FILE:          Explicit output path. Empty = derive from input.
  - OUTPUT_SUFFIX_SINGLE: Suffix when the input is a single file.
  - OUTPUT_SUFFIX_MANY:   Suffix when the input is a directory or a list of
                          multiple .kra files.
  - VERBOSE:              If True, print detailed per-layer processing
                          information.

Usage:
    python krita2caption.py [--stdout] [path ...]

Examples:
    # Use KRA_PATHS variable
    python krita2caption.py

    # Single .kra file
    python krita2caption.py drawing.kra

    # Single directory
    python krita2caption.py ~/krita_projects/

    # Multiple .kra files (no directories allowed alongside)
    python krita2caption.py a.kra b.kra c.kra

    # Also print to stdout (file is still written)
    python krita2caption.py --stdout drawing.kra
"""

import zipfile
import xml.etree.ElementTree as ET
import os
import sys
import secrets
from pathlib import Path
import argparse
import re
from typing import List, Dict, Set, Tuple

# ===== CONFIGURABLE SETTINGS =====
# Input list. Overridden by positional arguments if any are provided.
# Must be one of: [single_directory], [single_kra_file], or [kra_file, ...].
KRA_PATHS = []                     # e.g. ["~/krita_projects/"] or ["a.kra", "b.kra"]

# Search directories recursively for .kra files
RECURSIVE = False

# Explicit output file path. If empty, the path is derived from the input
# (see module docstring).
OUTPUT_FILE = ""                   # e.g. "/tmp/krita_text.txt"

# Suffix used when deriving the output filename (only when OUTPUT_FILE is empty).
# Picked based on the input kind.
OUTPUT_SUFFIX_SINGLE = "_caption.txt"
OUTPUT_SUFFIX_MANY = "_captions.txt"

# Verbose processing output
VERBOSE = False
# =================================

# XML namespaces used in Krita files
NAMESPACES = {
    'svg': 'http://www.w3.org/2000/svg',
    'krita': 'http://krita.org/namespace',
}

def register_namespaces():
    """Register namespaces with ElementTree for proper parsing"""
    for prefix, uri in NAMESPACES.items():
        ET.register_namespace(prefix, uri)

def parse_maindoc(zip_file) -> Dict[str, dict]:
    """
    Parse maindoc.xml to get layer structure and visibility information.
    Returns a dictionary mapping layer filenames to their properties.
    """
    try:
        with zip_file.open('maindoc.xml') as f:
            tree = ET.parse(f)
            root = tree.getroot()
    except KeyError:
        print("Error: maindoc.xml not found in .kra file")
        return {}
    except ET.ParseError as e:
        print(f"Error parsing maindoc.xml: {e}")
        return {}

    layers_info = {}

    # Find all layer nodes (recursive)
    def process_layer(layer_elem, parent_visible=True, parent_opacity=1.0):
        layer_type = layer_elem.get('type', '')
        layer_name = layer_elem.get('name', 'Unnamed')

        # Get visibility status (visible unless explicitly '0')
        visible = parent_visible and layer_elem.get('visible', '1') == '1'

        # Get opacity (0.0 to 1.0, default 1.0)
        try:
            opacity = float(layer_elem.get('opacity', '1.0')) * parent_opacity
        except ValueError:
            opacity = parent_opacity

        # Store layer info if it's a shape/text layer
        if 'shapelayer' in layer_type:
            # Find the layer file path - this can be in various places
            filename = layer_elem.get('filename')
            if filename:
                # The filename might be relative (e.g., "layers/layer4.shapelayer/content.svg")
                # or might need the prefix
                layers_info[filename] = {
                    'name': layer_name,
                    'visible': visible,
                    'opacity': opacity,
                    'type': layer_type
                }

        # Process child layers
        for child in layer_elem:
            if child.tag.endswith('layer') or child.tag.endswith('group'):
                process_layer(child, visible, opacity)

    # Start from the root document
    # The root might have a different structure, so look for any layer container
    document = root.find('.//*[@name="document"]') or root
    for layer in document.findall('.//*[@filename]'):
        process_layer(layer)

    # Also find any layer that might not have filename attribute yet
    for layer in document.findall('.//*[@type="shapelayer"]'):
        if 'filename' not in layer.attrib:
            # Try to find filename from 'name' or construct it
            layer_name = layer.get('name', '')
            # Some Krita versions store the path differently
            pass

    return layers_info

def extract_text_from_svg(svg_content: bytes) -> List[str]:
    """
    Extract text content from SVG file bytes.
    Returns list of visible text strings found in the SVG.
    """
    try:
        tree = ET.fromstring(svg_content)
    except ET.ParseError as e:
        print(f"Warning: Could not parse SVG: {e}")
        return []

    text_pieces = []

    # Find all text elements
    for text_elem in tree.findall('.//svg:text', NAMESPACES):
        # Get all text content including nested elements
        text_content = []

        # Get direct text
        if text_elem.text:
            text_content.append(text_elem.text)

        # Check for tspan elements
        for tspan in text_elem.findall('.//svg:tspan', NAMESPACES):
            if tspan.text:
                text_content.append(tspan.text)
            if tspan.tail:
                text_content.append(tspan.tail)

        full_text = ' '.join(text_content).strip()
        if full_text:
            text_pieces.append(full_text)

    # Also check for text in flowRoot elements (used for text boxes)
    for flowRoot in tree.findall('.//svg:flowRoot', NAMESPACES):
        flow_text = []
        for flowPara in flowRoot.findall('.//svg:flowPara', NAMESPACES):
            if flowPara.text:
                flow_text.append(flowPara.text)
            if flowPara.tail:
                flow_text.append(flowPara.tail)
        full_text = ' '.join(flow_text).strip()
        if full_text:
            text_pieces.append(full_text)

    return text_pieces

def extract_visible_text_from_kra(kra_path: Path, verbose: bool = False) -> Dict[str, List[str]]:
    """
    Extract all visible text from a single .kra file.
    Returns a dictionary mapping layer filenames to their extracted text.
    """
    result = {}

    try:
        with zipfile.ZipFile(kra_path, 'r') as zip_file:
            # Get layer visibility information
            layers_info = parse_maindoc(zip_file)

            if verbose:
                print(f"\nProcessing: {kra_path.name}")
                print(f"  Found {len(layers_info)} shape layers in maindoc")

            # Find all layer files (content.svg files) - look anywhere in the archive
            layer_files = [f for f in zip_file.namelist()
                          if f.endswith('content.svg') and ('shapelayer' in f or 'textlayer' in f)]

            if verbose:
                print(f"  Found {len(layer_files)} SVG content files in archive")

            for layer_file in layer_files:
                # Try to find corresponding layer info (exact match or by basename)
                layer_info = layers_info.get(layer_file, {})

                # If not found by exact path, try to find by the shapelayer directory name
                if not layer_info:
                    # Extract the shapelayer directory name (e.g., "layer4.shapelayer")
                    match = re.search(r'([^/]+\.shapelayer)/', layer_file)
                    if match:
                        shapelayer_name = match.group(1)
                        for key, info in layers_info.items():
                            if shapelayer_name in key:
                                layer_info = info
                                break

                is_visible = layer_info.get('visible', True)

                # Skip invisible layers
                if not is_visible:
                    if verbose:
                        print(f"  Skipping invisible layer: {layer_info.get('name', layer_file)}")
                    continue

                # Check opacity threshold (text at < 10% opacity is effectively invisible)
                opacity = layer_info.get('opacity', 1.0)
                if opacity < 0.1:
                    if verbose:
                        print(f"  Skipping low-opacity layer: {layer_info.get('name', layer_file)} ({opacity:.0%})")
                    continue

                # Extract text from the SVG
                try:
                    with zip_file.open(layer_file) as f:
                        svg_content = f.read()
                        text_pieces = extract_text_from_svg(svg_content)

                        if text_pieces:
                            result[layer_file] = {
                                'layer_name': layer_info.get('name', layer_file.split('/')[-2] if '/' in layer_file else layer_file),
                                'text': text_pieces,
                                'opacity': opacity
                            }
                            if verbose:
                                print(f"  ✓ Extracted text from '{result[layer_file]['layer_name']}': {text_pieces}")
                except Exception as e:
                    if verbose:
                        print(f"  Error extracting {layer_file}: {e}")

    except zipfile.BadZipFile:
        print(f"Error: {kra_path} is not a valid .kra file (bad zip format)")
    except Exception as e:
        print(f"Error processing {kra_path}: {e}")

    return result

def process_multiple_files(kra_files: List[Path], verbose: bool = False, output_file: Path = None,
                          to_stdout: bool = False):
    """
    Process the collected .kra files and output their visible text.

    File writing and stdout printing are independent: the file is written
    whenever output_file is set, and the content is additionally printed to
    stdout whenever to_stdout is True.
    """
    all_results = {}

    for kra_path in kra_files:
        if not kra_path.exists():
            print(f"Warning: File not found: {kra_path}")
            continue

        result = extract_visible_text_from_kra(kra_path, verbose)
        if result:
            all_results[str(kra_path)] = result

    # Build output content
    output_lines = []

    for file_path, layers in all_results.items():
        output_lines.append(f"\n{'='*60}")
        output_lines.append(f"📄 File: {Path(file_path).name}")
        output_lines.append(f"{'='*60}")

        found_text = False
        for layer_file, layer_data in layers.items():
            output_lines.append(f"\n  📝 Layer: {layer_data['layer_name']}")
            if layer_data['opacity'] < 1.0:
                output_lines.append(f"     Opacity: {layer_data['opacity']:.0%}")
            for text in layer_data['text']:
                output_lines.append(f"     Text: {text}")
                found_text = True

        if not found_text:
            output_lines.append("\n  No visible text found in this file")

    output_content = '\n'.join(output_lines)

    # Write to file (independent of stdout)
    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(output_content)
        print(f"\n✓ Results written to: {output_file}")

    # Print to stdout (independent of file writing)
    if to_stdout:
        print(output_content)

    return all_results

def collect_kra_files(input_paths: List[str], recursive: bool) -> List[Path]:
    """
    Expand the given input list into a list of .kra files.

    Allowed shapes:
      - [directory]                  -> glob (.kra and .KRA; rglob if recursive)
      - [single .kra file]           -> that file
      - [kra_file, kra_file, ...]    -> those files (no directories allowed)

    Anything else is an error and returns an empty list.
    """
    # Single input: directory or .kra file
    if len(input_paths) == 1:
        single = Path(input_paths[0]).expanduser()

        if single.is_dir():
            if recursive:
                return list(single.rglob('*.kra')) + list(single.rglob('*.KRA'))
            return list(single.glob('*.kra')) + list(single.glob('*.KRA'))

        if single.is_file() and single.suffix.lower() == '.kra':
            return [single]

        print(f"Error: {single} is neither a .kra file nor a directory")
        return []

    # Multiple inputs: all must be .kra files
    kra_files = []
    for path_str in input_paths:
        path = Path(path_str).expanduser()
        if not (path.is_file() and path.suffix.lower() == '.kra'):
            print(f"Error: {path} is not a .kra file "
                  f"(multiple inputs must all be .kra files)")
            return []
        kra_files.append(path)
    return kra_files

def derive_output_path(input_paths: List[str]) -> Path:
    """
    Derive the output file path from the input list.

    Single directory input  -> "<directory_name><OUTPUT_SUFFIX_MANY>" inside it.
    Single .kra file input  -> "<file_stem><OUTPUT_SUFFIX_SINGLE>" next to it.
    Multiple .kra file input -> "<random_string><OUTPUT_SUFFIX_MANY>" next to
                                the first file.
    """
    first = Path(input_paths[0]).expanduser()

    # Multiple .kra files: random prefix, next to the first file
    if len(input_paths) > 1:
        target_dir = first.parent
        stem = secrets.token_hex(4)
        suffix = OUTPUT_SUFFIX_MANY
        return target_dir / f"{stem}{suffix}"

    # Single input: directory or file
    if first.is_dir():
        target_dir = first
        stem = target_dir.name or "output"
        suffix = OUTPUT_SUFFIX_MANY
    else:
        target_dir = first.parent
        stem = first.stem or "output"
        suffix = OUTPUT_SUFFIX_SINGLE

    return target_dir / f"{stem}{suffix}"

def resolve_output_file(input_paths: List[str]) -> Path:
    """
    Determine the output file path.

    If OUTPUT_FILE is non-empty, it's used verbatim (with ~ expansion).
    Otherwise, the path is derived from the input list.
    """
    if OUTPUT_FILE.strip():
        return Path(OUTPUT_FILE).expanduser()
    return derive_output_path(input_paths)

def main():
    parser = argparse.ArgumentParser(
        description='Extract visible text from Krita (.kra) files without opening the application. '
                    'Takes either a single directory, a single .kra file, or a list '
                    'of .kra files. Configure via variables in the script; positional '
                    'arguments override KRA_PATHS.'
    )
    parser.add_argument('inputs', nargs='*', default=None,
                        help='A directory, a .kra file, or several .kra files. '
                             'Overrides KRA_PATHS variable.')
    parser.add_argument('-s', '--stdout', action='store_true',
                        help='Also print results to stdout (does not affect file output)')
    args = parser.parse_args()

    # Resolve the input list: positional overrides variable
    if args.inputs:
        input_paths = args.inputs
    else:
        input_paths = KRA_PATHS

    if not input_paths:
        print("Error: No input paths provided (set KRA_PATHS or pass paths as arguments)")
        sys.exit(1)

    # Collect .kra files
    kra_files = collect_kra_files(input_paths, RECURSIVE)
    if not kra_files:
        print("No .kra files found to process")
        sys.exit(1)

    print(f"Found {len(kra_files)} .kra file(s) to process")

    # Resolve the output path independently of the stdout flag
    output_file = resolve_output_file(input_paths)

    # Process the files
    register_namespaces()
    process_multiple_files(
        kra_files,
        verbose=VERBOSE,
        output_file=output_file,
        to_stdout=args.stdout,
    )

if __name__ == "__main__":
    main()
