#!/usr/bin/env python3
"""Setup/refresh script for the kritomatic xremap systemd service.

Safe to run any number of times:
  - Seeds ~/.config/kritomatic/bindings.yaml from bindings.example.yaml
    on first run. Never overwrites an existing bindings file.
  - Reports retired Phase-0 files (the old env file and personal config)
    as safely deletable; does not remove them automatically.
  - Makes the launcher executable if it isn't.
  - Validates the current bindings by delegating to
    `kritomatic-xremap.py validate`; refuses to touch the service if
    validation fails.
  - Re-injects REPODIR into the installed systemd units and re-installs
    them only if their contents or the repo path changed.
  - Refreshes the running service and polls (up to 15s) for it to come up.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

HOME = Path.home()
REPO_DIR = Path(__file__).resolve().parent

# Systemd user unit directory
SYSTEMD_USER_DIR  = HOME / ".config/systemd/user"
SERVICE_FILE      = SYSTEMD_USER_DIR / "xremap-kritomatic.service"
PATH_UNIT_FILE    = SYSTEMD_USER_DIR / "xremap-kritomatic.path"
RESTART_UNIT_FILE = SYSTEMD_USER_DIR / "xremap-kritomatic-restart.service"

# User-facing files (Phase 1 surface)
BINDINGS_DIR     = HOME / ".config" / "kritomatic"
BINDINGS_FILE    = BINDINGS_DIR / "bindings.yaml"
BINDINGS_EXAMPLE = REPO_DIR / "bindings.example.yaml"

# Launcher script (referenced by the installed .service)
LAUNCHER = REPO_DIR / "kritomatic-xremap.py"

# Retired Phase-0 files: reported as deletable, never removed here
LEGACY_ENV_FILE        = HOME / ".config" / "xremap" / "xremap_kritomatic.env"
LEGACY_PERSONAL_CONFIG = HOME / "xremap_config.yaml"


# ----------------------------------------------------------------- helpers

def list_devices() -> list[str]:
    """Return device names reported by `xremap --list-devices`."""
    try:
        out = subprocess.check_output(
            ["xremap", "--list-devices"], text=True, stderr=subprocess.DEVNULL
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return []
    names = []
    for line in out.splitlines():
        if ":" in line:
            names.append(line.split(":", 1)[1].strip())
    return sorted(set(names))


def seed_bindings() -> bool:
    """Copy bindings.example.yaml -> ~/.config/kritomatic/bindings.yaml
    if the destination does not exist. Returns True if newly created."""
    if BINDINGS_FILE.exists():
        return False
    BINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(BINDINGS_EXAMPLE, BINDINGS_FILE)
    return True


def bindings_have_devices() -> bool:
    """True if bindings.yaml parses and devices.listen is non-empty."""
    try:
        data = yaml.safe_load(BINDINGS_FILE.read_text())
    except Exception:
        return False
    listen = ((data or {}).get("devices") or {}).get("listen") or []
    return bool(listen)


def warn_legacy_files() -> None:
    """Note the retired Phase-0 files as safely deletable if present."""
    for path, role in (
        (LEGACY_ENV_FILE,        "old env file"),
        (LEGACY_PERSONAL_CONFIG, "old personal config"),
    ):
        if path.exists():
            print(f"ℹ️  {path} ({role}) is no longer used. You can delete it.")


def compute_state_hash() -> str:
    """Hash of repo path + user + unit template contents."""
    h = hashlib.sha256()
    h.update(str(REPO_DIR).encode())
    h.update(os.environ.get("USER", "unknown").encode())
    for name in ("xremap-kritomatic.service", "xremap-kritomatic.path"):
        f = REPO_DIR / name
        if f.exists():
            h.update(f.read_bytes())
    return h.hexdigest()[:32]


def read_installed_hash() -> str:
    if not SERVICE_FILE.exists():
        return ""
    for line in SERVICE_FILE.read_text().splitlines():
        if line.startswith("# STATE_HASH:"):
            return line.split(":", 1)[1].strip()
    return ""


def inject_repo_dir(template_path: Path) -> str:
    return template_path.read_text().replace("REPODIR_PLACEHOLDER", str(REPO_DIR))


def refresh_services() -> None:
    for unit in ("xremap-kritomatic.service", "xremap-kritomatic.path"):
        subprocess.run(
            ["systemctl", "--user", "try-restart", "--no-block", unit],
            check=False,
        )


def is_running() -> bool:
    r = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "xremap-kritomatic.service"],
        check=False,
    )
    return r.returncode == 0


def validate_bindings() -> int:
    """Run `kritomatic-xremap.py validate` and stream its output.

    Returns the launcher's exit code (0 = OK, non-zero = problems).
    If the launcher itself cannot be launched (missing interpreter,
    timeout), prints a warning and returns 0 so the caller proceeds;
    the ultimate is-running check will catch a real failure.
    """
    if not LAUNCHER.is_file():
        print(f"⚠️  Launcher not found at {LAUNCHER}; skipping validation.")
        return 0
    try:
        r = subprocess.run(
            [sys.executable, str(LAUNCHER), "validate"],
            check=False, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"⚠️  Could not run validation ({e}); continuing without it.")
        return 0
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    return r.returncode


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install / refresh the kritomatic xremap systemd service.",
    )
    ap.add_argument("--list-devices", action="store_true",
                    help="Print available input devices and exit")
    args = ap.parse_args()

    if args.list_devices:
        devs = list_devices()
        if not devs:
            print("(no devices reported by xremap --list-devices)", file=sys.stderr)
            return 1
        for d in devs:
            print(d)
        return 0

    print("🛠️  Kritomatic Xremap: Setup")

    # 1. Seed bindings.yaml if missing.
    if seed_bindings():
        print(f"📄 Created {BINDINGS_FILE}")
        print( "   Edit it to add your devices and bindings, then re-run this script.")
        return 0

    # 2. Refuse to proceed without at least one listened device.
    if not bindings_have_devices():
        print(f"⚠️  {BINDINGS_FILE} has no devices.listen entries.")
        print( "   Edit it and re-run this script.")
        return 1

    # 3. Notice retired files so the user can clean them up.
    warn_legacy_files()

    # 4. Ensure the launcher is executable (convenience: user may run it
    #    directly; the service always invokes it via `python <path>`, so
    #    this is not strictly required for the service to work).
    if LAUNCHER.is_file() and not os.access(LAUNCHER, os.X_OK):
        os.chmod(LAUNCHER, 0o755)
        print(f"🔧 chmod +x {LAUNCHER.name}")

    # 5. Validate bindings. Delegated to the launcher so the semantics
    #    live in exactly one place.
    rc = validate_bindings()
    if rc != 0:
        print("⚠️  Validation failed; not touching the service.")
        return rc

    # 6. Systemd units — hash-based reinstall.
    current_hash = compute_state_hash()
    installed_hash = read_installed_hash()
    if current_hash != installed_hash:
        print("🔗 Injecting paths and (re)installing systemd units...")
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
        SERVICE_FILE.write_text(
            inject_repo_dir(REPO_DIR / "xremap-kritomatic.service")
            + f"\n# STATE_HASH: {current_hash}\n"
        )
        PATH_UNIT_FILE.write_text(inject_repo_dir(REPO_DIR / "xremap-kritomatic.path"))
        shutil.copy(
            REPO_DIR / "xremap-kritomatic-restart.service",
            RESTART_UNIT_FILE,
        )
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(
            ["systemctl", "--user", "enable",
             "xremap-kritomatic.service", "xremap-kritomatic.path"],
            check=False,
        )
        subprocess.run(
            ["systemctl", "--user", "disable", "xremap-kritomatic-restart.service"],
            check=False, stderr=subprocess.DEVNULL,
        )
    else:
        print("⚡ Paths and unit files unchanged. Skipping re-injection.")

    # 7. Refresh the running service.
    print("🔄 Refreshing merger...")
    refresh_services()

    # 8. Verify — poll, don't sleep-and-peek. The installed .service has
    #    ExecStartPre=sleep 5 and the launcher itself may wait for devices,
    #    so "active" can take several seconds after try-restart --no-block
    #    returns. Poll up to 15s.
    print("⏳ Verifying service state...")
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if is_running():
            print("✅ Setup complete. Service is running.")
            return 0
        time.sleep(0.5)
    print("⚠️  Service not active after 15s. Check:")
    print("    systemctl --user status xremap-kritomatic.service")
    return 1


if __name__ == "__main__":
    sys.exit(main())
