"""Tests for memory backends — contract tests run against every implementation."""

import pytest

from mithai.memory.filesystem import FilesystemMemoryBackend
from mithai.memory.inmemory import InMemoryMemoryBackend


@pytest.fixture
def fs_backend(tmp_path):
    return FilesystemMemoryBackend(tmp_path / "memory")


@pytest.fixture
def mem_backend():
    return InMemoryMemoryBackend()


@pytest.fixture(params=["filesystem", "inmemory"])
def backend(request, tmp_path):
    """Parameterized fixture that runs each test against both backends."""
    if request.param == "filesystem":
        return FilesystemMemoryBackend(tmp_path / "memory")
    return InMemoryMemoryBackend()


class TestReadWrite:
    def test_read_nonexistent_returns_none(self, backend):
        assert backend.read("nonexistent.md") is None

    def test_write_and_read(self, backend):
        backend.write("test.md", "hello")
        assert backend.read("test.md") == "hello"

    def test_write_overwrite(self, backend):
        backend.write("test.md", "old")
        backend.write("test.md", "new")
        assert backend.read("test.md") == "new"

    def test_write_append(self, backend):
        backend.write("test.md", "line1\n")
        backend.write("test.md", "line2\n", append=True)
        content = backend.read("test.md")
        assert "line1" in content
        assert "line2" in content

    def test_nested_paths(self, backend):
        backend.write("playbooks/restart.md", "step 1")
        assert backend.read("playbooks/restart.md") == "step 1"

    def test_deeply_nested_paths(self, backend):
        backend.write("daily/2026/03/01.md", "data")
        assert backend.read("daily/2026/03/01.md") == "data"


class TestExists:
    def test_exists_false(self, backend):
        assert not backend.exists("nope.md")

    def test_exists_true(self, backend):
        backend.write("test.md", "data")
        assert backend.exists("test.md")


class TestSearch:
    def test_search_finds_match(self, backend):
        backend.write("MEMORY.md", "DaemonSet cannot be restarted")
        results = backend.search("DaemonSet")
        assert len(results) == 1
        assert results[0].path == "MEMORY.md"

    def test_search_case_insensitive(self, backend):
        backend.write("test.md", "Minikube cluster")
        results = backend.search("minikube")
        assert len(results) == 1

    def test_search_no_match(self, backend):
        backend.write("test.md", "nothing relevant")
        assert backend.search("kubernetes") == []

    def test_search_multiple_files(self, backend):
        backend.write("a.md", "cluster info")
        backend.write("b.md", "cluster status")
        results = backend.search("cluster")
        assert len(results) == 2

    def test_search_returns_line_context(self, backend):
        backend.write("test.md", "line 1\nkubernetes cluster\nline 3")
        results = backend.search("kubernetes")
        matches = results[0].matches
        assert matches[0].line == 2
        assert "kubernetes" in matches[0].text

    def test_search_max_matches_per_file(self, backend):
        lines = "\n".join(f"match line {i}" for i in range(20))
        backend.write("test.md", lines)
        results = backend.search("match", max_matches_per_file=3)
        assert len(results[0].matches) == 3

    def test_search_glob_filter(self, backend):
        backend.write("data.json", "cluster info")
        backend.write("data.md", "cluster info")
        results = backend.search("cluster", glob="**/*.md")
        assert len(results) == 1
        assert results[0].path == "data.md"


class TestListFiles:
    def test_list_empty(self, backend):
        assert backend.list_files() == []

    def test_list_files(self, backend):
        backend.write("MEMORY.md", "data")
        backend.write("daily/2026-03-01.md", "data")
        files = backend.list_files(glob="**/*.md")
        assert "MEMORY.md" in files


class TestPathSafety:
    def test_path_traversal_blocked(self, backend):
        assert not backend.validate_path("../../etc/passwd")

    def test_absolute_path_blocked(self, backend):
        assert not backend.validate_path("/etc/passwd")

    def test_valid_path(self, backend):
        assert backend.validate_path("MEMORY.md")
        assert backend.validate_path("playbooks/restart.md")
        assert backend.validate_path("daily/2026-03-01.md")


class TestJson:
    def test_read_json_write_json(self, backend):
        data = {"tool": {"cmd": {"approved": 3, "denied": 0}}}
        backend.write_json("approvals.json", data)
        loaded = backend.read_json("approvals.json")
        assert loaded == data

    def test_read_json_nonexistent(self, backend):
        assert backend.read_json("nope.json") is None

    def test_write_json_overwrites(self, backend):
        backend.write_json("data.json", {"old": True})
        backend.write_json("data.json", {"new": True})
        assert backend.read_json("data.json") == {"new": True}
