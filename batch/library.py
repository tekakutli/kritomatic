"""Batch library for storing and managing named batches"""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional


class BatchLibrary:
    def __init__(self, library_dir: Optional[Path] = None):
        if library_dir is None:
            project_root = Path(__file__).parent.parent
            library_dir = project_root / 'batches'
        self.library_dir = library_dir
        self.library_dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, batch_data: Dict[str, Any]) -> bool:
        file_path = self.library_dir / f"{name}.json"
        try:
            with open(file_path, 'w') as f:
                json.dump(batch_data, f, indent=2)
            return True
        except Exception as e:
            print(f"❌ Error saving batch '{name}': {e}")
            return False

    def load(self, name: str) -> Optional[Dict[str, Any]]:
        file_path = self.library_dir / f"{name}.json"
        if not file_path.exists():
            return None
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading batch '{name}': {e}")
            return None

    def delete(self, name: str) -> bool:
        file_path = self.library_dir / f"{name}.json"
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def list_batches(self) -> List[str]:
        batches = []
        for file_path in self.library_dir.glob('*.json'):
            batches.append(file_path.stem)
        return sorted(batches)

    def get_info(self, name: str) -> Optional[Dict[str, Any]]:
        batch = self.load(name)
        if batch:
            return {
                'name': name,
                'id': batch.get('id', name),
                'command_count': len(batch.get('commands', [])),
                'path': str(self.library_dir / f"{name}.json")
            }
        return None

    def exists(self, name: str) -> bool:
        return (self.library_dir / f"{name}.json").exists()
