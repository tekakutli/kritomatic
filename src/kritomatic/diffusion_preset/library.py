"""Library for storing and managing diffusion presets"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


class DiffusionPresetLibrary:
    """Manages diffusion presets stored in data/diffusion/"""

    def __init__(self, library_dir: Optional[Path] = None):
        if library_dir is None:
            # Get git root directory (where data/ folder lives)
            # __file__ is: .../kritomatic/src/kritomatic/diffusion_preset/library.py
            # We need to go up: src/kritomatic/diffusion_preset/ -> src/ -> kritomatic/ -> git root
            git_root = Path(__file__).parent.parent.parent.parent
            library_dir = git_root / 'data' / 'diffusion'
        self.library_dir = library_dir
        self.library_dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, preset_data: Dict[str, Any]) -> bool:
        """Save a preset with the given name"""
        file_path = self.library_dir / f"{name}.json"
        try:
            with open(file_path, 'w') as f:
                json.dump(preset_data, f, indent=2)
            return True
        except Exception as e:
            print(f"❌ Error saving preset '{name}': {e}")
            return False

    def load(self, name: str) -> Optional[Dict[str, Any]]:
        """Load a preset by name"""
        file_path = self.library_dir / f"{name}.json"
        if not file_path.exists():
            return None
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading preset '{name}': {e}")
            return None

    def delete(self, name: str) -> bool:
        """Delete a preset by name"""
        file_path = self.library_dir / f"{name}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def list_presets(self) -> List[str]:
        """List all saved preset names"""
        presets = []
        for file_path in sorted(self.library_dir.glob('*.json')):
            presets.append(file_path.stem)
        return presets

    def get_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get preset metadata without loading the entire parameters"""
        preset = self.load(name)
        if preset:
            return {
                'name': name,
                'workflow': preset.get('workflow', 'unknown'),
                'param_count': len(preset.get('parameters', {})),
                'created': preset.get('created', 'unknown'),
                'modified': preset.get('modified', 'unknown'),
                'path': str(self.library_dir / f"{name}.json")
            }
        return None

    def exists(self, name: str) -> bool:
        """Check if a preset exists"""
        return (self.library_dir / f"{name}.json").exists()
