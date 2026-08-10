"""Tests for per-turn log tool budgets."""

import json
from unittest.mock import MagicMock

import pytest

from mithai.core.engine import Engine
from mithai.core.skill_loader import load_skills
from mithai.core.tool_budgets import (
    budget_exhausted_result,
    get_tool_budget_config,
    is_budgeted_log_tool,
    is_long_range_log_query,
)
from mithai.llm.base import LLMResponse
from mithai.state.memory import MemoryStateBackend


def test_get_tool_budget_config_defaults():
    cfg = get_tool_budget_config({})
    assert cfg["enabled"] is True
    assert cfg["log_tools"]["max_per_turn"] == 5
    assert "get_logs" in cfg["log_tools"]["tools"]


def test_is_budgeted_log_tool_matches_prefixed_names():
    names = frozenset({"get_logs", "get_service_logs"})
    assert is_budgeted_log_tool("mcp__last9__get_logs", names)
    assert is_budgeted_log_tool("kubernetes__get_logs", names)


def test_is_long_range_log_query_lookback():
    assert is_long_range_log_query({"lookback_minutes": 180}, 60)
    assert not is_long_range_log_query({"lookback_minutes": 30}, 60)


def test_is_long_range_log_query_service_default():
    assert is_long_range_log_query({"service_name": "issuing-oltp"}, 60)


def test_budget_exhausted_result_shape():
    payload = json.loads(budget_exhausted_result("log_tool_turn_limit", limit=5))
    assert payload["budget_exhausted"] is True
    assert payload["reason"] == "log_tool_turn_limit"
    assert payload["limit"] == 5


@pytest.fixture
def tmp_skill_dir(tmp_path):
    skill_dir = tmp_path / "test_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test_skill\ndescription: test\n---\nTest skill.\n"
    )
    (skill_dir / "tools.py").write_text(
        "from mithai.core.skill_loader import tool\n"
        "@tool\ndef echo(message: str) -> str:\n"
        '    """Echo."""\n'
        "    return message\n"
    )
    return skill_dir


def _make_engine(tmp_skill_dir, llm, extra_config=None):
    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic", "model": "claude-test", "anthropic": {"api_key": "x"}},
        "skills": {"paths": [str(tmp_skill_dir)]},
        "tool_budgets": {
            "log_tools": {
                "max_per_turn": 2,
                "tools": ["get_logs", "get_service_logs"],
            }
        },
    }
    if extra_config:
        config.update(extra_config)
    skills = load_skills([tmp_skill_dir])
    return Engine(config, llm, MemoryStateBackend(), skills=skills)


def _tool_use(tool_name, tool_input, tool_id="tu_1"):
    return LLMResponse(
        content=[{
            "type": "tool_use",
            "id": tool_id,
            "name": tool_name,
            "input": tool_input,
        }],
        stop_reason="tool_use",
        model="claude-test",
        usage={"input_tokens": 10, "output_tokens": 5},
    )



def _end_turn(text="done"):
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        model="claude-test",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def test_check_log_tool_budget_blocks_turn_limit(tmp_skill_dir):
    engine = _make_engine(tmp_skill_dir, MagicMock())
    turn_tool_calls = [
        {"tool": "mcp__last9__get_logs", "input": {"lookback_minutes": 5}},
        {"tool": "mcp__last9__get_logs", "input": {"lookback_minutes": 5}},
    ]

    result = engine._check_log_tool_budget(
        "mcp__last9__get_logs",
        {"lookback_minutes": 5},
        turn_tool_calls,
    )

    payload = json.loads(result)
    assert payload["budget_exhausted"] is True
    assert payload["reason"] == "log_tool_turn_limit"


def test_check_log_tool_budget_blocks_long_range_limit(tmp_skill_dir):
    engine = _make_engine(tmp_skill_dir, MagicMock())
    turn_tool_calls = [
        {
            "tool": "mcp__last9__get_service_logs",
            "input": {"service_name": "issuing-oltp", "lookback_minutes": 180},
        },
    ]

    result = engine._check_log_tool_budget(
        "mcp__last9__get_service_logs",
        {"service_name": "iss-dapi", "lookback_minutes": 180},
        turn_tool_calls,
    )

    payload = json.loads(result)
    assert payload["budget_exhausted"] is True
    assert payload["reason"] == "long_range_log_turn_limit"
