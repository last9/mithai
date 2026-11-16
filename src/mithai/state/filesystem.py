"""JSON-on-disk state backend."""

import fcntl
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from mithai.state.base import StateBackend


class FilesystemStateBackend(StateBackend):
    """
    Stores state as JSON files on disk.

    Structure: {base_path}/{namespace}/{key}.json

    Writes hold an advisory flock on a sibling ``.{key}.lock`` file and
    replace the JSON atomically, so concurrent writers (threads or processes)
    serialize and unlocked readers never observe a partially written file.
    """

    def __init__(self, base_path: str | Path):
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        ns_dir = self._base / namespace
        return ns_dir / f"{key}.json"

    @contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_name(f".{path.stem}.lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _replace(path: Path, value: Any) -> None:
        """Write *value* to a temp file, then atomically swap it into place."""
        tmp = path.with_name(f".{path.name}.tmp")
        with open(tmp, "w") as fh:
            fh.write(json.dumps(value, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def set(self, namespace: str, key: str, value: Any) -> None:
        path = self._path(namespace, key)
        with self._locked(path):
            self._replace(path, value)

    def update(self, namespace: str, key: str, fn: Callable[[Any | None], Any]) -> Any:
        path = self._path(namespace, key)
        with self._locked(path):
            value = fn(self.get(namespace, key))
            self._replace(path, value)
        return value

    def delete(self, namespace: str, key: str) -> None:
        path = self._path(namespace, key)
        if path.exists():
            path.unlink()

    def list_keys(self, namespace: str) -> list[str]:
        ns_dir = self._base / namespace
        if not ns_dir.exists():
            return []
        return [p.stem for p in ns_dir.glob("*.json")]
