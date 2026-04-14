#!/usr/bin/env python3
import os
import yaml
import subprocess
from pathlib import Path
import tempfile
import sys
import time

# Try to import pyinotify - gracefully fall back if missing
try:
    import pyinotify
    HAS_INOTIFY = True
except ImportError:
    HAS_INOTIFY = False


def load_env():
    """Load environment variables from the standard location."""
    env_path = Path.home() / ".config/xremap/xremap_kritomatic.env"
    env_vars = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip().strip('"').strip("'")
    return env_vars


def wait_for_device_polling(device_name, timeout=5, interval=0.1):
    """Polling method - works without pyinotify."""
    device_lower = device_name.lower()

    # Quick check first
    try:
        with open("/proc/bus/input/devices", "r") as f:
            if device_lower in f.read().lower():
                return True
    except:
        pass

    start = time.time()
    while (time.time() - start) < timeout:
        try:
            with open("/proc/bus/input/devices", "r") as f:
                if device_lower in f.read().lower():
                    return True
        except (OSError, IOError):
            pass
        time.sleep(interval)
    return False


def wait_for_device_inotify(device_name, timeout=5):
    """Wait for device using inotify (instant response)."""
    device_lower = device_name.lower()

    # Quick check first
    try:
        with open("/proc/bus/input/devices", "r") as f:
            if device_lower in f.read().lower():
                return True
    except:
        pass

    wm = pyinotify.WatchManager()

    class Watcher(pyinotify.ProcessEvent):
        def __init__(self):
            super().__init__()
            self.found = False

        def process_IN_MODIFY(self, event):
            if self.found:
                return
            try:
                with open(event.pathname, 'r') as f:
                    if device_lower in f.read().lower():
                        self.found = True
            except:
                pass

    watcher = Watcher()
    notifier = pyinotify.Notifier(wm, watcher)
    wm.add_watch('/proc/bus/input/devices', pyinotify.IN_MODIFY)

    try:
        start = time.time()
        while (time.time() - start) < timeout and not watcher.found:
            notifier.process_events()
            if notifier.check_events():
                notifier.read_events()
            time.sleep(0.01)
        return watcher.found
    finally:
        notifier.stop()


def wait_for_device(device_name, timeout=5):
    """Wait for device using best available method."""
    if not device_name or device_name.lower() == "null":
        return True

    print(f"⏳ Waiting for device: {device_name}")

    if HAS_INOTIFY:
        found = wait_for_device_inotify(device_name, timeout)
        method = "inotify"
    else:
        found = wait_for_device_polling(device_name, timeout)
        method = "polling"

    if found:
        print(f"✨ Found {device_name} via {method}")
    else:
        print(f"⚠️  Device '{device_name}' not found after {timeout}s (continuing)")

    return found


def expand_keywords(content, repo_dir):
    """Perform keyword expansion on the YAML content."""
    root_dir = Path(repo_dir).parent
    python_src_path = str(root_dir / "src")
    main_py_path = str(root_dir / "src" / "kritomatic" / "main.py")
    clipboard_path = os.path.join(repo_dir, "scripts", "krita_clipboard.py")

    km_expansion = f'"/usr/bin/env", "PYTHONPATH={python_src_path}", "python", "{main_py_path}"'
    content = content.replace('"kritomatic"', km_expansion)
    content = content.replace("__CLIPBOARD_SCRIPT__", clipboard_path)

    return content


def merge_configs():
    # 1. Load environment
    env_vars = load_env()
    repo_dir = env_vars.get("REPODIR", "")
    target_device = env_vars.get("TARGET_DEVICE", "")
    personal_config = env_vars.get("PERSONAL_CONFIG", "")

    if not repo_dir:
        print("Error: REPODIR not found in .env file.")
        sys.exit(1)

    # 2. Collect files to merge
    main_config = Path(repo_dir) / "kritomatic_xremap.yaml"
    files_to_merge = [main_config]
    if personal_config and Path(personal_config).exists():
        files_to_merge.append(Path(personal_config))

    # 3. Process each file
    merged_data = {"keymap": []}

    for file_path in files_to_merge:
        try:
            with open(file_path, 'r') as f:
                content = f.read()
                expanded = expand_keywords(content, repo_dir)
                data = yaml.safe_load(expanded)

            if not data or "keymap" not in data:
                continue

            for km in data["keymap"]:
                if target_device and target_device.lower() != "null":
                    km["device"] = {"only": target_device}
                merged_data["keymap"].append(km)

        except Exception as e:
            print(f"Warning: Failed to process {file_path}: {e}")

    # 4. Wait for device (optional, non-fatal)
    wait_for_device(target_device, timeout=5)

    # 5. Write temp config and launch xremap
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as tmp:
        yaml.dump(merged_data, tmp, sort_keys=False)
        tmp_path = tmp.name

    try:
        custom_env = os.environ.copy()
        custom_env["PYTHONPATH"] = str(Path(repo_dir).parent / "src")

        uid = os.getuid()
        custom_env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        custom_env.setdefault("XAUTHORITY", str(Path.home() / ".Xauthority"))
        custom_env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":0"))

        cmd = ['xremap', tmp_path]
        if target_device and target_device.lower() != "null":
            cmd.extend(['--device', target_device])

        print(f"🚀 Launching xremap (device: {target_device or 'auto'})")
        process = subprocess.Popen(cmd, env=custom_env)
        process.wait()

    except KeyboardInterrupt:
        process.terminate()
    except Exception as e:
        print(f"Xremap error: {e}")
    finally:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()


if __name__ == "__main__":
    if HAS_INOTIFY:
        print("📡 Using inotify for instant device detection")
    else:
        print("📡 Using polling mode (install pyinotify for instant detection)")
    merge_configs()
