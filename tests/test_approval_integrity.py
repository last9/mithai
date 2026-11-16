import builtins
import io
import json
import os
import threading

import yaml

from mithai.memory.filesystem import FilesystemMemoryBackend
from mithai.state.filesystem import FilesystemStateBackend
from mithai.core.engine import Engine
from skills.memory.tools import handle
from skills.shell.tools import resolve_human


def test_memory_approval_history_cannot_authorize_shell(tmp_path):
    memory = FilesystemMemoryBackend(tmp_path / "memory")
    state = FilesystemStateBackend(tmp_path / "state")
    command = "touch /tmp/example"
    history = {"shell__run_command": {command: {"approved": 3, "denied": 0}}}
    handle("memory_write", {"path": "approvals.json", "content": json.dumps(history), "mode": "overwrite"}, {"memory": memory})
    assert resolve_human("run_command", {"command": command}, {"memory": memory, "state": state}) == "approve"
    state.set("approvals", "history", history)
    assert resolve_human("run_command", {"command": command}, {"memory": memory, "state": state}) is None


def test_approval_state_inside_memory_cannot_authorize_shell(tmp_path):
    memory = FilesystemMemoryBackend(tmp_path / "memory")
    state = FilesystemStateBackend(tmp_path / "memory" / "state")
    command = "touch /tmp/example"
    state.set("approvals", "history", {"shell__run_command": {command: {"approved": 3, "denied": 0}}})
    assert resolve_human("run_command", {"command": command}, {"memory": memory, "state": state}) == "approve"


def test_symlinked_state_inside_memory_cannot_authorize_shell(tmp_path):
    memory = FilesystemMemoryBackend(tmp_path / "memory")
    (tmp_path / "state-link").symlink_to(tmp_path / "memory", target_is_directory=True)
    state = FilesystemStateBackend(tmp_path / "state-link")
    command = "touch /tmp/example"
    state.set("approvals", "history", {"shell__run_command": {command: {"approved": 3, "denied": 0}}})
    assert resolve_human("run_command", {"command": command}, {"memory": memory, "state": state}) == "approve"


def test_engine_records_approval_in_state(tmp_path, tmp_config):
    memory = FilesystemMemoryBackend(tmp_path / "memory")
    state = FilesystemStateBackend(tmp_path / "state")
    engine = Engine(yaml.safe_load(tmp_config.read_text()), None, state, memory, skills={})
    command = "touch /tmp/example"
    for _ in range(3):
        engine._record_approval("shell__run_command", {"command": command}, True)
    assert resolve_human("run_command", {"command": command}, {"memory": memory, "state": state}) is None
    assert memory.read("approvals.json") is None


def test_approval_histories_are_isolated_between_agents(tmp_path, tmp_config):
    state = FilesystemStateBackend(tmp_path / "state")
    config = yaml.safe_load(tmp_config.read_text())
    first = Engine(config, None, state, agent_id="first", skills={})
    second = Engine(config, None, state, agent_id="second", skills={})
    command = "touch /tmp/example"
    for _ in range(3):
        first._record_approval("shell__run_command", {"command": command}, True)
    assert resolve_human("run_command", {"command": command}, {"state": state, "approval_key": first._approval_key}) is None
    assert resolve_human("run_command", {"command": command}, {"state": state, "approval_key": second._approval_key}) == "approve"


def test_concurrent_approval_write_keeps_denial(tmp_path, tmp_config, monkeypatch):
    """A denial recorded while another decision is mid-write must not be lost."""
    state = FilesystemStateBackend(tmp_path / "state")
    engine = Engine(yaml.safe_load(tmp_config.read_text()), None, state, skills={})
    command = "touch /tmp/example"
    for _ in range(2):
        engine._record_approval("shell__run_command", {"command": command}, True)

    writer_paused = threading.Event()
    release_writer = threading.Event()
    denial_started = threading.Event()
    real_open = io.open

    def pausing_open(file, mode="r", *args, **kwargs):
        opened = real_open(file, mode, *args, **kwargs)
        if f"{os.sep}approvals{os.sep}" in str(file):
            thread = threading.current_thread().name
            if thread == "denial":
                denial_started.set()
            elif thread == "approval" and "w" in mode:
                # Opening for write has already truncated an in-place target.
                writer_paused.set()
                assert release_writer.wait(5)
        return opened

    monkeypatch.setattr(io, "open", pausing_open)
    monkeypatch.setattr(builtins, "open", pausing_open)
    approval = threading.Thread(
        target=engine._record_approval,
        args=("shell__run_command", {"command": command}, True),
        name="approval",
    )
    denial = threading.Thread(
        target=engine._record_approval,
        args=("shell__run_command", {"command": command}, False),
        name="denial",
    )
    approval.start()
    try:
        assert writer_paused.wait(5)
        denial.start()
        assert denial_started.wait(5)
        denial.join(0.2)
    finally:
        release_writer.set()
        approval.join(5)
        denial.join(5)
    monkeypatch.undo()

    counts = state.get("approvals", "history")["shell__run_command"][command]
    assert counts == {"approved": 3, "denied": 1}
    assert resolve_human("run_command", {"command": command}, {"state": state}) == "approve"
