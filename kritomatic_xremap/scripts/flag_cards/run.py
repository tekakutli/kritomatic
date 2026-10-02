#!/usr/bin/env python3
"""
Run the flag/text overlay pipeline:
1. stage_1_fetch_flags.py
2. stage_2_render_text.py
3. stage_3_compose.py
4. stage_4_pdf_grid.py

Each stage runs with the venv's python binary and the pipeline directory
as its working directory. Stops at the first failure.
"""

import sys
import subprocess
from pathlib import Path

from paths import PIPELINE_ROOT

# ===== CONFIGURATION =====
VENV_PATH = Path("/home/tekakutli/code/kritomatic-auxiliary")
SCRIPT_DIR = Path(__file__).resolve().parent

SCRIPTS = [
    "stage_1_fetch_flags.py",
    "stage_2_render_text.py",
    "stage_3_compose.py",
    "stage_4_pdf_grid.py",
]
# =========================


def resolve_venv_python() -> Path | None:
    python_bin = VENV_PATH / "bin" / "python"
    if not python_bin.is_file():
        print(f"✗ venv python not found: {python_bin}")
        return None
    return python_bin


def ensure_pipeline_root() -> bool:
    try:
        PIPELINE_ROOT.mkdir(parents=True, exist_ok=True)
        print(f"✓ Pipeline root ready: {PIPELINE_ROOT}")
        return True
    except OSError as e:
        print(f"✗ Could not create {PIPELINE_ROOT}: {e}")
        return False


def run_stage(script_name: str, venv_python: Path) -> bool:
    script_path = SCRIPT_DIR / script_name
    if not script_path.is_file():
        print(f"✗ Script not found: {script_path}")
        return False

    print(f"▶ {script_name}")
    result = subprocess.run(
        [str(venv_python), str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        cwd=SCRIPT_DIR,
    )

    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)

    if result.returncode == 0:
        print(f"✓ {script_name} completed")
        return True
    print(f"✗ {script_name} failed with exit code {result.returncode}")
    return False


def main() -> int:
    print("=" * 60)
    print("Starting flag/text overlay pipeline")
    print("=" * 60)
    print(f"Python:       {sys.executable}")
    print(f"Venv:         {VENV_PATH}")
    print(f"Script dir:   {SCRIPT_DIR}")
    print(f"Pipeline root:{PIPELINE_ROOT}")
    print("=" * 60)

    venv_python = resolve_venv_python()
    if venv_python is None:
        return 1

    if not ensure_pipeline_root():
        return 1

    for i, script in enumerate(SCRIPTS, 1):
        print(f"\n[{i}/{len(SCRIPTS)}] {script}")
        if not run_stage(script, venv_python):
            print(f"\n✗ Pipeline aborted at {script}")
            return 1

    print("\n" + "=" * 60)
    print("✓ Pipeline complete")
    print(f"Output PDF: {PIPELINE_ROOT / 'output.pdf'}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
