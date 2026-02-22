"""Tests for state backends."""

from mithai.state.memory import MemoryStateBackend
from mithai.state.filesystem import FilesystemStateBackend


def test_memory_backend():
    state = MemoryStateBackend()

    assert state.get("ns", "key") is None
    assert state.list_keys("ns") == []

    state.set("ns", "key", {"data": 1})
    assert state.get("ns", "key") == {"data": 1}
    assert state.list_keys("ns") == ["key"]

    state.set("ns", "key2", "value2")
    assert sorted(state.list_keys("ns")) == ["key", "key2"]

    state.delete("ns", "key")
    assert state.get("ns", "key") is None
    assert state.list_keys("ns") == ["key2"]


def test_filesystem_backend(tmp_path):
    state = FilesystemStateBackend(tmp_path / "state")

    assert state.get("ns", "key") is None
    assert state.list_keys("ns") == []

    state.set("ns", "key", {"data": 1})
    assert state.get("ns", "key") == {"data": 1}
    assert state.list_keys("ns") == ["key"]

    state.delete("ns", "key")
    assert state.get("ns", "key") is None
    assert state.list_keys("ns") == []
