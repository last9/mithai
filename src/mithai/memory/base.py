"""Abstract memory backend interface."""

import json
import posixpath
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchMatch:
    """A single matching line within a file."""

    line: int
    text: str


@dataclass
class SearchResult:
    """Search results for a single file."""

    path: str
    matches: list[SearchMatch]


class MemoryBackend(ABC):
    """
    Pluggable storage for the bot's persistent memory.

    All paths are virtual, relative to the memory root.
    Paths use forward slashes regardless of OS.
    Implementations handle path safety internally.
    """

    @abstractmethod
    def read(self, path: str) -> str | None:
        """Read text content at path. Returns None if not found."""
        ...

    @abstractmethod
    def write(self, path: str, content: str, *, append: bool = False) -> None:
        """Write content to path. If append=True, adds to end of existing content."""
        ...

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check whether a path exists."""
        ...

    @abstractmethod
    def search(
        self, query: str, *, glob: str = "**/*.md", max_matches_per_file: int = 5
    ) -> list[SearchResult]:
        """Search for a substring across files matching the glob pattern.

        Search is case-insensitive. Returns matching files with line-level context.
        """
        ...

    @abstractmethod
    def list_files(self, prefix: str = "", *, glob: str = "*") -> list[str]:
        """List virtual paths under prefix matching the glob pattern."""
        ...

    def read_json(self, path: str) -> dict | list | None:
        """Read and parse a JSON file. Returns None if not found."""
        content = self.read(path)
        if content is None:
            return None
        return json.loads(content)

    def write_json(self, path: str, data: dict | list) -> None:
        """Serialize data as JSON and write to path (always overwrites)."""
        self.write(path, json.dumps(data, indent=2), append=False)

    def validate_path(self, path: str) -> bool:
        """Check that path does not escape the memory root."""
        normalized = posixpath.normpath(path)
        return not normalized.startswith("..") and not normalized.startswith("/")

    def delete(self, path: str) -> bool:
        """Delete the file at path. Returns True if it existed and was removed,
        False if it did not exist. Backends that don't support deletion raise
        NotImplementedError (the default)."""
        raise NotImplementedError(f"{type(self).__name__} does not support delete")
