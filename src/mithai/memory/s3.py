"""S3/Object Storage memory backend for durability."""

import json
import logging

from mithai.memory.base import MemoryBackend, SearchMatch, SearchResult
from mithai.memory.inmemory import _glob_to_regex

logger = logging.getLogger(__name__)


class S3MemoryBackend(MemoryBackend):
    """
    Stores memory as S3 objects.

    Keys: {prefix}/{virtual_path}

    Note: append requires read-then-write (S3 has no native append).
    For high-write concurrency, prefer the Redis backend.

    Requires: boto3 (pip install mithai[s3])
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "memory",
        region: str | None = None,
        profile: str | None = None,
    ):
        import boto3

        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        if region:
            session_kwargs["region_name"] = region
        session = boto3.Session(**session_kwargs)
        self._s3 = session.client("s3")
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")

    def _key(self, path: str) -> str:
        return f"{self._prefix}/{path}"

    def _unkey(self, key: str) -> str:
        return key[len(self._prefix) + 1 :]

    def read(self, path: str) -> str | None:
        if not self.validate_path(path):
            return None
        import botocore.exceptions

        try:
            response = self._s3.get_object(Bucket=self._bucket, Key=self._key(path))
            return response["Body"].read().decode("utf-8")
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    def write(self, path: str, content: str, *, append: bool = False) -> None:
        if not self.validate_path(path):
            raise ValueError(f"Invalid path: {path}")
        if append:
            existing = self.read(path)
            if existing:
                content = existing + content
        self._s3.put_object(
            Bucket=self._bucket,
            Key=self._key(path),
            Body=content.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )

    def exists(self, path: str) -> bool:
        if not self.validate_path(path):
            return False
        import botocore.exceptions

        try:
            self._s3.head_object(Bucket=self._bucket, Key=self._key(path))
            return True
        except botocore.exceptions.ClientError:
            return False

    def search(
        self, query: str, *, glob: str = "**/*.md", max_matches_per_file: int = 5
    ) -> list[SearchResult]:
        query_lower = query.lower()
        results = []
        pattern_re = _glob_to_regex(glob)

        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix + "/"):
            for obj in page.get("Contents", []):
                vpath = self._unkey(obj["Key"])
                if not pattern_re.match(vpath):
                    continue
                content = self.read(vpath)
                if content is None or query_lower not in content.lower():
                    continue
                matches = []
                for i, line in enumerate(content.splitlines(), 1):
                    if query_lower in line.lower():
                        matches.append(SearchMatch(line=i, text=line.strip()[:200]))
                        if len(matches) >= max_matches_per_file:
                            break
                results.append(SearchResult(path=vpath, matches=matches))

        return results

    def list_files(self, prefix: str = "", *, glob: str = "*") -> list[str]:
        full_prefix = f"{self._prefix}/{prefix}" if prefix else f"{self._prefix}/"
        full_glob = f"{prefix}{glob}" if prefix else glob
        pattern_re = _glob_to_regex(full_glob)
        result = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                vpath = self._unkey(obj["Key"])
                if pattern_re.match(vpath):
                    result.append(vpath)
        return sorted(result)

    def read_json(self, path: str) -> dict | list | None:
        content = self.read(path)
        if content is None:
            return None
        return json.loads(content)

    def write_json(self, path: str, data: dict | list) -> None:
        self.write(path, json.dumps(data, indent=2), append=False)
