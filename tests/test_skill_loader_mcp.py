"""Tests for MCP_TOOLS loading and validation in skill_loader."""


from mithai.core.skill_loader import load_skills, validate_skill


def _make_skill_with_mcp(tmp_path, mcp_tools_code="MCP_TOOLS = []"):
    """Helper to create a skill directory with MCP_TOOLS."""
    skill_dir = tmp_path / "skills" / "mcp_skill"
    skill_dir.mkdir(parents=True)

    (skill_dir / "prompt.md").write_text("A skill with MCP tools.")
    (skill_dir / "tools.py").write_text(f'''
import json

TOOLS = [
    {{
        "name": "local_tool",
        "description": "A local tool.",
        "input_schema": {{"type": "object", "properties": {{}}}},
    }},
]

{mcp_tools_code}

def handle(name, input, ctx):
    return json.dumps({{"handled": name}})
''')

    return tmp_path / "skills"


def test_load_skill_with_mcp_tools(tmp_path):
    """Skills with MCP_TOOLS have them loaded."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {
        "server": "linear",
        "tools": ["searchIssues", "listIssues"],
        "human": "approve",
    },
]
''')

    skills = load_skills([skills_dir])
    skill = skills["mcp_skill"]

    assert len(skill.mcp_tools) == 1
    assert skill.mcp_tools[0]["server"] == "linear"
    assert skill.mcp_tools[0]["tools"] == ["searchIssues", "listIssues"]
    assert skill.mcp_tools[0]["human"] == "approve"


def test_load_skill_without_mcp_tools(tmp_skill_dir):
    """Skills without MCP_TOOLS default to empty list."""
    skills = load_skills([tmp_skill_dir])
    skill = skills["test_skill"]
    assert skill.mcp_tools == []


def test_load_skill_mcp_wildcard(tmp_path):
    """MCP_TOOLS with tools='*' is loaded correctly."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {"server": "github", "tools": "*"},
]
''')

    skills = load_skills([skills_dir])
    skill = skills["mcp_skill"]
    assert skill.mcp_tools[0]["tools"] == "*"


def test_load_skill_mcp_with_overrides(tmp_path):
    """MCP_TOOLS human_overrides are preserved."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {
        "server": "linear",
        "tools": ["searchIssues", "createIssue"],
        "human": "approve",
        "human_overrides": {"searchIssues": None},
    },
]
''')

    skills = load_skills([skills_dir])
    entry = skills["mcp_skill"].mcp_tools[0]
    assert entry["human_overrides"] == {"searchIssues": None}


def test_load_skill_mcp_non_list_ignored(tmp_path):
    """Non-list MCP_TOOLS is ignored (defaults to empty)."""
    skills_dir = _make_skill_with_mcp(tmp_path, 'MCP_TOOLS = "not a list"')

    skills = load_skills([skills_dir])
    skill = skills["mcp_skill"]
    assert skill.mcp_tools == []


# Validation tests

def test_validate_mcp_tools_valid(tmp_path):
    """Valid MCP_TOOLS passes validation."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {"server": "linear", "tools": ["searchIssues"], "human": "approve"},
]
''')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert errors == []


def test_validate_mcp_tools_missing_server(tmp_path):
    """MCP_TOOLS entry without 'server' fails validation."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {"tools": ["searchIssues"]},
]
''')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert any("server" in e for e in errors)


def test_validate_mcp_tools_missing_tools(tmp_path):
    """MCP_TOOLS entry without 'tools' fails validation."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {"server": "linear"},
]
''')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert any("tools" in e for e in errors)


def test_validate_mcp_tools_invalid_tools_type(tmp_path):
    """MCP_TOOLS with tools as non-list/non-wildcard fails."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {"server": "linear", "tools": 42},
]
''')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert any("tools" in e and "list" in e for e in errors)


def test_validate_mcp_tools_wildcard_is_valid(tmp_path):
    """MCP_TOOLS with tools='*' passes validation."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {"server": "github", "tools": "*"},
]
''')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert errors == []


def test_validate_mcp_tools_invalid_human(tmp_path):
    """MCP_TOOLS with invalid human level fails."""
    skills_dir = _make_skill_with_mcp(tmp_path, '''
MCP_TOOLS = [
    {"server": "linear", "tools": ["x"], "human": "invalid_level"},
]
''')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert any("human" in e.lower() for e in errors)


def test_validate_mcp_tools_not_a_list(tmp_path):
    """MCP_TOOLS as non-list fails validation."""
    skills_dir = _make_skill_with_mcp(tmp_path, 'MCP_TOOLS = "bad"')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert any("MCP_TOOLS must be a list" in e for e in errors)


def test_validate_mcp_tools_entry_not_dict(tmp_path):
    """MCP_TOOLS entry that isn't a dict fails validation."""
    skills_dir = _make_skill_with_mcp(tmp_path, 'MCP_TOOLS = ["not_a_dict"]')
    errors = validate_skill(skills_dir / "mcp_skill")
    assert any("must be a dict" in e for e in errors)
