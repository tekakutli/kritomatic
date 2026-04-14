# kritomatic_daemon/__init__.py

import os
import subprocess
from pathlib import Path

# ==============================================================================
# KRITOMATIC XREMAP SELF-INITIALIZATION
# ==============================================================================
# NOTE: We avoid configuring this solely through systemd units because system-
# level startup often lacks the necessary environment context (session paths,
# window manager sockets, and local variables) required for Xremap to interact
# with Krita.
#
# PURPOSE:
# This function acts as the "bridge" between the system and this plugin. By
# triggering 'setup_links.sh' from the daemon, we ensure that:
# 1. Absolute paths for all Kritomatic methods are correctly mapped.
# 2. The Xremap service inherits the active user session permissions.
# 3. The configuration is refreshed to match the current location of the repo.
#
# This ensures that all custom shortcuts and plugin interactions work
# immediately upon Krita launching, without requiring manual system setup.
# ==============================================================================

def sync_systemd_environment():
    """
    Triggers setup_links.sh to refresh systemd units with the current
    graphical environment variables (Wayland/X11).
    """
    try:
        # Path: .../kritomatic/kritomatic_daemon/__init__.py
        # Target: .../kritomatic/kritomatic_xremap/setup_links.sh
        current_dir = Path(__file__).parent.resolve()
        setup_script = current_dir.parent / "kritomatic_xremap" / "setup_links.sh"

        if setup_script.exists():
            print(f"🔄 Krita detected. Synchronizing xremap environment via {setup_script}...")

            subprocess.run(["/bin/bash", str(setup_script)],
                           check=True,
                           capture_output=True,
                           text=True)
            print("✅ Environment synced successfully.")
        else:
            print(f"⚠️ Warning: Could not find setup_links.sh at {setup_script}")

    except Exception as e:
        print(f"❌ Failed to sync environment: {e}")

# Execute immediately when plugin loads
sync_systemd_environment()

from .kritomatic_daemon import KritomaticDaemon
from .decorators import command
from .registry import get_command_registry, register_command

Krita.instance().addExtension(KritomaticDaemon(Krita.instance()))
