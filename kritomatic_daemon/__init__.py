# kritomatic_daemon/__init__.py
#
# Krita plugin entry point.
#
# The bottom of this file registers the daemon with Krita. The rest of
# the module is only meaningful inside Krita. Everything below the
# `if _KRITA_AVAILABLE:` guard runs on import when Krita loads the
# plugin; everything above it runs everywhere, harmlessly.
#
# The guard exists so the module can be imported from a plain Python
# interpreter — for example, by a small helper script that wants to
# reach the daemon's event stream — without triggering Krita-only
# side effects (systemd sync, addExtension).

import os
import subprocess
from pathlib import Path


def sync_systemd_environment():
    """Refresh systemd units with the current graphical environment."""
    try:
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


try:
    from krita import Krita  # noqa: F401
    _KRITA_AVAILABLE = True
except ImportError:
    _KRITA_AVAILABLE = False


if _KRITA_AVAILABLE:
    sync_systemd_environment()

    from .kritomatic_daemon import KritomaticDaemon
    from .decorators import command
    from .registry import get_command_registry, register_command

    Krita.instance().addExtension(KritomaticDaemon(Krita.instance()))
