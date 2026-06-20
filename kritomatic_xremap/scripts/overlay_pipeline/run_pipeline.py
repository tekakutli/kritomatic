#!/usr/bin/env python3
"""
Script to run the flag generation pipeline:
1. Activates virtual environment
2. Creates output directory if it doesn't exist
3. Runs flag_downloader.py
4. Runs bulk_text_renderer.py
5. Runs overlay.py
6. Runs output_grid_pdf.py
"""
# EQUIVALENT IN BASH TO:
# run_overlay_pipeline() {
#     # Activate virtual environment, change directory, and run scripts
#     source /home/tekakutli/code/kritomatic-auxiliary/bin/activate && \
#     cd /home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/overlay_pipeline && \
#     mkdir -p /tmp/output/ && \
#     python flag_downloader.py && \
#     python bulk_text_renderer.py && \
#     python overlay.py && \
#     python output_grid_pdf.py

#     # Capture exit code
#     local EXIT_CODE=$?

#     # Deactivate virtual environment
#     deactivate 2>/dev/null

#     # Return the exit code
#     return $EXIT_CODE
# }

import os
import sys
import subprocess
from pathlib import Path

# Configuration
VENV_PATH = Path("/home/tekakutli/code/kritomatic-auxiliary")
SCRIPT_DIR = Path("/home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/overlay_pipeline")

def activate_venv():
    """Activate the virtual environment by modifying PATH and sys.path."""
    venv_bin = VENV_PATH / "bin"

    if not VENV_PATH.exists():
        print(f"✗ Virtual environment not found at: {VENV_PATH}")
        return False

    if not venv_bin.exists():
        print(f"✗ Virtual environment bin directory not found: {venv_bin}")
        return False

    # Get the current PATH
    current_path = os.environ.get('PATH', '')

    # Prepend venv bin to PATH
    os.environ['PATH'] = f"{venv_bin}:{current_path}"

    # Add venv's site-packages to sys.path
    venv_site_packages = VENV_PATH / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if venv_site_packages.exists():
        sys.path.insert(0, str(venv_site_packages))

    # Set VIRTUAL_ENV environment variable
    os.environ['VIRTUAL_ENV'] = str(VENV_PATH)

    # Also try the activate_this.py method for better compatibility
    activate_script = venv_bin / "activate_this.py"
    if activate_script.exists():
        try:
            with open(activate_script) as f:
                exec(f.read(), {'__file__': str(activate_script)})
        except Exception as e:
            print(f"⚠ Warning: Could not run activate_this.py: {e}")

    print(f"✓ Virtual environment activated: {VENV_PATH}")
    return True

def change_to_script_dir():
    """Change working directory to the script directory."""
    if not SCRIPT_DIR.exists():
        print(f"✗ Script directory not found: {SCRIPT_DIR}")
        return False

    try:
        os.chdir(SCRIPT_DIR)
        print(f"✓ Changed to script directory: {SCRIPT_DIR}")
        return True
    except Exception as e:
        print(f"✗ Failed to change directory: {e}")
        return False

def create_output_directory():
    """Create /tmp/output/ directory if it doesn't exist."""
    output_dir = Path("/tmp/output/")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"✓ Output directory ready: {output_dir}")
        return True
    except PermissionError:
        print(f"✗ Permission denied: Cannot create {output_dir}")
        return False
    except Exception as e:
        print(f"✗ Error creating directory: {e}")
        return False

def run_script(script_name):
    """Run a Python script in the current directory using the venv Python."""
    script_path = Path(script_name)

    # Check if script exists
    if not script_path.exists():
        print(f"✗ Script not found: {script_name}")
        return False

    # Check if it's a file
    if not script_path.is_file():
        print(f"✗ Not a file: {script_name}")
        return False

    try:
        print(f"▶ Running {script_name}...")

        # Use python from the virtual environment
        venv_python = VENV_PATH / "bin" / "python"
        if not venv_python.exists():
            print(f"⚠ Warning: venv python not found, using system python")
            python_cmd = sys.executable
        else:
            python_cmd = str(venv_python)

        # Run the script
        result = subprocess.run(
            [python_cmd, script_name],
            capture_output=True,
            text=True,
            check=False
        )

        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

        if result.returncode == 0:
            print(f"✓ {script_name} completed successfully")
            return True
        else:
            print(f"✗ {script_name} failed with exit code {result.returncode}")
            return False

    except Exception as e:
        print(f"✗ Error running {script_name}: {e}")
        return False

def main():
    """Main execution function."""
    print("=" * 60)
    print("Starting overlay pipeline")
    print("=" * 60)
    print(f"Python version: {sys.version}")
    print(f"Script directory: {SCRIPT_DIR}")
    print(f"Virtual environment: {VENV_PATH}")
    print("=" * 60)

    # Step 1: Activate virtual environment
    print("\n[Step 1] Activating virtual environment...")
    if not activate_venv():
        print("✗ Pipeline aborted: Failed to activate virtual environment")
        sys.exit(1)

    # Step 2: Change to script directory
    print("\n[Step 2] Changing to script directory...")
    if not change_to_script_dir():
        print("✗ Pipeline aborted: Failed to change directory")
        sys.exit(1)

    # Step 3: Create output directory
    print("\n[Step 3] Creating output directory...")
    if not create_output_directory():
        print("✗ Pipeline aborted: Failed to create output directory")
        sys.exit(1)

    # List of scripts to run in order
    scripts = [
        "flag_downloader.py",
        "bulk_text_renderer.py",
        "overlay.py",
        "output_grid_pdf.py"
    ]

    # Step 4: Run each script
    print("\n[Step 4] Running pipeline scripts...")
    print("-" * 60)
    for i, script in enumerate(scripts, 1):
        print(f"\n[{i}/{len(scripts)}] Running {script}...")
        if not run_script(script):
            print(f"✗ Pipeline aborted at {script}")
            sys.exit(1)

    # All done
    print("\n" + "=" * 60)
    print("✓ Pipeline completed successfully!")
    print("=" * 60)
    print(f"Output directory: /tmp/output/")
    print(f"Virtual environment: {VENV_PATH}")
    print("=" * 60)
    sys.exit(0)

if __name__ == "__main__":
    main()
