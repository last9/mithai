"""Tests for ControlRoomData — the Control Room data access layer."""


import pytest

from mithai.memory.filesystem import FilesystemMemoryBackend
from mithai.state.filesystem import FilesystemStateBackend
from mithai.ui.data import ControlRoomData, _redact_secrets


@pytest.fixture
def state_dir(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def memory_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture
def state(state_dir):
    return FilesystemStateBackend(str(state_dir))


@pytest.fixture
def memory(memory_dir):
    return FilesystemMemoryBackend(memory_dir)


@pytest.fixture
def config(tmp_path):
    skills_dir = tmp_path / "skills" / "parrot"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("You are a parrot.")
    (skills_dir / "tools.py").write_text(
        'TOOLS = [{"name": "echo", "description": "Echo input", '
        '"input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}]\n'
        'def handle(name, input, ctx): return input.get("text", "")\n'
    )
    return {
        "skills": {"paths": [str(tmp_path / "skills")]},
        "learning": {"approval_auto_promote": 3},
    }


@pytest.fixture
def ctrl(state, memory, config):
    return ControlRoomData(state=state, memory=memory, config=config)


class TestSessions:
    def test_list_sessions_empty(self, ctrl):
        assert ctrl.list_sessions() == []

    def test_list_sessions_with_data(self, ctrl, state):
        state.set("sessions", "slack:C1", {
            "session_id": "slack:C1",
            "platform": "slack",
            "channel_id": "C1",
            "created_at": "2026-03-01T10:00:00",
            "updated_at": "2026-03-01T10:05:00",
            "turns": [
                {"user_message": "hello", "tool_calls": [], "assistant_response": "hi"},
            ],
        })
        sessions = ctrl.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "slack:C1"
        assert sessions[0]["turn_count"] == 1

    def test_get_session(self, ctrl, state):
        state.set("sessions", "cli:test", {
            "session_id": "cli:test",
            "platform": "cli",
            "channel_id": "test",
            "turns": [],
        })
        session = ctrl.get_session("cli:test")
        assert session is not None
        assert session["platform"] == "cli"

    def test_get_session_missing(self, ctrl):
        assert ctrl.get_session("nonexistent") is None

    def test_search_sessions(self, ctrl, state):
        state.set("sessions", "slack:C1", {
            "session_id": "slack:C1",
            "platform": "slack",
            "channel_id": "C1",
            "turns": [
                {"user_message": "check kubernetes pods", "assistant_response": "here are pods"},
            ],
        })
        results = ctrl.search_sessions("kubernetes")
        assert len(results) == 1

    def test_session_stats(self, ctrl, state):
        state.set("sessions", "slack:C1", {
            "platform": "slack",
            "turns": [{"user_message": "a"}, {"user_message": "b"}],
        })
        state.set("sessions", "cli:local", {
            "platform": "cli",
            "turns": [{"user_message": "c"}],
        })
        stats = ctrl.get_session_stats()
        assert stats["total"] == 2
        assert stats["by_platform"]["slack"] == 1
        assert stats["by_platform"]["cli"] == 1
        assert stats["total_turns"] == 3
        assert stats["avg_turns"] == 1.5

    def test_session_stats_empty(self, ctrl):
        stats = ctrl.get_session_stats()
        assert stats["total"] == 0
        assert stats["avg_turns"] == 0


class TestApprovals:
    def test_get_approvals_empty(self, ctrl):
        assert ctrl.get_approvals() == {}

    def test_get_approvals_with_data(self, ctrl, memory):
        data = {
            "shell__run_command": {
                "df -h": {"approved": 5, "denied": 0},
                "rm -rf /": {"approved": 0, "denied": 2},
            }
        }
        memory.write_json("approvals.json", data)
        result = ctrl.get_approvals()
        assert result["shell__run_command"]["df -h"]["approved"] == 5

    def test_approval_stats(self, ctrl, memory):
        data = {
            "shell__run_command": {
                "df -h": {"approved": 5, "denied": 0},
                "ps aux": {"approved": 3, "denied": 0},
                "rm -rf /": {"approved": 0, "denied": 2},
            }
        }
        memory.write_json("approvals.json", data)
        stats = ctrl.get_approval_stats()
        assert stats["total_approved"] == 8
        assert stats["total_denied"] == 2
        assert stats["auto_promoted_count"] == 2  # df -h (5>=3) and ps aux (3>=3)
        assert stats["threshold"] == 3

    def test_approval_stats_empty(self, ctrl):
        stats = ctrl.get_approval_stats()
        assert stats["total_approved"] == 0
        assert stats["auto_promoted_count"] == 0

    def test_approvals_no_memory(self, state, config):
        ctrl = ControlRoomData(state=state, memory=None, config=config)
        assert ctrl.get_approvals() == {}


class TestMemory:
    def test_list_memory_files(self, ctrl, memory_dir):
        (memory_dir / "MEMORY.md").write_text("hello")
        (memory_dir / "daily").mkdir()
        (memory_dir / "daily" / "2026-03-01.md").write_text("learnings")
        files = ctrl.list_memory_files()
        assert "MEMORY.md" in files
        assert "daily/2026-03-01.md" in files

    def test_read_memory_file(self, ctrl, memory_dir):
        (memory_dir / "MEMORY.md").write_text("my knowledge")
        content = ctrl.read_memory_file("MEMORY.md")
        assert content == "my knowledge"

    def test_read_memory_missing(self, ctrl):
        assert ctrl.read_memory_file("nonexistent.md") is None

    def test_search_memory(self, ctrl, memory_dir):
        (memory_dir / "test.md").write_text("kubernetes pods are running")
        results = ctrl.search_memory("kubernetes")
        assert len(results) == 1
        assert results[0]["file"] == "test.md"

    def test_memory_no_backend(self, state, config):
        ctrl = ControlRoomData(state=state, memory=None, config=config)
        assert ctrl.list_memory_files() == []
        assert ctrl.read_memory_file("test") is None
        assert ctrl.search_memory("test") == []


class TestSkills:
    def test_list_skills(self, ctrl):
        skills = ctrl.list_skills()
        assert len(skills) >= 1
        parrot = [s for s in skills if s["name"] == "parrot"]
        assert len(parrot) == 1
        assert parrot[0]["tool_count"] == 1
        assert parrot[0]["tools"][0]["name"] == "echo"

    def test_skill_tool_details(self, ctrl):
        skills = ctrl.list_skills()
        parrot = [s for s in skills if s["name"] == "parrot"]
        tool = parrot[0]["tools"][0]
        assert tool["human"] == "none"
        assert "properties" in tool["input_schema"]


class TestConfig:
    def test_get_config_redacts_secrets(self):
        config = {
            "llm": {"provider": "anthropic", "anthropic": {"api_key": "sk-ant-1234"}},
            "adapter": {"slack": {"bot_token": "xoxb-abc", "app_token": "xapp-def"}},
            "bot": {"name": "mithai"},
        }
        ctrl = ControlRoomData(
            state=None, memory=None, config=config,
        )
        redacted = ctrl.get_config()
        assert redacted["llm"]["anthropic"]["api_key"] == "***REDACTED***"
        assert redacted["adapter"]["slack"]["bot_token"] == "***REDACTED***"
        assert redacted["adapter"]["slack"]["app_token"] == "***REDACTED***"
        assert redacted["bot"]["name"] == "mithai"


class TestRedactSecrets:
    def test_redacts_api_key(self):
        assert _redact_secrets({"api_key": "secret"}) == {"api_key": "***REDACTED***"}

    def test_redacts_token(self):
        assert _redact_secrets({"bot_token": "xoxb"}) == {"bot_token": "***REDACTED***"}

    def test_redacts_env_var_syntax(self):
        assert _redact_secrets({"host": "${SECRET_VAR}"}) == {"host": "***REDACTED***"}

    def test_preserves_normal_values(self):
        assert _redact_secrets({"name": "mithai"}) == {"name": "mithai"}

    def test_handles_nested(self):
        result = _redact_secrets({"a": {"password": "bad"}})
        assert result["a"]["password"] == "***REDACTED***"

    def test_handles_lists(self):
        # Items in a list under a secret-named key get redacted
        result = _redact_secrets({"tokens": ["a", "b"]})
        assert result == {"tokens": ["***REDACTED***", "***REDACTED***"]}

    def test_preserves_safe_lists(self):
        result = _redact_secrets({"paths": ["./skills", "./more"]})
        assert result == {"paths": ["./skills", "./more"]}
