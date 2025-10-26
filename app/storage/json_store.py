from pathlib import Path
import json
from typing import Any, Dict, List


class JsonStore:
    """Simple JSON-on-disk collections: one file per collection."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

    def _path(self, collection: str) -> Path:
        return self.data_dir / f"{collection}.json"

    def _load(self, collection: str) -> Dict[str, Any]:
        p = self._path(collection)
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, collection: str, obj: Dict[str, Any]):
        p = self._path(collection)
        with p.open("w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)

    def list(self, collection: str) -> List[Dict[str, Any]]:
        return list(self._load(collection).values())

    def get(self, collection: str, key: str):
        return self._load(collection).get(key)

    def upsert(self, collection: str, key: str, value: Dict[str, Any]):
        data = self._load(collection)
        data[key] = value
        self._save(collection, data)

    def delete(self, collection: str, key: str):
        data = self._load(collection)
        data.pop(key, None)
        self._save(collection, data)

    def replace_collection(self, collection: str, mapping: Dict[str, Any]):
        """Overwrite entire collection with provided mapping (id -> obj)."""
        if not isinstance(mapping, dict):
            raise TypeError("mapping must be a dict")
        self._save(collection, mapping)
