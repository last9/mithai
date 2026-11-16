"""Tests for state backends."""

import threading

import pytest

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


@pytest.fixture(params=["memory", "filesystem"])
def state(request, tmp_path):
    if request.param == "memory":
        return MemoryStateBackend()
    return FilesystemStateBackend(tmp_path / "state")


def test_update_passes_none_for_unset_key_and_returns_new_value(state):
    seen = []

    def bump(current):
        seen.append(current)
        return (current or 0) + 1

    assert state.update("ns", "counter", bump) == 1
    assert state.update("ns", "counter", bump) == 2
    assert seen == [None, 1]
    assert state.get("ns", "counter") == 2
    assert state.list_keys("ns") == ["counter"]


def test_concurrent_updates_are_not_lost(state):
    def bump_many():
        for _ in range(25):
            state.update("ns", "counter", lambda current: (current or 0) + 1)

    threads = [threading.Thread(target=bump_many) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state.get("ns", "counter") == 200
