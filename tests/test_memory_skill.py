"""Tests for the memory skill."""

import json

import pytest

from mithai.memory.filesystem import FilesystemMemoryBackend


@pytest.fixture
def memory_dir(tmp_path):
    """Create a temporary memory directory."""
    mem = tmp_path / "memory"
    mem.mkdir()
    return mem


@pytest.fixture
def memory_backend(memory_dir):
    """Create a FilesystemMemoryBackend pointing to the temp dir."""
    return FilesystemMemoryBackend(memory_dir)


@pytest.fixture
def ctx(memory_backend):
    """Build a skill context with memory backend."""
    return {"memory": memory_backend, "config": {}}


def _handle(name, input, ctx):
    from skills.memory.tools import handle
    return json.loads(handle(name, input, ctx))


class TestMemoryRead:
    def test_read_existing_file(self, ctx, memory_dir):
        (memory_dir / "MEMORY.md").write_text("hello world")
        result = _handle("memory_read", {"path": "MEMORY.md"}, ctx)
        assert result["content"] == "hello world"

    def test_read_missing_file(self, ctx):
        result = _handle("memory_read", {"path": "nope.md"}, ctx)
        assert "error" in result

    def test_read_path_escape_blocked(self, ctx):
        result = _handle("memory_read", {"path": "../../etc/passwd"}, ctx)
        assert result == {"error": "Invalid path"}

    def test_read_nested_file(self, ctx, memory_dir):
        (memory_dir / "playbooks").mkdir()
        (memory_dir / "playbooks" / "restart.md").write_text("step 1")
        result = _handle("memory_read", {"path": "playbooks/restart.md"}, ctx)
        assert result["content"] == "step 1"


class TestMemoryWrite:
    def test_write_append(self, ctx, memory_dir):
        _handle("memory_write", {"path": "MEMORY.md", "content": "line1"}, ctx)
        _handle("memory_write", {"path": "MEMORY.md", "content": "line2"}, ctx)
        content = (memory_dir / "MEMORY.md").read_text()
        assert "line1" in content
        assert "line2" in content

    def test_write_overwrite(self, ctx, memory_dir):
        _handle("memory_write", {"path": "test.md", "content": "old", "mode": "overwrite"}, ctx)
        _handle("memory_write", {"path": "test.md", "content": "new", "mode": "overwrite"}, ctx)
        content = (memory_dir / "test.md").read_text()
        assert content == "new"
        assert "old" not in content

    def test_write_creates_subdirs(self, ctx, memory_dir):
        result = _handle("memory_write", {"path": "playbooks/new.md", "content": "data"}, ctx)
        assert result["written"] == "playbooks/new.md"
        assert (memory_dir / "playbooks" / "new.md").exists()

    def test_write_path_escape_blocked(self, ctx):
        result = _handle("memory_write", {"path": "../escape.md", "content": "bad"}, ctx)
        assert result == {"error": "Invalid path"}


class TestMemorySearch:
    def test_search_finds_match(self, ctx, memory_dir):
        (memory_dir / "MEMORY.md").write_text("DaemonSet cannot be restarted directly")
        result = _handle("memory_search", {"query": "DaemonSet"}, ctx)
        assert len(result["results"]) == 1
        assert result["results"][0]["file"] == "MEMORY.md"

    def test_search_case_insensitive(self, ctx, memory_dir):
        (memory_dir / "test.md").write_text("Minikube cluster is running")
        result = _handle("memory_search", {"query": "minikube"}, ctx)
        assert len(result["results"]) == 1

    def test_search_no_match(self, ctx, memory_dir):
        (memory_dir / "test.md").write_text("nothing relevant")
        result = _handle("memory_search", {"query": "kubernetes"}, ctx)
        assert result["results"] == []

    def test_search_multiple_files(self, ctx, memory_dir):
        (memory_dir / "a.md").write_text("cluster info")
        (memory_dir / "b.md").write_text("cluster status")
        result = _handle("memory_search", {"query": "cluster"}, ctx)
        assert len(result["results"]) == 2

    def test_search_empty_memory(self, ctx, memory_dir):
        # Remove the dir to test empty case
        import shutil
        shutil.rmtree(memory_dir)
        result = _handle("memory_search", {"query": "anything"}, ctx)
        assert result["results"] == []

    def test_search_returns_line_context(self, ctx, memory_dir):
        (memory_dir / "test.md").write_text("line 1\nkubernetes cluster\nline 3")
        result = _handle("memory_search", {"query": "kubernetes"}, ctx)
        matches = result["results"][0]["matches"]
        assert matches[0]["line"] == 2
        assert "kubernetes" in matches[0]["text"]


class TestNoMemoryBackend:
    def test_no_memory_returns_error(self):
        ctx = {"config": {}}
        result = _handle("memory_read", {"path": "test.md"}, ctx)
        assert "error" in result
        assert "not configured" in result["error"]


class TestUnknownTool:
    def test_unknown_tool(self, ctx):
        result = _handle("nonexistent", {}, ctx)
        assert "error" in result
