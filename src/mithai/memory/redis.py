"""Redis memory backend for shared/multi-instance setups."""

import json
import logging

from mithai.memory.base import MemoryBackend, SearchMatch, SearchResult
from mithai.memory.inmemory import _glob_to_regex

logger = logging.getLogger(__name__)


class RedisMemoryBackend(MemoryBackend):
    """
    Stores memory as Redis string values.

    Keys are prefixed: {prefix}:{virtual_path}
    Search scans keys and reads content.

    Requires: redis (pip install mithai[redis])
    """

    def __init__(self, url: str = "redis://localhost:6379", prefix: str = "mithai:memory"):
        import redis

        self._redis = redis.from_url(url, decode_responses=True)
        self._prefix = prefix

    def _key(self, path: str) -> str:
        return f"{self._prefix}:{path}"

    def _unkey(self, key: str) -> str:
        return key[len(self._prefix) + 1 :]

    def read(self, path: str) -> str | None:
        if not self.validate_path(path):
            return None
        return self._redis.get(self._key(path))

    def write(self, path: str, content: str, *, append: bool = False) -> None:
        if not self.validate_path(path):
            raise ValueError(f"Invalid path: {path}")
        key = self._key(path)
        if append:
            self._redis.append(key, content)
        else:
            self._redis.set(key, content)

    def exists(self, path: str) -> bool:
        if not self.validate_path(path):
            return False
        return self._redis.exists(self._key(path)) > 0

    def search(
        self, query: str, *, glob: str = "**/*.md", max_matches_per_file: int = 5
    ) -> list[SearchResult]:
        query_lower = query.lower()
        results = []
        pattern_re = _glob_to_regex(glob)

        cursor = 0
        scan_pattern = f"{self._prefix}:*"
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=scan_pattern, count=100)
            for key in keys:
                vpath = self._unkey(key)
                if not pattern_re.match(vpath):
                    continue
                content = self._redis.get(key)
                if content is None or query_lower not in content.lower():
                    continue
                matches = []
                for i, line in enumerate(content.splitlines(), 1):
                    if query_lower in line.lower():
                        matches.append(SearchMatch(line=i, text=line.strip()[:200]))
                        if len(matches) >= max_matches_per_file:
                            break
                results.append(SearchResult(path=vpath, matches=matches))
            if cursor == 0:
                break

        return results

    def list_files(self, prefix: str = "", *, glob: str = "*") -> list[str]:
        full_pattern = f"{self._prefix}:{prefix}*" if prefix else f"{self._prefix}:*"
        full_glob = f"{prefix}{glob}" if prefix else glob
        pattern_re = _glob_to_regex(full_glob)
        result = []
        cursor = 0
        while True:
            cursor, keys = self._redis.scan(cursor=cursor, match=full_pattern, count=100)
            for key in keys:
                vpath = self._unkey(key)
                if pattern_re.match(vpath):
                    result.append(vpath)
            if cursor == 0:
                break
        return sorted(result)

    def read_json(self, path: str) -> dict | list | None:
        content = self.read(path)
        if content is None:
            return None
        return json.loads(content)

    def write_json(self, path: str, data: dict | list) -> None:
        self.write(path, json.dumps(data, indent=2), append=False)
