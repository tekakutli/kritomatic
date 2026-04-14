#!/bin/bash

# --- 1. DETECT ENVIRONMENT ---
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
ENV_DIR="$HOME/.config/xremap"
ENV_FILE="$ENV_DIR/xremap_kritomatic.env"
SERVICE_FILE="$SYSTEMD_USER_DIR/xremap-kritomatic.service"

echo "🛠️  Kritomatic Xremap: Initializing Decoupled Setup..."

# --- 2. ADMINISTER ENVIRONMENT FILE ---
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    echo "📄 Creating new .env file at $ENV_FILE"
    cat <<EOF > "$ENV_FILE"
REPODIR="$REPO_DIR"
PERSONAL_CONFIG="$HOME/xremap_config.yaml"
TARGET_DEVICE="kanata"
EOF
else
    echo "🔄 Syncing REPODIR while preserving your settings..."
    sed -i "s|^REPODIR=.*|REPODIR=\"$REPO_DIR\"|" "$ENV_FILE"
fi

# --- 3. INSTALL SYSTEMD UNITS (With Hash Check) ---
# Create a unique fingerprint of the current path/user
CURRENT_STATE_HASH=$(echo "$REPO_DIR-$USER" | md5sum | cut -d' ' -f1)
INSTALLED_HASH=$(grep "# STATE_HASH:" "$SERVICE_FILE" 2>/dev/null | cut -d':' -f2 | xargs)

if [ "$CURRENT_STATE_HASH" != "$INSTALLED_HASH" ]; then
    echo "🔗 Injecting paths and linking units..."
    mkdir -p "$SYSTEMD_USER_DIR"

    # 1. Main Service (Inject REPODIR + HASH)
    sed "s|REPODIR_PLACEHOLDER|$REPO_DIR|g" "$REPO_DIR/xremap-kritomatic.service" > "$SERVICE_FILE"
    echo "# STATE_HASH: $CURRENT_STATE_HASH" >> "$SERVICE_FILE"

    # 2. Path Unit
    sed "s|REPODIR_PLACEHOLDER|$REPO_DIR|g" "$REPO_DIR/xremap-kritomatic.path" > "$SYSTEMD_USER_DIR/xremap-kritomatic.path"

    # 3. Restart Service (Ghost Middleman)
    cp "$REPO_DIR/xremap-kritomatic-restart.service" "$SYSTEMD_USER_DIR/xremap-kritomatic-restart.service"

    echo "🚀 Hard resetting systemd units (Path Changed)..."
    systemctl --user daemon-reload
    systemctl --user enable xremap-kritomatic.service xremap-kritomatic.path
    systemctl --user disable xremap-kritomatic-restart.service 2>/dev/null
else
    echo "⚡ Paths match. Skipping systemd re-injection."
fi

# --- 4. ACTIVATE (Fast Parallel Restart) ---
echo "🔄 Refreshing Merger..."

# Use '--no-block' to avoid waiting for the operation to complete
# Run both restarts in background for parallel execution
systemctl --user try-restart --no-block xremap-kritomatic.service &
systemctl --user try-restart --no-block xremap-kritomatic.path &
wait

# --- 5. VERIFICATION (Non-blocking, quiet) ---
# Small delay to let services settle
sleep 0.2

if systemctl --user is-active --quiet xremap-kritomatic.service; then
    echo "✅ Setup Complete. Service is running."
else
    # One more check - maybe it's still starting
    sleep 0.3
    if systemctl --user is-active --quiet xremap-kritomatic.service; then
        echo "✅ Setup Complete. Service is running."
    else
        echo "⚠️  Service not active. Check: systemctl --user status xremap-kritomatic.service"
    fi
fi
