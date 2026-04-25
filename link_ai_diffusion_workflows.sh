#!/bin/bash
# Get the directory where this script is located (kritomatic project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Remove existing workflows directory
rm -rf "$HOME/.local/share/krita/ai_diffusion/workflows"

# Create symlink
ln -s "$SCRIPT_DIR/data/workflows" "$HOME/.local/share/krita/ai_diffusion/workflows"

echo "✓ Symlink created: $HOME/.local/share/krita/ai_diffusion/workflows -> $SCRIPT_DIR/data/workflows"
