#!/usr/bin/env sh

# Set variables for paths
SOURCE_DIR="/path/to/where/is/kritomatic"
TARGET_BASE="$HOME/.local/share/krita/pykrita"

# Create symlinks
ln -s "$SOURCE_DIR/kritomatic/kritomatic_daemon" "$TARGET_BASE/kritomatic_daemon"
ln -s "$SOURCE_DIR/kritomatic/kritomatic_daemon/kritomatic_daemon.desktop" "$TARGET_BASE/kritomatic_daemon.desktop"

# ---- comfy-api-proxy venv (best-effort) -----------------------------------
#
# The daemon spawns comfy-api-proxy as a child process on 127.0.0.1:8189.
# It lives in its own venv because its dependencies (aiohttp, pydantic,
# blake3, PyYAML) are heavyweight and orthogonal to Krita's runtime.
#
# Best-effort here: if no Python 3.10+ is on PATH, skip the venv step and
# tell the user. They can run `comfy_proxy_install` inside Krita after
# installing a newer Python, or install the proxy manually with
# `pipx install comfy-api-proxy` (comfy_proxy_start finds it on PATH).
#
# If the venv already exists, this step is a no-op — re-running the
# installer to update symlinks does not reinstall the proxy.
PROXY_VERSION=$(cat "$SOURCE_DIR/kritomatic/kritomatic_daemon/proxy/PINNED_VERSION" 2>/dev/null || echo "0.1.0")
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kritomatic/comfy-api-proxy"

if [ ! -x "$DATA_DIR/venv/bin/comfy-api-proxy" ]; then
    PYTHON=""
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
                PYTHON="$candidate"
                break
            fi
        fi
    done
    if [ -n "$PYTHON" ]; then
        echo "→ Creating comfy-api-proxy venv at $DATA_DIR/venv ..."
        mkdir -p "$DATA_DIR"
        "$PYTHON" -m venv "$DATA_DIR/venv" && \
        "$DATA_DIR/venv/bin/pip" install --quiet "comfy-api-proxy==$PROXY_VERSION" && \
        echo "✓ comfy-api-proxy $PROXY_VERSION installed"
    else
        echo "⚠ No Python 3.10+ found; comfy-api-proxy venv not created."
        echo "  Install one, then run \`comfy_proxy_install\` inside Krita,"
        echo "  or use \`pipx install comfy-api-proxy==$PROXY_VERSION\`."
    fi
fi