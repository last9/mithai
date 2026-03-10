"""Tests for the code_review skill."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from mithai.core.skill_loader import load_skills, validate_skill


@pytest.fixture
def skills_dir():
    return Path(__file__).parent.parent / "skills"


@pytest.fixture
def cr_skill(skills_dir):
    skills = load_skills([skills_dir])
    return skills["code_review"]


@pytest.fixture
def cr_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "code_review_tools",
        Path(__file__).parent.parent / "skills" / "code_review" / "tools.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- Skill loading & validation ----

class TestSkillLoading:
    def test_loads(self, cr_skill):
        assert cr_skill.name == "code_review"

    def test_has_6_tools(self, cr_skill):
        assert len(cr_skill.tools) == 6

    def test_validates_cleanly(self, skills_dir):
        errors = validate_skill(skills_dir / "code_review")
        assert errors == []

    def test_tool_names(self, cr_skill):
        names = {t.name for t in cr_skill.tools}
        expected = {
            "cr_get_pr_diff", "cr_get_pr_files", "cr_get_file_content",
            "cr_get_review_comments", "cr_submit_review", "cr_post_comment",
        }
        assert names == expected

    def test_read_only_tools_no_approval(self, cr_skill):
        read_only = {"cr_get_pr_diff", "cr_get_pr_files", "cr_get_file_content", "cr_get_review_comments"}
        for tool in cr_skill.tools:
            if tool.name in read_only:
                assert tool.human is None, f"{tool.name} should have no approval"

    def test_mutating_tools_require_approval(self, cr_skill):
        mutating = {"cr_submit_review", "cr_post_comment"}
        for tool in cr_skill.tools:
            if tool.name in mutating:
                assert tool.human == "approve", f"{tool.name} should require approval"

    def test_prompt_mentions_review(self, cr_skill):
        assert "review" in cr_skill.prompt.lower()

    def test_no_resolve_human(self, cr_skill):
        assert cr_skill.resolve_human is None


# ---- _gh helper ----

class TestGhHelper:
    def test_gh_success_json(self, cr_module):
        mock_result = MagicMock(returncode=0, stdout='{"files": []}', stderr="")
        with patch("subprocess.run", return_value=mock_result) as run:
            result = cr_module._gh("pr", "view", "1")
            assert result == {"files": []}
            assert run.call_args[1]["stdin"] is not None

    def test_gh_success_text(self, cr_module):
        mock_result = MagicMock(returncode=0, stdout="diff --git a/f.py", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            result = cr_module._gh("pr", "diff", "1")
            assert result == {"output": "diff --git a/f.py"}

    def test_gh_error(self, cr_module):
        mock_result = MagicMock(returncode=1, stdout="", stderr="not found")
        with patch("subprocess.run", return_value=mock_result):
            result = cr_module._gh("pr", "view", "999")
            assert result == {"error": "not found"}

    def test_gh_not_found(self, cr_module):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = cr_module._gh("pr", "list")
            assert "not found" in result["error"].lower()

    def test_gh_timeout(self, cr_module):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            result = cr_module._gh("pr", "list")
            assert "timed out" in result["error"]

    def test_gh_stdin_devnull(self, cr_module):
        import subprocess
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        with patch("subprocess.run", return_value=mock_result) as run:
            cr_module._gh("pr", "view")
            assert run.call_args[1]["stdin"] == subprocess.DEVNULL


# ---- handle() for each tool ----

class TestGetPrDiff:
    def test_returns_diff(self, cr_module):
        diff_text = "diff --git a/foo.py b/foo.py\n+new line"
        with patch.object(cr_module, "_gh", return_value={"output": diff_text}) as mock:
            result = json.loads(cr_module.handle("cr_get_pr_diff", {"repo": "a/b", "number": 1}, {}))
            assert result["output"] == diff_text
            args = mock.call_args[0]
            assert "pr" in args and "diff" in args
            assert "1" in args

    def test_passes_repo(self, cr_module):
        with patch.object(cr_module, "_gh", return_value={"output": ""}) as mock:
            cr_module.handle("cr_get_pr_diff", {"repo": "org/repo", "number": 42}, {})
            args = mock.call_args[0]
            assert "--repo" in args
            assert "org/repo" in args


class TestGetPrFiles:
    def test_returns_files(self, cr_module):
        data = {"files": [{"path": "foo.py", "additions": 10, "deletions": 2}]}
        with patch.object(cr_module, "_gh", return_value=data) as mock:
            result = json.loads(cr_module.handle("cr_get_pr_files", {"repo": "a/b", "number": 5}, {}))
            assert result == data
            args = mock.call_args[0]
            assert "pr" in args and "view" in args
            assert "--json" in args


class TestGetFileContent:
    def test_returns_decoded_content(self, cr_module):
        import base64
        content = "def hello():\n    print('hi')\n"
        encoded = base64.b64encode(content.encode()).decode()

        pr_info = {"headRefOid": "abc123"}
        file_resp = {"output": encoded}

        with patch.object(cr_module, "_gh", side_effect=[pr_info, file_resp]):
            result = json.loads(cr_module.handle(
                "cr_get_file_content",
                {"repo": "a/b", "number": 1, "path": "src/main.py"},
                {},
            ))
            assert result["path"] == "src/main.py"
            assert result["content"] == content

    def test_pr_info_error_propagates(self, cr_module):
        with patch.object(cr_module, "_gh", return_value={"error": "not found"}):
            result = json.loads(cr_module.handle(
                "cr_get_file_content",
                {"repo": "a/b", "number": 999, "path": "x.py"},
                {},
            ))
            assert "error" in result

    def test_missing_sha(self, cr_module):
        with patch.object(cr_module, "_gh", return_value={"headRefOid": ""}):
            result = json.loads(cr_module.handle(
                "cr_get_file_content",
                {"repo": "a/b", "number": 1, "path": "x.py"},
                {},
            ))
            assert "error" in result
            assert "head commit" in result["error"]

    def test_file_fetch_error(self, cr_module):
        pr_info = {"headRefOid": "abc123"}
        with patch.object(cr_module, "_gh", side_effect=[pr_info, {"error": "404"}]):
            result = json.loads(cr_module.handle(
                "cr_get_file_content",
                {"repo": "a/b", "number": 1, "path": "gone.py"},
                {},
            ))
            assert result == {"error": "404"}


class TestGetReviewComments:
    def test_returns_comments(self, cr_module):
        data = [{"path": "f.py", "line": 10, "body": "bug here", "user": "alice"}]
        with patch.object(cr_module, "_gh", return_value=data) as mock:
            result = json.loads(cr_module.handle(
                "cr_get_review_comments", {"repo": "a/b", "number": 3}, {},
            ))
            assert result == data
            args = mock.call_args[0]
            assert "api" in args
            assert "pulls/3/comments" in args[1]


class TestSubmitReview:
    def test_submit_with_comments(self, cr_module):
        response = {"id": 1, "state": "CHANGES_REQUESTED"}
        mock_proc = MagicMock(returncode=0, stdout=json.dumps(response), stderr="")

        with patch("subprocess.run", return_value=mock_proc) as run:
            result = json.loads(cr_module.handle(
                "cr_submit_review",
                {
                    "repo": "a/b",
                    "number": 7,
                    "event": "REQUEST_CHANGES",
                    "body": "Found issues",
                    "comments": [
                        {"path": "foo.py", "line": 42, "body": "SQL injection risk"},
                    ],
                },
                {},
            ))
            assert result["state"] == "CHANGES_REQUESTED"
            # Verify the payload sent via stdin
            call_kwargs = run.call_args[1]
            payload = json.loads(call_kwargs["input"])
            assert payload["event"] == "REQUEST_CHANGES"
            assert len(payload["comments"]) == 1
            assert payload["comments"][0]["line"] == 42

    def test_submit_approve_no_comments(self, cr_module):
        mock_proc = MagicMock(returncode=0, stdout='{"id": 2, "state": "APPROVED"}', stderr="")
        with patch("subprocess.run", return_value=mock_proc) as run:
            result = json.loads(cr_module.handle(
                "cr_submit_review",
                {"repo": "a/b", "number": 5, "event": "APPROVE", "body": "LGTM"},
                {},
            ))
            assert result["state"] == "APPROVED"
            payload = json.loads(run.call_args[1]["input"])
            assert "comments" not in payload

    def test_submit_error(self, cr_module):
        mock_proc = MagicMock(returncode=1, stdout="", stderr="Validation failed")
        with patch("subprocess.run", return_value=mock_proc):
            result = json.loads(cr_module.handle(
                "cr_submit_review",
                {"repo": "a/b", "number": 1, "event": "COMMENT", "body": "note"},
                {},
            ))
            assert result["error"] == "Validation failed"

    def test_submit_gh_not_found(self, cr_module):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = json.loads(cr_module.handle(
                "cr_submit_review",
                {"repo": "a/b", "number": 1, "event": "COMMENT", "body": "x"},
                {},
            ))
            assert "not found" in result["error"].lower()

    def test_submit_timeout(self, cr_module):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            result = json.loads(cr_module.handle(
                "cr_submit_review",
                {"repo": "a/b", "number": 1, "event": "COMMENT", "body": "x"},
                {},
            ))
            assert "timed out" in result["error"]


class TestPostComment:
    def test_post_single_comment(self, cr_module):
        pr_info = {"headRefOid": "sha123"}
        comment_resp = MagicMock(returncode=0, stdout='{"id": 99}', stderr="")

        with patch.object(cr_module, "_gh", return_value=pr_info):
            with patch("subprocess.run", return_value=comment_resp) as run:
                result = json.loads(cr_module.handle(
                    "cr_post_comment",
                    {"repo": "a/b", "number": 3, "path": "bar.py", "line": 15, "body": "Fix this"},
                    {},
                ))
                assert result["id"] == 99
                payload = json.loads(run.call_args[1]["input"])
                assert payload["path"] == "bar.py"
                assert payload["line"] == 15
                assert payload["commit_id"] == "sha123"
                assert payload["side"] == "RIGHT"

    def test_post_comment_pr_error(self, cr_module):
        with patch.object(cr_module, "_gh", return_value={"error": "not found"}):
            result = json.loads(cr_module.handle(
                "cr_post_comment",
                {"repo": "a/b", "number": 999, "path": "x.py", "line": 1, "body": "oops"},
                {},
            ))
            assert "error" in result

    def test_post_comment_missing_sha(self, cr_module):
        with patch.object(cr_module, "_gh", return_value={"headRefOid": ""}):
            result = json.loads(cr_module.handle(
                "cr_post_comment",
                {"repo": "a/b", "number": 1, "path": "x.py", "line": 1, "body": "oops"},
                {},
            ))
            assert "head commit" in result["error"]

    def test_post_comment_api_error(self, cr_module):
        pr_info = {"headRefOid": "sha456"}
        err_proc = MagicMock(returncode=1, stdout="", stderr="422 Unprocessable")

        with patch.object(cr_module, "_gh", return_value=pr_info):
            with patch("subprocess.run", return_value=err_proc):
                result = json.loads(cr_module.handle(
                    "cr_post_comment",
                    {"repo": "a/b", "number": 1, "path": "x.py", "line": 1, "body": "bad"},
                    {},
                ))
                assert result["error"] == "422 Unprocessable"

    def test_post_comment_gh_not_found(self, cr_module):
        pr_info = {"headRefOid": "sha789"}
        with patch.object(cr_module, "_gh", return_value=pr_info):
            with patch("subprocess.run", side_effect=FileNotFoundError):
                result = json.loads(cr_module.handle(
                    "cr_post_comment",
                    {"repo": "a/b", "number": 1, "path": "x.py", "line": 1, "body": "x"},
                    {},
                ))
                assert "not found" in result["error"].lower()

    def test_post_comment_timeout(self, cr_module):
        import subprocess
        pr_info = {"headRefOid": "sha000"}
        with patch.object(cr_module, "_gh", return_value=pr_info):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
                result = json.loads(cr_module.handle(
                    "cr_post_comment",
                    {"repo": "a/b", "number": 1, "path": "x.py", "line": 1, "body": "x"},
                    {},
                ))
                assert "timed out" in result["error"]


class TestUnknown:
    def test_unknown_tool(self, cr_module):
        result = json.loads(cr_module.handle("nonexistent", {}, {}))
        assert "Unknown tool" in result["error"]
