"""Abstract state backend interface."""

from abc import ABC, abstractmethod
from typing import Any


class StateBackend(ABC):
    """
    Pluggable state storage for skills.

    Skills use state to persist data between conversations.
    Default implementation is filesystem (JSON files).
    """

    @abstractmethod
    def get(self, namespace: str, key: str) -> Any | None:
        """Get a value by namespace + key. Returns None if not found."""
        ...

    @abstractmethod
    def set(self, namespace: str, key: str, value: Any) -> None:
        """Set a value by namespace + key."""
        ...

    @abstractmethod
    def delete(self, namespace: str, key: str) -> None:
        """Delete a value."""
        ...

    @abstractmethod
    def list_keys(self, namespace: str) -> list[str]:
        """List all keys in a namespace."""
        ...
