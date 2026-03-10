"""Filesystem memory backend — the default."""

import os
from pathlib import Path

from mithai.memory.base import MemoryBackend, SearchMatch, SearchResult


class FilesystemMemoryBackend(MemoryBackend):
    """
    Stores memory as files on disk.

    Structure: {base_path}/{virtual_path}
    """

    def __init__(self, base_path: str | Path):
        self._base = Path(base_path).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path | None:
        """Resolve virtual path to absolute, with traversal guard."""
        if not self.validate_path(path):
            return None
        target = (self._base / path).resolve()
        if not str(target).startswith(str(self._base)):
            return None
        return target

    def read(self, path: str) -> str | None:
        target = self._resolve(path)
        if target is None or not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def write(self, path: str, content: str, *, append: bool = False) -> None:
        target = self._resolve(path)
        if target is None:
            raise ValueError(f"Invalid path: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with open(target, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            target.write_text(content, encoding="utf-8")

    def exists(self, path: str) -> bool:
        target = self._resolve(path)
        return target is not None and target.exists()

    def search(
        self, query: str, *, glob: str = "**/*.md", max_matches_per_file: int = 5
    ) -> list[SearchResult]:
        query_lower = query.lower()
        results = []
        if not self._base.exists():
            return results

        for file_path in self._base.glob(glob):
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue
            if query_lower not in content.lower():
                continue
            matches = []
            for i, line in enumerate(content.splitlines(), 1):
                if query_lower in line.lower():
                    matches.append(SearchMatch(line=i, text=line.strip()[:200]))
                    if len(matches) >= max_matches_per_file:
                        break
            rel = str(file_path.relative_to(self._base)).replace(os.sep, "/")
            results.append(SearchResult(path=rel, matches=matches))

        return results

    def list_files(self, prefix: str = "", *, glob: str = "*") -> list[str]:
        search_base = self._base / prefix if prefix else self._base
        if not search_base.exists():
            return []
        return sorted(
            str(p.relative_to(self._base)).replace(os.sep, "/")
            for p in search_base.glob(glob)
            if p.is_file()
        )
