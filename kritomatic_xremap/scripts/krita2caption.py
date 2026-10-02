#!/usr/bin/env python3
"""
Extract visible text from Krita (.kra) files without opening the application.
This version properly handles Krita's layer path structure including the "Unnamed/" prefix.
"""

import zipfile
import xml.etree.ElementTree as ET
import os
import sys
from pathlib import Path
import argparse
from typing import List, Dict, Set, Tuple

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
                    import re
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

def process_multiple_files(kra_files: List[Path], verbose: bool = False, output_file: Path = None):
    """
    Process multiple .kra files and output their visible text.
    """
    all_results = {}

    for kra_path in kra_files:
        if not kra_path.exists():
            print(f"Warning: File not found: {kra_path}")
            continue

        result = extract_visible_text_from_kra(kra_path, verbose)
        if result:
            all_results[str(kra_path)] = result

    # Output results
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

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(output_content)
        print(f"\n✓ Results written to: {output_file}")
    else:
        print(output_content)

    return all_results

def main():
    parser = argparse.ArgumentParser(
        description='Extract visible text from Krita (.kra) files without opening the application'
    )
    parser.add_argument('files', nargs='+',
                       help='Path to .kra file(s) or directory containing .kra files')
    parser.add_argument('-v', '--verbose', action='store_true',
                       help='Show detailed processing information')
    parser.add_argument('-o', '--output', type=Path,
                       help='Output file path (default: print to console)')
    parser.add_argument('-r', '--recursive', action='store_true',
                       help='Recursively search directories for .kra files')

    args = parser.parse_args()

    # Collect all .kra files
    kra_files = []
    for path_str in args.files:
        path = Path(path_str)
        if path.is_file() and path.suffix.lower() == '.kra':
            kra_files.append(path)
        elif path.is_dir():
            if args.recursive:
                kra_files.extend(path.rglob('*.kra'))
                kra_files.extend(path.rglob('*.KRA'))
            else:
                kra_files.extend(path.glob('*.kra'))
                kra_files.extend(path.glob('*.KRA'))
        else:
            print(f"Warning: {path} is not a .kra file or directory")

    if not kra_files:
        print("No .kra files found to process")
        sys.exit(1)

    print(f"Found {len(kra_files)} .kra file(s) to process")

    # Process the files
    register_namespaces()
    process_multiple_files(kra_files, args.verbose, args.output)

if __name__ == "__main__":
    main()
