"""Filesystem memory backend — the default.

Uses fcntl.flock() advisory locking to prevent data corruption when
multiple processes (e.g. main agent + scheduled tasks) share the same
memory directory.  Writes acquire LOCK_EX, reads acquire LOCK_SH.
"""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from mithai.memory.base import MemoryBackend, SearchMatch, SearchResult


@contextmanager
def _flock(filepath: Path, exclusive: bool):
    """Acquire an advisory flock on *filepath*, then yield the open file handle.

    ``exclusive=True``  → LOCK_EX  (for writes)
    ``exclusive=False`` → LOCK_SH  (for reads)

    The lock is always released in the finally block, even on error.
    """
    # "a+" creates the file atomically (O_CREAT) and never truncates,
    # avoiding the TOCTOU race of checking exists() then opening.
    fd = open(filepath, "a+", encoding="utf-8")  # noqa: SIM115
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield fd
    finally:
        try:
            fd.flush()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()


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
        if target != self._base and not str(target).startswith(str(self._base) + os.sep):
            return None
        return target

    def read(self, path: str) -> str | None:
        target = self._resolve(path)
        if target is None or not target.exists():
            return None
        with _flock(target, exclusive=False) as fh:
            fh.seek(0)
            return fh.read()

    def write(self, path: str, content: str, *, append: bool = False) -> None:
        target = self._resolve(path)
        if target is None:
            raise ValueError(f"Invalid path: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with _flock(target, exclusive=True) as fh:
            if append:
                fh.seek(0, os.SEEK_END)
                fh.write(content)
            else:
                fh.seek(0)
                fh.truncate()
                fh.write(content)

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
                with _flock(file_path, exclusive=False) as fh:
                    fh.seek(0)
                    content = fh.read()
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
