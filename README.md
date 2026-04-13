# Kritomatic

Control Krita from the command line.

## Installation

This is the CLI:
```bash
pipx install -e .
```

You need to install the plugin for Krita:

```bash
# Set variables for paths
SOURCE_DIR="/path/to/where/is/kritomatic"
TARGET_BASE="$HOME/.local/share/krita/pykrita"

# Create symlinks
ln -s "$SOURCE_DIR/kritomatic/kritomatic_daemon" "$TARGET_BASE/kritomatic_daemon"
ln -s "$SOURCE_DIR/kritomatic/kritomatic_daemon/kritomatic_daemon.desktop" "$TARGET_BASE/kritomatic_daemon.desktop"

```
and enable it in Krita/Settings/Configure Krita/Plugin Manager/Kritomatic Daemon
