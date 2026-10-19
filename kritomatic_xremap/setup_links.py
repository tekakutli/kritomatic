#!/usr/bin/env python3
"""Setup/refresh script for the kritomatic xremap systemd service.

Safe to run any number of times:
  - REPODIR is updated to the current repo location.
  - Any missing env keys are appended with safe defaults (empty).
  - Existing user-set values are never modified, except with --reconfigure.
  - systemd units are re-installed only if their contents (or repo path) changed.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
REPO_DIR = Path(__file__).resolve().parent
SYSTEMD_USER_DIR = HOME / ".config/systemd/user"
ENV_DIR = HOME / ".config/xremap"
ENV_FILE = ENV_DIR / "xremap_kritomatic.env"
SERVICE_FILE = SYSTEMD_USER_DIR / "xremap-kritomatic.service"
PATH_UNIT_FILE = SYSTEMD_USER_DIR / "xremap-kritomatic.path"
RESTART_UNIT_FILE = SYSTEMD_USER_DIR / "xremap-kritomatic-restart.service"

PERSONAL_CONFIG_DEFAULT = str(HOME / "xremap_config.yaml")

TEMPLATE = f"""# ==============================================================================
# KRITOMATIC ENVIRONMENT CONFIGURATION
# ==============================================================================
# Re-run ./setup_links.py --reconfigure to change devices interactively.

# 1. REPOSITORY PATH
REPODIR="{REPO_DIR}"

# 2. PERSONAL CONFIGURATION
PERSONAL_CONFIG="{PERSONAL_CONFIG_DEFAULT}"

# 3. TARGET DEVICE (default keyboard for kritomatic shortcuts)
# xremap does EXACT string matching. Run `./setup_links.py --list-devices`
# to see valid names. Suggested: TARGET_DEVICE="kanata ministeren"
TARGET_DEVICE=""

# 4. ADDITIONAL DEVICES (extra keyboards xremap should ALSO listen to)
# Comma-separated. Leave empty to listen only on TARGET_DEVICE.
# Suggested: PERSONAL_DEVICES="kanata standard"
PERSONAL_DEVICES=""
"""

# (key, description) pairs. Missing keys are appended automatically.
REQUIRED_KEYS = [
    ("REPODIR",          "Repository path"),
    ("PERSONAL_CONFIG",  "Path to personal xremap config"),
    ("TARGET_DEVICE",    "Default keyboard for kritomatic shortcuts"),
    ("PERSONAL_DEVICES", "Additional keyboards xremap should listen to"),
]


# ----------------------------------------------------------------- helpers

def env_get(text: str, key: str) -> str:
    m = re.search(rf'^{re.escape(key)}="(.*)"', text, re.MULTILINE)
    return m.group(1) if m else ""


def env_set_line(text: str, key: str, value: str) -> tuple[str, bool]:
    """Set KEY="value" in text. Returns (new_text, changed)."""
    pattern = re.compile(rf'^{re.escape(key)}=.*$', re.MULTILINE)
    new_line = f'{key}="{value}"'
    if pattern.search(text):
        new_text = pattern.sub(new_line, text, count=1)
        return new_text, new_text != text
    sep = "" if text.endswith("\n") or not text else "\n"
    return text + sep + new_line + "\n", True


def ensure_keys(text: str) -> tuple[str, list[str]]:
    """Append any missing REQUIRED_KEYS as KEY=\"\" with a comment."""
    added = []
    for key, desc in REQUIRED_KEYS:
        if not re.search(rf'^{re.escape(key)}=', text, re.MULTILINE):
            sep = "" if text.endswith("\n") or not text else "\n"
            text += f'{sep}\n# {desc}\n{key}=""\n'
            added.append(key)
    return text, added


def list_devices() -> list[str]:
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


def prompt_devices(text: str) -> tuple[str, str]:
    devices = list_devices()
    print()
    if devices:
        print("Available input devices:")
        for d in devices:
            print(f"  {d}")
    else:
        print("(xremap --list-devices unavailable)")

    cur_target = env_get(text, "TARGET_DEVICE")
    cur_personal = env_get(text, "PERSONAL_DEVICES")

    print()
    print('Enter to keep current, "-" to clear, or type a new value.')
    print('Leave PERSONAL_DEVICES empty to listen on only one device.')
    print()

    t = input(f'TARGET_DEVICE [{cur_target or "(empty)"}]: ').strip()
    if t == "-":
        cur_target = ""
    elif t:
        cur_target = t

    p = input(f'PERSONAL_DEVICES [{cur_personal or "(empty)"}]: ').strip()
    if p == "-":
        cur_personal = ""
    elif p:
        cur_personal = p

    return cur_target, cur_personal


