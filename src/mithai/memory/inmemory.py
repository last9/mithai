"""In-memory memory backend for testing."""

import fnmatch
import posixpath
import re

from mithai.memory.base import MemoryBackend, SearchMatch, SearchResult


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a glob pattern to a regex that handles ** correctly.

    ** matches zero or more path segments (including none).
    * matches any characters except /.
    """
    parts = []
    i = 0
    while i < len(pattern):
        if pattern[i:i + 2] == "**":
            parts.append(".*")
            i += 2
            if i < len(pattern) and pattern[i] == "/":
                i += 1  # skip the trailing / after **
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        elif pattern[i] == ".":
            parts.append(r"\.")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(parts) + "$")


class InMemoryMemoryBackend(MemoryBackend):
    """Stores memory in a dict. Data is lost when the process exits."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def _matches(self, path: str, glob: str) -> bool:
        """Check if path matches glob pattern with ** support."""
        return bool(_glob_to_regex(glob).match(path))

    def read(self, path: str) -> str | None:
        return self._store.get(path)

    def write(self, path: str, content: str, *, append: bool = False) -> None:
        if not self.validate_path(path):
            raise ValueError(f"Invalid path: {path}")
        if append and path in self._store:
            self._store[path] = self._store[path] + content
        else:
            self._store[path] = content

    def exists(self, path: str) -> bool:
        return path in self._store

    def search(
        self, query: str, *, glob: str = "**/*.md", max_matches_per_file: int = 5
    ) -> list[SearchResult]:
        query_lower = query.lower()
        results = []
        for path, content in sorted(self._store.items()):
            if not self._matches(path, glob):
                continue
            if query_lower not in content.lower():
                continue
            matches = []
            for i, line in enumerate(content.splitlines(), 1):
                if query_lower in line.lower():
                    matches.append(SearchMatch(line=i, text=line.strip()[:200]))
                    if len(matches) >= max_matches_per_file:
                        break
            results.append(SearchResult(path=path, matches=matches))
        return results

    def list_files(self, prefix: str = "", *, glob: str = "*") -> list[str]:
        full_glob = posixpath.join(prefix, glob) if prefix else glob
        return sorted(p for p in self._store if self._matches(p, full_glob))
