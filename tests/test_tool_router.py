"""Tests for tool router."""

import json

from mithai.core.skill_loader import load_skills
from mithai.core.tool_router import ToolRouter


def test_collect_tools(tmp_skill_dir):
    skills = load_skills([tmp_skill_dir])
    router = ToolRouter(skills)
    tools = router.collect_tools_for_llm()

    names = [t["name"] for t in tools]
    assert "test_skill__echo" in names
    assert "test_skill__risky_action" in names


def test_parse_tool_name(tmp_skill_dir):
    skills = load_skills([tmp_skill_dir])
    router = ToolRouter(skills)

    skill_name, tool_name = router.parse("test_skill__echo")
    assert skill_name == "test_skill"
    assert tool_name == "echo"


def test_get_definition(tmp_skill_dir):
    skills = load_skills([tmp_skill_dir])
    router = ToolRouter(skills)

    tool_def = router.get_definition("test_skill__echo")
    assert tool_def is not None
    assert tool_def.name == "echo"
    assert tool_def.human is None

    tool_def = router.get_definition("test_skill__risky_action")
    assert tool_def.human == "approve"


def test_route_success(tmp_skill_dir):
    skills = load_skills([tmp_skill_dir])
    router = ToolRouter(skills)

    result = router.route("test_skill__echo", {"message": "hello"}, {})
    data = json.loads(result)
    assert data["echoed"] == "hello"


def test_route_unknown_tool(tmp_skill_dir):
    skills = load_skills([tmp_skill_dir])
    router = ToolRouter(skills)

    result = router.route("nonexistent__tool", {}, {})
    data = json.loads(result)
    assert "error" in data
