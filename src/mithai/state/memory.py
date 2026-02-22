"""In-memory state backend for testing."""

from typing import Any

from mithai.state.base import StateBackend


class MemoryStateBackend(StateBackend):
    """Stores state in a dict. Data is lost when the process exits."""

    def __init__(self):
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, namespace: str, key: str) -> Any | None:
        return self._store.get(namespace, {}).get(key)

    def set(self, namespace: str, key: str, value: Any) -> None:
        if namespace not in self._store:
            self._store[namespace] = {}
        self._store[namespace][key] = value

    def delete(self, namespace: str, key: str) -> None:
        if namespace in self._store:
            self._store[namespace].pop(key, None)

    def list_keys(self, namespace: str) -> list[str]:
        return list(self._store.get(namespace, {}).keys())
