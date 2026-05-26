"""Tests for IncomingMessage.extra_system_prompt being appended to the system prompt."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

from mithai.adapters.base import IncomingMessage
from mithai.core.engine import Engine
from mithai.core.skill_loader import Skill, ToolDefinition
from mithai.memory.filesystem import FilesystemMemoryBackend
from mithai.state.memory import MemoryStateBackend


def _make_engine():
    llm = MagicMock()
    resp = MagicMock()
    resp.content = [{"type": "text", "text": "done"}]
    resp.stop_reason = "end_turn"
    llm.create_message.return_value = resp

    state = MemoryStateBackend()
    memory = FilesystemMemoryBackend(Path(tempfile.mkdtemp()))
    config = {
        "bot": {"system_prompt": "You are a helpful assistant."},
        "learning": {"enabled": False},
        "llm": {"provider": "anthropic", "api_key": "test"},
    }
    engine = Engine(config=config, llm=llm, state=state, memory=memory, skills={})
    return engine, llm


def _adapter():
    a = MagicMock()
    a.fetch_thread_context.return_value = None
    return a


def _system_from_call(llm_mock):
    """Return the system prompt string passed to the most recent create_message call."""
    call_kwargs = llm_mock.create_message.call_args
    return (call_kwargs.kwargs or call_kwargs[1]).get("system", "")


class TestExtraSystemPrompt:
    def test_extra_system_prompt_appended(self):
        engine, llm = _make_engine()
        msg = IncomingMessage(
            text="do task",
            channel_id="C1",
            user_id="alice",
            platform="cli",
            extra_system_prompt="## Task Instructions\nFollow these rules.",
        )
        engine.handle(msg, _adapter())

        system = _system_from_call(llm)
        assert "You are a helpful assistant." in system
        assert "## Task Instructions" in system
        assert "Follow these rules." in system

    def test_extra_system_prompt_separator_present(self):
        engine, llm = _make_engine()
        msg = IncomingMessage(
            text="go",
            channel_id="C1",
            user_id="alice",
            platform="cli",
            extra_system_prompt="extra content",
        )
        engine.handle(msg, _adapter())

        system = _system_from_call(llm)
        # Base prompt and extra content must be separated by the divider.
        assert "---" in system
        base_end = system.index("---")
        assert "extra content" in system[base_end:]

    def test_empty_extra_system_prompt_unchanged(self):
        engine, llm = _make_engine()

        msg_without = IncomingMessage(
            text="hello", channel_id="C1", user_id="alice", platform="cli"
        )
        engine.handle(msg_without, _adapter())
        system_without = _system_from_call(llm)

        msg_with_empty = IncomingMessage(
            text="hello",
            channel_id="C1",
            user_id="alice",
            platform="cli",
            extra_system_prompt="",
        )
        engine.handle(msg_with_empty, _adapter())
        system_with_empty = _system_from_call(llm)

        assert system_without == system_with_empty

    def test_extra_system_prompt_stripped(self):
        """Leading/trailing whitespace in extra_system_prompt is stripped."""
        engine, llm = _make_engine()
        msg = IncomingMessage(
            text="go",
            channel_id="C1",
            user_id="alice",
            platform="cli",
            extra_system_prompt="\n\n  ## Instructions\n  Do this.\n\n",
        )
        engine.handle(msg, _adapter())

        system = _system_from_call(llm)
        # Find the Task Instructions block (appended by engine, always last).
        task_marker = "## Task Instructions\n"
        idx = system.index(task_marker)
        tail = system[idx + len(task_marker):]
        # strip() was called on the input, so no leading newlines in the tail.
        assert tail.startswith("## Instructions")


class TestEngineMCPAllowlist:
    def test_agent_level_mcp_tool_routes_through_engine_allowlist(self):
        """Engine allowlist is derived from router indexes, including direct MCP tools."""
        llm = MagicMock()
        state = MemoryStateBackend()
        memory = FilesystemMemoryBackend(Path(tempfile.mkdtemp()))
        mcp_manager = MagicMock()
        mcp_manager.server_names.return_value = ["last9"]
        mcp_manager.discover_tools.return_value = [
            ToolDefinition(
                name="prometheus_labels",
                description="List labels",
                input_schema={"type": "object"},
            )
        ]
        mcp_manager.call_tool.return_value = '{"labels": ["job"]}'

        config = {
            "bot": {"system_prompt": "You are a helpful assistant."},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "test"},
            "mcp_servers": {"last9": {"transport": "http", "url": "https://example.test/mcp"}},
        }
        skill = Skill(
            name="local",
            prompt="local skill",
            tools=[],
            handle=lambda _name, _input, _ctx: "{}",
            source_dir=Path(tempfile.mkdtemp()),
        )

        with patch("mithai.core.mcp_manager.MCPManager", return_value=mcp_manager):
            engine = Engine(
                config=config,
                llm=llm,
                state=state,
                memory=memory,
                skills={"local": skill},
            )

        result = engine._router.route(
            "mcp__last9__prometheus_labels",
            {"lookback_minutes": 60},
            {},
        )

        assert result == '{"labels": ["job"]}'
        mcp_manager.start.assert_called_once_with()
        mcp_manager.call_tool.assert_called_once_with(
            "last9",
            "prometheus_labels",
            {"lookback_minutes": 60},
        )
        assert "mcp__last9__prometheus_labels" in engine._router._allowed_tools
