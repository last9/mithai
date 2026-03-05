"""Tests for the github skill."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from mithai.core.skill_loader import load_skills, validate_skill


@pytest.fixture
def github_skills_dir():
    """Return the real skills directory containing the github skill."""
    return Path(__file__).parent.parent / "skills"


@pytest.fixture
def github_skill(github_skills_dir):
    """Load the github skill."""
    skills = load_skills([github_skills_dir])
    return skills["github"]


@pytest.fixture
def github_module():
    """Import the github tools module directly for unit testing."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "github_tools",
        Path(__file__).parent.parent / "skills" / "github" / "tools.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- Skill loading & validation ----

class TestSkillLoading:
    def test_github_skill_loads(self, github_skill):
        assert github_skill.name == "github"

    def test_has_15_tools(self, github_skill):
        assert len(github_skill.tools) == 15

    def test_prompt_mentions_gh(self, github_skill):
        assert "gh" in github_skill.prompt.lower()

    def test_validates_cleanly(self, github_skills_dir):
        errors = validate_skill(github_skills_dir / "github")
        assert errors == []

    def test_tool_names(self, github_skill):
        names = {t.name for t in github_skill.tools}
        expected = {
            "gh_list_prs", "gh_get_pr", "gh_create_pr", "gh_merge_pr",
            "gh_list_issues", "gh_get_issue", "gh_create_issue", "gh_add_comment",
            "gh_list_runs", "gh_get_run", "gh_rerun_failed",
            "gh_list_releases", "gh_create_release",
            "gh_repo_info", "gh_list_branches",
        }
        assert names == expected

    def test_read_only_tools_no_approval(self, github_skill):
        read_only = {
            "gh_list_prs", "gh_get_pr",
            "gh_list_issues", "gh_get_issue",
            "gh_list_runs", "gh_get_run",
            "gh_list_releases",
            "gh_repo_info", "gh_list_branches",
        }
        for tool in github_skill.tools:
            if tool.name in read_only:
                assert tool.human is None, f"{tool.name} should have no approval"

    def test_mutating_tools_require_approval(self, github_skill):
        mutating = {
            "gh_create_pr", "gh_merge_pr",
            "gh_create_issue", "gh_add_comment",
            "gh_rerun_failed",
            "gh_create_release",
        }
        for tool in github_skill.tools:
            if tool.name in mutating:
                assert tool.human == "approve", f"{tool.name} should require approval"

    def test_no_resolve_human(self, github_skill):
        assert github_skill.resolve_human is None


# ---- _gh helper ----

class TestGhHelper:
    def test_gh_success_json(self, github_module):
        mock_result = MagicMock(returncode=0, stdout='[{"id": 1}]', stderr="")
        with patch("subprocess.run", return_value=mock_result) as run:
            result = github_module._gh("pr", "list", "--repo", "a/b")
            assert result == [{"id": 1}]
            run.assert_called_once()
            # Verify stdin=DEVNULL
            call_kwargs = run.call_args[1]
            assert call_kwargs.get("stdin") is not None

    def test_gh_success_text(self, github_module):
        mock_result = MagicMock(returncode=0, stdout="merged PR #5", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = github_module._gh("pr", "merge", "5")
            assert result == {"output": "merged PR #5"}

    def test_gh_error(self, github_module):
        mock_result = MagicMock(returncode=1, stdout="", stderr="not found")
        with patch("subprocess.run", return_value=mock_result):
            result = github_module._gh("pr", "view", "999")
            assert result == {"error": "not found"}

    def test_gh_not_found(self, github_module):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = github_module._gh("pr", "list")
            assert "not found" in result["error"].lower()

    def test_gh_timeout(self, github_module):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            result = github_module._gh("pr", "list")
            assert "timed out" in result["error"]

    def test_gh_stdin_devnull(self, github_module):
        import subprocess
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        with patch("subprocess.run", return_value=mock_result) as run:
            github_module._gh("repo", "view")
            call_kwargs = run.call_args[1]
            assert call_kwargs["stdin"] == subprocess.DEVNULL


# ---- handle() for each tool ----

def _mock_gh(return_value):
    """Patch _gh to return a canned value."""
    return patch("github_tools._gh", return_value=return_value)


class TestHandlePullRequests:
    def test_list_prs(self, github_module):
        data = [{"number": 1, "title": "fix"}]
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_list_prs", {"repo": "a/b"}, {}))
            assert result == data
            args = mock.call_args[0]
            assert "pr" in args and "list" in args
            assert "--state" in args
            assert "open" in args  # default

    def test_list_prs_with_state(self, github_module):
        with patch.object(github_module, "_gh", return_value=[]) as mock:
            github_module.handle("gh_list_prs", {"repo": "a/b", "state": "closed", "limit": 5}, {})
            args = mock.call_args[0]
            assert "closed" in args
            assert "5" in args

    def test_get_pr(self, github_module):
        data = {"number": 42, "title": "my pr"}
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_get_pr", {"repo": "a/b", "number": 42}, {}))
            assert result == data
            assert "42" in mock.call_args[0]

    def test_create_pr(self, github_module):
        data = {"output": "https://github.com/a/b/pull/1"}
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle(
                "gh_create_pr",
                {"repo": "a/b", "title": "New feature", "head": "feat-branch", "body": "desc", "base": "main"},
                {},
            ))
            assert result == data
            args = mock.call_args[0]
            assert "--title" in args
            assert "New feature" in args
            assert "--head" in args
            assert "--body" in args
            assert "--base" in args

    def test_create_pr_minimal(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "ok"}) as mock:
            github_module.handle("gh_create_pr", {"repo": "a/b", "title": "t", "head": "h"}, {})
            args = mock.call_args[0]
            assert "--body" not in args
            assert "--base" not in args

    def test_merge_pr(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "merged"}) as mock:
            result = json.loads(github_module.handle("gh_merge_pr", {"repo": "a/b", "number": 7}, {}))
            assert result["output"] == "merged"
            args = mock.call_args[0]
            assert "7" in args
            assert "--merge" in args

    def test_merge_pr_squash(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "ok"}) as mock:
            github_module.handle("gh_merge_pr", {"repo": "a/b", "number": 7, "method": "squash"}, {})
            assert "--squash" in mock.call_args[0]


