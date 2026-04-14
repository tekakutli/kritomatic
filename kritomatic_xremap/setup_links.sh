#!/bin/bash

# --- 1. DETECT ENVIRONMENT ---
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
ENV_DIR="$HOME/.config/xremap"
ENV_FILE="$ENV_DIR/xremap_kritomatic.env"

echo "🛠️  Kritomatic Xremap: Initializing Portable Setup..."

# --- 2. ADMINISTER ENVIRONMENT FILE ---
# This ensures we don't erase your TARGET_DEVICE or PERSONAL_CONFIG
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    echo "📄 Creating new .env file at $ENV_FILE"
    cat <<EOF > "$ENV_FILE"
REPODIR="$REPO_DIR"
PERSONAL_CONFIG="$HOME/xremap_config.yaml"
TARGET_DEVICE="kanata ministeren"
EOF
else
    echo "🔄 Syncing REPODIR while preserving your settings..."
    # Update REPODIR path in case you moved the folder, but leave others alone
    sed -i "s|^REPODIR=.*|REPODIR=\"$REPO_DIR\"|" "$ENV_FILE"
fi

# --- 3. INSTALL SYSTEMD UNITS ---
echo "🔗 Injecting paths into systemd units..."

# Clear old units and ensure they aren't masked
systemctl --user stop xremap-kritomatic.path xremap-kritomatic.service 2>/dev/null
systemctl --user unmask xremap-kritomatic.service 2>/dev/null
systemctl --user unmask xremap-kritomatic.path 2>/dev/null

# 1. Generate Service Unit from template (Injects current REPO_DIR)
if [ -f "$REPO_DIR/xremap-kritomatic.service" ]; then
    sed "s|REPODIR_PLACEHOLDER|$REPO_DIR|g" "$REPO_DIR/xremap-kritomatic.service" > "$SYSTEMD_USER_DIR/xremap-kritomatic.service"
else
    echo "❌ Error: xremap-kritomatic.service template missing!"
    exit 1
fi

# 2. Generate Path Unit from template (Injects current REPO_DIR)
if [ -f "$REPO_DIR/xremap-kritomatic.path" ]; then
    sed "s|REPODIR_PLACEHOLDER|$REPO_DIR|g" "$REPO_DIR/xremap-kritomatic.path" > "$SYSTEMD_USER_DIR/xremap-kritomatic.path"
else
    echo "❌ Error: xremap-kritomatic.path template missing!"
    exit 1
fi

# --- 4. ACTIVATE ---
echo "🚀 Activating and performing live reload..."
systemctl --user daemon-reload
systemctl --user stop xremap-kritomatic.path
systemctl --user enable xremap-kritomatic.path
systemctl --user enable xremap-kritomatic.service

# Force restart triggers the merger immediately (No 'touch' needed)
systemctl --user restart xremap-kritomatic.path
systemctl --user restart xremap-kritomatic.service

if systemctl --user is-active --quiet xremap-kritomatic.service; then
    echo "✅ Success! System is portable and active."
    echo "📍 Environment: $ENV_FILE"
else
    echo "⚠️  Service failed to start. Check: journalctl --user -u xremap-kritomatic.service -f"
fi
