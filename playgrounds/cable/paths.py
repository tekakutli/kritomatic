"""Filesystem locations for the installed extension and the Zen profile."""
import os
import sys
from importlib import resources
from pathlib import Path
from typing import ContextManager


def assets_dir() -> Path:
    """Materialised path to the bundled extension source files.

    Uses importlib.resources so the package works whether it's
    installed from a wheel, in editable mode, or from a zipapp."""
    return Path(str(resources.files("deepdig.extension.assets")))


def zen_root() -> Path:
    """Best-effort guess at the Zen profile root."""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/zen"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", "")) / "zen"
    return Path.home() / ".zen"


def marker_path(profile_dir: Path) -> Path:
    return profile_dir / "deepdig_extension.json"


def policies_path(profile_dir: Path) -> Path:
    return profile_dir / "distribution" / "policies.json"


def xpi_path(profile_dir: Path, ext_id: str) -> Path:
    return profile_dir / "extensions" / f"{ext_id}.xpi"