class TestHandleIssues:
    def test_list_issues(self, github_module):
        data = [{"number": 1}]
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_list_issues", {"repo": "a/b"}, {}))
            assert result == data
            args = mock.call_args[0]
            assert "issue" in args and "list" in args

    def test_list_issues_closed(self, github_module):
        with patch.object(github_module, "_gh", return_value=[]) as mock:
            github_module.handle("gh_list_issues", {"repo": "a/b", "state": "closed"}, {})
            assert "closed" in mock.call_args[0]

    def test_get_issue(self, github_module):
        data = {"number": 10, "title": "bug"}
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_get_issue", {"repo": "a/b", "number": 10}, {}))
            assert result == data

    def test_create_issue(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "created"}) as mock:
            result = json.loads(github_module.handle(
                "gh_create_issue",
                {"repo": "a/b", "title": "Bug report", "body": "details"},
                {},
            ))
            assert result["output"] == "created"
            args = mock.call_args[0]
            assert "--title" in args
            assert "--body" in args

    def test_create_issue_no_body(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "ok"}) as mock:
            github_module.handle("gh_create_issue", {"repo": "a/b", "title": "t"}, {})
            assert "--body" not in mock.call_args[0]

    def test_add_comment(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "commented"}) as mock:
            result = json.loads(github_module.handle(
                "gh_add_comment",
                {"repo": "a/b", "number": 5, "body": "LGTM"},
                {},
            ))
            assert result["output"] == "commented"
            args = mock.call_args[0]
            assert "comment" in args
            assert "5" in args


class TestHandleActions:
    def test_list_runs(self, github_module):
        data = [{"databaseId": 123}]
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_list_runs", {"repo": "a/b"}, {}))
            assert result == data
            args = mock.call_args[0]
            assert "run" in args and "list" in args

    def test_list_runs_limit(self, github_module):
        with patch.object(github_module, "_gh", return_value=[]) as mock:
            github_module.handle("gh_list_runs", {"repo": "a/b", "limit": 20}, {})
            assert "20" in mock.call_args[0]

    def test_get_run(self, github_module):
        data = {"databaseId": 456, "status": "completed"}
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_get_run", {"repo": "a/b", "run_id": "456"}, {}))
            assert result == data

    def test_rerun_failed(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "rerun triggered"}) as mock:
            result = json.loads(github_module.handle("gh_rerun_failed", {"repo": "a/b", "run_id": "789"}, {}))
            assert "rerun" in result["output"]
            args = mock.call_args[0]
            assert "--failed" in args


class TestHandleReleases:
    def test_list_releases(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "v1.0\nv0.9"}) as mock:
            result = json.loads(github_module.handle("gh_list_releases", {"repo": "a/b"}, {}))
            assert "v1.0" in result["output"]
            args = mock.call_args[0]
            assert "release" in args and "list" in args

    def test_list_releases_limit(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": ""}) as mock:
            github_module.handle("gh_list_releases", {"repo": "a/b", "limit": 3}, {})
            assert "3" in mock.call_args[0]

    def test_create_release(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "created v1.0"}) as mock:
            result = json.loads(github_module.handle(
                "gh_create_release",
                {"repo": "a/b", "tag": "v1.0", "title": "Release 1.0", "notes": "changelog"},
                {},
            ))
            assert "created" in result["output"]
            args = mock.call_args[0]
            assert "v1.0" in args
            assert "--title" in args
            assert "--notes" in args

    def test_create_release_minimal(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "ok"}) as mock:
            github_module.handle("gh_create_release", {"repo": "a/b", "tag": "v2.0"}, {})
            args = mock.call_args[0]
            assert "v2.0" in args
            assert "--title" not in args
            assert "--notes" not in args

    def test_create_release_draft_prerelease(self, github_module):
        with patch.object(github_module, "_gh", return_value={"output": "ok"}) as mock:
            github_module.handle(
                "gh_create_release",
                {"repo": "a/b", "tag": "v3.0-rc1", "draft": True, "prerelease": True},
                {},
            )
            args = mock.call_args[0]
            assert "--draft" in args
            assert "--prerelease" in args


class TestHandleRepository:
    def test_repo_info(self, github_module):
        data = {"name": "mithai", "stargazerCount": 42}
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_repo_info", {"repo": "a/b"}, {}))
            assert result == data
            args = mock.call_args[0]
            assert "repo" in args and "view" in args

    def test_list_branches(self, github_module):
        data = [{"name": "main"}, {"name": "dev"}]
        with patch.object(github_module, "_gh", return_value=data) as mock:
            result = json.loads(github_module.handle("gh_list_branches", {"repo": "a/b"}, {}))
            assert result == data
            args = mock.call_args[0]
            assert "api" in args
            assert "repos/a/b/branches" in args


class TestHandleUnknown:
    def test_unknown_tool(self, github_module):
        result = json.loads(github_module.handle("nonexistent", {}, {}))
        assert "error" in result
        assert "Unknown tool" in result["error"]
