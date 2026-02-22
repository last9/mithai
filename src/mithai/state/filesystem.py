"""JSON-on-disk state backend."""

import json
from pathlib import Path
from typing import Any

from mithai.state.base import StateBackend


class FilesystemStateBackend(StateBackend):
    """
    Stores state as JSON files on disk.

    Structure: {base_path}/{namespace}/{key}.json
    """

    def __init__(self, base_path: str | Path):
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        ns_dir = self._base / namespace
        return ns_dir / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def set(self, namespace: str, key: str, value: Any) -> None:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n")

    def delete(self, namespace: str, key: str) -> None:
        path = self._path(namespace, key)
        if path.exists():
            path.unlink()

    def list_keys(self, namespace: str) -> list[str]:
        ns_dir = self._base / namespace
        if not ns_dir.exists():
            return []
        return [p.stem for p in ns_dir.glob("*.json")]