def warn_unreachable_bindings(personal_config: Path, listen: set[str]) -> None:
    """Warn if personal YAML has device: {only: X} where X isn't listened to."""
    if not personal_config.exists():
        return
    try:
        import yaml
    except ImportError:
        return
    try:
        data = yaml.safe_load(personal_config.read_text())
    except Exception:
        return
    if not data or "keymap" not in data:
        return

    for entry in data["keymap"]:
        dev = entry.get("device", {})
        if "only" not in dev:
            continue
        only = dev["only"] if isinstance(dev["only"], list) else [dev["only"]]
        missing = [d for d in only if d not in listen]
        if missing:
            name = entry.get("name", "<unnamed binding>")
            print(f'⚠️  Binding "{name}" requires device(s) {missing} '
                  f'that xremap will not listen to. It will never fire.')


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


# ----------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install / refresh the kritomatic xremap systemd service.",
    )
    ap.add_argument("--reconfigure", action="store_true",
                    help="Interactively re-prompt TARGET_DEVICE and PERSONAL_DEVICES")
    ap.add_argument("--list-devices", action="store_true",
                    help="Print available input devices and exit")
    ap.add_argument("--non-interactive", action="store_true",
                    help="Do not prompt even with --reconfigure")
    args = ap.parse_args()

    if args.list_devices:
        devs = list_devices()
        if not devs:
            print("(no devices reported by xremap --list-devices)", file=sys.stderr)
            return 1
        for d in devs:
            print(d)
        return 0

    print("🛠️  Kritomatic Xremap: Initializing Decoupled Setup...")

    # 1. env file: create or read
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    first_install = not ENV_FILE.exists()
    if first_install:
        print(f"📄 Creating new .env file at {ENV_FILE}")
        env_text = TEMPLATE
    else:
        env_text = ENV_FILE.read_text()

    # 2. ensure all required keys exist
    env_text, added = ensure_keys(env_text)
    if added:
        print(f"➕ Added missing keys: {', '.join(added)}")

    # 3. update REPODIR
    env_text, repodir_changed = env_set_line(env_text, "REPODIR", str(REPO_DIR))
    if repodir_changed:
        print(f"🔄 REPODIR updated to {REPO_DIR}")

    # 4. reconfigure prompt (only with --reconfigure)
    if args.reconfigure and not args.non_interactive:
        new_target, new_personal = prompt_devices(env_text)
        env_text, t_changed = env_set_line(env_text, "TARGET_DEVICE", new_target)
        env_text, p_changed = env_set_line(env_text, "PERSONAL_DEVICES", new_personal)
        if t_changed:
            print(f'✏️  TARGET_DEVICE = "{new_target}"')
        if p_changed:
            print(f'✏️  PERSONAL_DEVICES = "{new_personal}"')

    # 5. write env back if changed
    old_text = ENV_FILE.read_text() if ENV_FILE.exists() else ""
    if env_text != old_text:
        ENV_FILE.write_text(env_text)

    # 6. warn about device filters that can't fire
    listen = {env_get(env_text, "TARGET_DEVICE")}
    extra = env_get(env_text, "PERSONAL_DEVICES")
    if extra:
        listen.update(d.strip() for d in extra.split(",") if d.strip())
    listen.discard("")
    if not listen:
        print("⚠️  TARGET_DEVICE and PERSONAL_DEVICES are both empty. "
              "xremap will listen on ALL devices.")
    personal_cfg = Path(env_get(env_text, "PERSONAL_CONFIG") or PERSONAL_CONFIG_DEFAULT)
    warn_unreachable_bindings(personal_cfg, listen)

    # 7. systemd units (hash-based)
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

    # 8. refresh
    print("🔄 Refreshing merger...")
    refresh_services()

    # 9. verify
    time.sleep(0.3)
    if is_running() or (time.sleep(0.3) or is_running()):
        print("✅ Setup complete. Service is running.")
        return 0
    print("⚠️  Service not active. Check:")
    print("    systemctl --user status xremap-kritomatic.service")
    return 1


if __name__ == "__main__":
    sys.exit(main())
