import json
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
