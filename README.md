# Kritomatic

Control Krita from the command line.

## Installation

First clone:
```bash
git clone https://github.com/tekakutli/kritomatic
```

Install the CLI:
```bash
cd kritomatic
pipx install -e .
```

Install the Krita plugin:

```bash
# Set variables for paths
SOURCE_DIR="/path/to/where/is/kritomatic"
TARGET_BASE="$HOME/.local/share/krita/pykrita"

# Create symlinks
ln -s "$SOURCE_DIR/kritomatic/kritomatic_daemon" "$TARGET_BASE/kritomatic_daemon"
ln -s "$SOURCE_DIR/kritomatic/kritomatic_daemon/kritomatic_daemon.desktop" "$TARGET_BASE/kritomatic_daemon.desktop"

```
and enable it in Krita/Settings/Configure Krita/Plugin Manager/Kritomatic Daemon  

And (optional) setup the xremap config:
```bash
yay -S go-yq python-pyinotify # THE GO-LANG VERSION
cd kritomatic/kritomatic_xremap
chmod +x setup_links.sh && ./setup_links.sh
```

and configure it: 
Edit `~/.config/xremap/xremap_kritomatic.env` to set your `TARGET_DEVICE` or `PERSONAL_CONFIG`. Copy there the one at `kritomatic/kritomatic_xremap/xremap_kritomatic.env`

> [!TIP]
> The setup script automatically handles pathing and systemd registration. Run it again if you move the folder.
