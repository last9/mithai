"""Tests for the exception_fixer skill."""

import json
from pathlib import Path

from mithai.core.skill_loader import _load_skill

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills" / "exception_fixer"


def test_skill_loads():
    """The skill loads without error."""
    skill = _load_skill(SKILL_DIR)
    assert skill is not None
    assert skill.name == "exception_fixer"
    assert len(skill.tools) == 1
    assert skill.tools[0].name == "format_pr_body"
    assert len(skill.mcp_tools) == 2


def test_last9_mcp_entry():
    """Last9 tools are read-only, auto-execute."""
    skill = _load_skill(SKILL_DIR)
    last9 = skill.mcp_tools[0]
    assert last9["server"] == "last9"
    assert last9["human"] is None
    assert "get_exceptions" in last9["tools"]
    assert "get_service_traces" in last9["tools"]


def test_github_mcp_entry():
    """GitHub read tools auto-execute, write tools require approval."""
    skill = _load_skill(SKILL_DIR)
    github = skill.mcp_tools[1]
    assert github["server"] == "github"
    assert github["human"] is None

    overrides = github["human_overrides"]
    assert overrides["create_branch"] == "approve"
    assert overrides["push_files"] == "approve"
    assert overrides["create_or_update_file"] == "approve"
    assert overrides["create_pull_request"] == "approve"

    assert "get_file_contents" not in overrides
    assert "search_code" not in overrides
    assert "get_pull_request_status" not in overrides


def test_prompt_contains_workflow():
    """Prompt has key workflow steps."""
    skill = _load_skill(SKILL_DIR)
    prompt = skill.prompt.lower()
    assert "exception" in prompt
    assert "stack trace" in prompt
    assert "claude.md" in prompt
    assert "format_pr_body" in prompt


def test_format_pr_body():
    """format_pr_body produces correct markdown with Last9 link."""
    skill = _load_skill(SKILL_DIR)
    result = skill.handle("format_pr_body", {
        "exception_type": "NullPointerException",
        "service_name": "checkout",
        "exception_message": "null reference on line 42",
        "frequency": "15 occurrences in last 6 hours",
        "root_cause": "Missing nil check after DB query.",
        "fix_description": "Added nil check before accessing result.",
        "files_changed": [
            {"path": "api/checkout.go", "summary": "Added nil check after QueryRow"},
        ],
    }, {})

    assert "## Exception Fix: `NullPointerException`" in result
    assert "**Service:** `checkout`" in result
    assert "**Frequency:** 15 occurrences in last 6 hours" in result
    assert "[View in Last9](https://observability.example.com/exceptions?service=checkout)" in result
    assert "Missing nil check" in result
    assert "- `api/checkout.go` — Added nil check after QueryRow" in result


def test_format_pr_body_url_encodes_service():
    """Service names with special chars are URL-encoded in the Last9 link."""
    skill = _load_skill(SKILL_DIR)
    result = skill.handle("format_pr_body", {
        "exception_type": "Error",
        "service_name": "my service/v2",
        "exception_message": "fail",
        "frequency": "1",
        "root_cause": "bug",
        "fix_description": "fixed",
        "files_changed": [{"path": "a.py", "summary": "fix"}],
    }, {})

    assert "service=my%20service/v2" in result


def test_format_pr_body_multiple_files():
    """All changed files appear in the output."""
    skill = _load_skill(SKILL_DIR)
    result = skill.handle("format_pr_body", {
        "exception_type": "IndexError",
        "service_name": "api",
        "exception_message": "list index out of range",
        "frequency": "8 in last 2 hours",
        "root_cause": "Off-by-one in pagination.",
        "fix_description": "Fixed loop bounds.",
        "files_changed": [
            {"path": "api/paginate.py", "summary": "Fixed loop upper bound"},
            {"path": "tests/test_paginate.py", "summary": "Added edge case test"},
            {"path": "api/utils.py", "summary": "Added bounds helper"},
        ],
    }, {})

    assert "- `api/paginate.py`" in result
    assert "- `tests/test_paginate.py`" in result
    assert "- `api/utils.py`" in result
    assert "### Root Cause" in result
    assert "### Fix" in result


def test_handle_unknown_tool():
    """Unknown tool names return an error."""
    skill = _load_skill(SKILL_DIR)
    result = skill.handle("nonexistent_tool", {}, {})
    data = json.loads(result)
    assert "error" in data
