"""Tests for flock-based file locking in FilesystemMemoryBackend.

Verifies that concurrent reads, writes, and mixed read/write operations
are safe under advisory file locking.
"""

import multiprocessing

import pytest

from mithai.memory.filesystem import FilesystemMemoryBackend, _flock


@pytest.fixture
def backend(tmp_path):
    return FilesystemMemoryBackend(tmp_path / "memory")


@pytest.fixture
def mem_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# _flock context-manager unit tests
# ---------------------------------------------------------------------------


class TestFlockContextManager:
    def test_shared_lock_allows_read(self, mem_dir):
        p = mem_dir / "f.txt"
        p.write_text("data", encoding="utf-8")
        with _flock(p, exclusive=False) as fh:
            fh.seek(0)
            assert fh.read() == "data"

    def test_exclusive_lock_allows_write(self, mem_dir):
        p = mem_dir / "f.txt"
        p.write_text("old", encoding="utf-8")
        with _flock(p, exclusive=True) as fh:
            fh.seek(0)
            fh.truncate()
            fh.write("new")
        assert p.read_text(encoding="utf-8") == "new"

    def test_lock_released_on_exception(self, mem_dir):
        p = mem_dir / "f.txt"
        p.write_text("data", encoding="utf-8")
        with pytest.raises(RuntimeError):
            with _flock(p, exclusive=True):
                raise RuntimeError("boom")
        # Lock must be released — another exclusive lock should succeed immediately.
        with _flock(p, exclusive=True) as fh:
            fh.seek(0)
            assert fh.read() == "data"

    def test_creates_file_if_missing(self, mem_dir):
        p = mem_dir / "new.txt"
        assert not p.exists()
        with _flock(p, exclusive=True) as fh:
            fh.write("created")
        assert p.read_text(encoding="utf-8") == "created"


# ---------------------------------------------------------------------------
# Backend-level locking integration tests
# ---------------------------------------------------------------------------


class TestLockedReadWrite:
    def test_write_then_read(self, backend):
        backend.write("test.md", "hello")
        assert backend.read("test.md") == "hello"

    def test_overwrite(self, backend):
        backend.write("test.md", "old")
        backend.write("test.md", "new")
        assert backend.read("test.md") == "new"

    def test_append(self, backend):
        backend.write("test.md", "a")
        backend.write("test.md", "b", append=True)
        assert backend.read("test.md") == "ab"

    def test_search_under_lock(self, backend):
        backend.write("notes.md", "important keyword here")
        results = backend.search("keyword")
        assert len(results) == 1
        assert results[0].path == "notes.md"


# ---------------------------------------------------------------------------
# Multiprocess concurrency tests
# ---------------------------------------------------------------------------

def _writer(base_path: str, key: str, value: str):
    """Write *value* to *key* via a fresh backend instance."""
    b = FilesystemMemoryBackend(base_path)
    b.write(key, value)


def _appender(base_path: str, key: str, token: str, count: int):
    """Append *token* to *key* ``count`` times."""
    b = FilesystemMemoryBackend(base_path)
    for _ in range(count):
        b.write(key, token, append=True)


def _reader(base_path: str, key: str, result_queue):
    """Read *key* and push the value into a multiprocessing queue."""
    b = FilesystemMemoryBackend(base_path)
    result_queue.put(b.read(key))


class TestConcurrentWrites:
    def test_concurrent_overwrites_no_corruption(self, mem_dir):
        """Two processes overwriting the same file must not produce garbled content."""
        base = str(mem_dir)
        b = FilesystemMemoryBackend(base)
        b.write("shared.md", "")

        p1 = multiprocessing.Process(target=_writer, args=(base, "shared.md", "AAAA" * 1000))
        p2 = multiprocessing.Process(target=_writer, args=(base, "shared.md", "BBBB" * 1000))
        p1.start()
        p2.start()
        p1.join(timeout=5)
        p2.join(timeout=5)
        assert p1.exitcode == 0, f"writer 1 failed with {p1.exitcode}"
        assert p2.exitcode == 0, f"writer 2 failed with {p2.exitcode}"

        content = b.read("shared.md")
        # One of the two values must win cleanly — no interleaving.
        assert content in ("AAAA" * 1000, "BBBB" * 1000)

    def test_concurrent_appends_no_lost_writes(self, mem_dir):
        """Concurrent appenders must not lose tokens."""
        base = str(mem_dir)
        b = FilesystemMemoryBackend(base)
        b.write("log.md", "")

        n = 50
        p1 = multiprocessing.Process(target=_appender, args=(base, "log.md", "A", n))
        p2 = multiprocessing.Process(target=_appender, args=(base, "log.md", "B", n))
        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)
        assert p1.exitcode == 0, f"appender 1 failed with {p1.exitcode}"
        assert p2.exitcode == 0, f"appender 2 failed with {p2.exitcode}"

        content = b.read("log.md")
        assert content.count("A") == n
        assert content.count("B") == n
        assert len(content) == 2 * n


class TestConcurrentReads:
    def test_concurrent_reads_consistent(self, mem_dir):
        """Multiple readers must all see the same committed content."""
        base = str(mem_dir)
        b = FilesystemMemoryBackend(base)
        b.write("stable.md", "snapshot")

        q = multiprocessing.Queue()
        procs = [multiprocessing.Process(target=_reader, args=(base, "stable.md", q)) for _ in range(5)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=5)
        for i, p in enumerate(procs):
            assert p.exitcode == 0, f"reader {i} failed with {p.exitcode}"

        results = [q.get_nowait() for _ in range(5)]
        assert all(r == "snapshot" for r in results)


class TestConcurrentReadWrite:
    def test_reader_never_sees_partial_write(self, mem_dir):
        """A reader must see either the old or the new value, never a partial mix."""
        base = str(mem_dir)
        b = FilesystemMemoryBackend(base)
        old = "X" * 2000
        new = "Y" * 2000
        b.write("data.md", old)

        q = multiprocessing.Queue()
        writer = multiprocessing.Process(target=_writer, args=(base, "data.md", new))
        reader = multiprocessing.Process(target=_reader, args=(base, "data.md", q))
        writer.start()
        reader.start()
        writer.join(timeout=5)
        reader.join(timeout=5)
        assert writer.exitcode == 0, f"writer failed with {writer.exitcode}"
        assert reader.exitcode == 0, f"reader failed with {reader.exitcode}"

        result = q.get_nowait()
        assert result in (old, new)


class TestLockReleaseOnError:
    def test_write_error_releases_lock(self, mem_dir):
        """If write raises after acquiring the lock, subsequent operations must not deadlock."""
        base = str(mem_dir)
        b = FilesystemMemoryBackend(base)
        b.write("target.md", "original")

        # Provoke an error inside _flock by making the path invalid after resolve.
        # Simpler: just verify the file is still usable after a normal write error path.
        # We'll test via the _flock helper directly.
        target = mem_dir / "target.md"
        with pytest.raises(ValueError):
            with _flock(target, exclusive=True):
                raise ValueError("simulated write error")

        # Must not deadlock — the lock was released in the finally block.
        assert b.read("target.md") == "original"
        b.write("target.md", "updated")
        assert b.read("target.md") == "updated"
