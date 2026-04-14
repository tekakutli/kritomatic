import os
import yaml
import subprocess
from pathlib import Path
import tempfile
import sys

def merge_configs():
    # 1. Load Environment Variables
    env_path = Path.home() / ".config/xremap/xremap_kritomatic.env"
    env_vars = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip().strip('"').strip("'")

    repo_dir = env_vars.get("REPODIR", "")
    target_device = env_vars.get("TARGET_DEVICE", "")
    personal_config = env_vars.get("PERSONAL_CONFIG", "")

    if not repo_dir:
        print("Error: REPODIR not found in .env file.")
        sys.exit(1)

    merged_data = {"keymap": []}

    # 2. Files to Merge
    main_config_path = Path(repo_dir) / "kritomatic_xremap.yaml"
    files_to_merge = [main_config_path]
    if personal_config and Path(personal_config).exists():
        files_to_merge.append(Path(personal_config))

    # 3. Process and Expand Keywords
    for file_path in files_to_merge:
        try:
            with open(file_path, 'r') as f:
                content = f.read()

                # Path Calculations: Step back one level to find 'src'
                root_dir = Path(repo_dir).parent
                python_src_path = str(root_dir / "src")
                main_py_path = str(root_dir / "src" / "kritomatic" / "main.py")
                clipboard_path = os.path.join(repo_dir, "scripts", "krita_clipboard.py")

                # EXPANSION LOGIC
                # Use a marker to avoid double-wrapping "python" inside "kritomatic"
                km_expansion = f'"/usr/bin/env", "PYTHONPATH={python_src_path}", "PYTHON_BIN_MARKER", "{main_py_path}"'
                content = content.replace('"kritomatic"', km_expansion)

                # Finalize bin and paths
                content = content.replace("PYTHON_BIN_MARKER", "python")
                content = content.replace("__CLIPBOARD_SCRIPT__", clipboard_path)

                data = yaml.safe_load(content)
                if not data or "keymap" not in data:
                    continue

                for km in data["keymap"]:
                    # --- DEVICE TARGETING INJECTION ---
                    clean_device = str(target_device).strip()
                    if clean_device and clean_device.lower() != "null":
                        km["device"] = {"only": clean_device}

                    merged_data["keymap"].append(km)

        except Exception as e:
            print(f"Warning: Failed to process {file_path}: {e}")

    # 4. Launch xremap with Wayland Environment
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as tmp:
        yaml.dump(merged_data, tmp)
        tmp_path = tmp.name

    try:
        print(f"Launching xremap with config: {tmp_path}")

        # Build custom environment for Wayland/Session communication
        custom_env = os.environ.copy()
        custom_env["PYTHONPATH"] = str(Path(repo_dir).parent / "src")

        # Ensure Wayland variables are present
        uid = os.getuid()
        if "XDG_RUNTIME_DIR" not in custom_env:
            custom_env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

        # Standardize Wayland/X11 auth for Krita window detection
        home = str(Path.home())
        if "XAUTHORITY" not in custom_env:
            custom_env["XAUTHORITY"] = os.path.join(home, ".Xauthority")
        if "DISPLAY" not in custom_env:
            custom_env["DISPLAY"] = os.environ.get("DISPLAY", ":0")

        # Spawn xremap
        process = subprocess.Popen(['xremap', tmp_path], env=custom_env)
        process.wait()

    except KeyboardInterrupt:
        process.terminate()
    except Exception as e:
        print(f"Xremap error: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

if __name__ == "__main__":
    merge_configs()
