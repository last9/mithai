"""Tests for post-turn reflection."""

from datetime import date
from unittest.mock import MagicMock

import pytest

from mithai.core.reflection import reflect


def _make_llm_response(text):
    """Create a mock LLM response with text content."""
    mock = MagicMock()
    mock.content = [{"type": "text", "text": text}]
    return mock


@pytest.fixture
def memory_dir(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    return mem


@pytest.fixture
def mock_llm():
    return MagicMock()


class TestReflect:
    def test_skips_turns_without_tool_calls(self, mock_llm, memory_dir):
        turn = {"user_message": "hello", "tool_calls": [], "assistant_response": "hi"}
        reflect(turn, mock_llm, memory_dir)
        mock_llm.create_message.assert_not_called()

    def test_writes_learnings_to_daily_log(self, mock_llm, memory_dir):
        mock_llm.create_message.return_value = _make_llm_response(
            "- DaemonSets use `rollout restart`, not delete/recreate"
        )
        turn = {
            "user_message": "restart the daemonset",
            "tool_calls": [{"tool": "shell__run_command", "input": {"command": "kubectl rollout restart ds/foo"}}],
            "assistant_response": "Restarted",
            "timestamp": "12:00",
        }

        reflect(turn, mock_llm, memory_dir)

        daily = memory_dir / "daily" / f"{date.today()}.md"
        assert daily.exists()
        content = daily.read_text()
        assert "DaemonSets" in content
        assert "12:00" in content

    def test_skips_when_llm_returns_none(self, mock_llm, memory_dir):
        mock_llm.create_message.return_value = _make_llm_response("none")
        turn = {
            "user_message": "check status",
            "tool_calls": [{"tool": "shell__run_command", "input": {"command": "uptime"}}],
            "assistant_response": "System is up",
        }

        reflect(turn, mock_llm, memory_dir)

        daily_dir = memory_dir / "daily"
        assert not daily_dir.exists() or not list(daily_dir.iterdir())

    def test_appends_multiple_reflections(self, mock_llm, memory_dir):
        mock_llm.create_message.return_value = _make_llm_response("- learning 1")
        turn1 = {
            "user_message": "q1",
            "tool_calls": [{"tool": "t", "input": {}}],
            "assistant_response": "a1",
            "timestamp": "10:00",
        }
        reflect(turn1, mock_llm, memory_dir)

        mock_llm.create_message.return_value = _make_llm_response("- learning 2")
        turn2 = {
            "user_message": "q2",
            "tool_calls": [{"tool": "t", "input": {}}],
            "assistant_response": "a2",
            "timestamp": "11:00",
        }
        reflect(turn2, mock_llm, memory_dir)

        daily = memory_dir / "daily" / f"{date.today()}.md"
        content = daily.read_text()
        assert "learning 1" in content
        assert "learning 2" in content

    def test_handles_llm_error_gracefully(self, mock_llm, memory_dir):
        mock_llm.create_message.side_effect = Exception("API error")
        turn = {
            "user_message": "test",
            "tool_calls": [{"tool": "t", "input": {}}],
            "assistant_response": "resp",
        }

        # Should not raise
        reflect(turn, mock_llm, memory_dir)

    def test_skips_empty_response(self, mock_llm, memory_dir):
        mock_llm.create_message.return_value = _make_llm_response("")
        turn = {
            "user_message": "test",
            "tool_calls": [{"tool": "t", "input": {}}],
            "assistant_response": "resp",
        }

        reflect(turn, mock_llm, memory_dir)

        daily_dir = memory_dir / "daily"
        assert not daily_dir.exists() or not list(daily_dir.iterdir())
