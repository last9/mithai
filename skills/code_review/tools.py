"""Skill: Code review for GitHub pull requests via gh CLI."""

import json
import subprocess


TOOLS = [
    {
        "name": "cr_get_pr_diff",
        "description": "Get the unified diff of a pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "number": {"type": "integer", "description": "PR number"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "name": "cr_get_pr_files",
        "description": "List files changed in a pull request with addition/deletion counts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "number": {"type": "integer", "description": "PR number"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "name": "cr_get_file_content",
        "description": "Get the full content of a file from the PR's head branch (for additional context beyond the diff).",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "number": {"type": "integer", "description": "PR number"},
                "path": {"type": "string", "description": "File path relative to repo root"},
            },
            "required": ["repo", "number", "path"],
        },
    },
    {
        "name": "cr_get_review_comments",
        "description": "Get existing review comments on a pull request (to avoid posting duplicates).",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "number": {"type": "integer", "description": "PR number"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "name": "cr_submit_review",
        "description": "Submit a pull request review with an overall verdict and optional inline comments on specific lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "number": {"type": "integer", "description": "PR number"},
                "event": {
                    "type": "string",
                    "description": "Review verdict: APPROVE, REQUEST_CHANGES, or COMMENT",
                    "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                },
                "body": {
                    "type": "string",
                    "description": "Overall review summary",
                },
                "comments": {
                    "type": "array",
                    "description": "Inline comments on specific files/lines",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path"},
                            "line": {"type": "integer", "description": "Line number in the diff (new file line)"},
                            "body": {"type": "string", "description": "Comment text"},
                        },
                        "required": ["path", "line", "body"],
                    },
                },
            },
            "required": ["repo", "number", "event", "body"],
        },
        "human": "approve",
    },
    {
        "name": "cr_post_comment",
        "description": "Post a single inline comment on a specific file and line in a pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "number": {"type": "integer", "description": "PR number"},
                "path": {"type": "string", "description": "File path"},
                "line": {"type": "integer", "description": "Line number in the new file"},
                "body": {"type": "string", "description": "Comment text"},
            },
            "required": ["repo", "number", "path", "line", "body"],
        },
        "human": "approve",
    },
]


def _gh(*args, timeout=30) -> dict:
    """Run a gh CLI command and return structured result."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"output": result.stdout.strip()}
    except FileNotFoundError:
        return {"error": "gh CLI not found. Install from https://cli.github.com/"}
    except subprocess.TimeoutExpired:
        return {"error": f"gh CLI timed out after {timeout}s"}


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "cr_get_pr_diff":
        repo = input["repo"]
        number = str(input["number"])
        result = _gh(
            "pr", "diff", number,
            "--repo", repo,
        )
        return json.dumps(result)

    if name == "cr_get_pr_files":
        repo = input["repo"]
        number = str(input["number"])
        result = _gh(
            "pr", "view", number,
            "--repo", repo,
            "--json", "files",
        )
        return json.dumps(result)

    if name == "cr_get_file_content":
        repo = input["repo"]
        number = str(input["number"])
        path = input["path"]
        # Get the head ref of the PR, then fetch file content at that ref
        pr_info = _gh(
            "pr", "view", number,
            "--repo", repo,
            "--json", "headRefOid",
        )
        if "error" in pr_info:
            return json.dumps(pr_info)
        sha = pr_info.get("headRefOid", "")
        if not sha:
            return json.dumps({"error": "Could not determine PR head commit"})
        result = _gh(
            "api", f"repos/{repo}/contents/{path}",
            "--method", "GET",
            "-f", f"ref={sha}",
            "--jq", ".content",
        )
        if "error" in result:
            return json.dumps(result)
        # GitHub returns base64-encoded content
        import base64
        raw = result.get("output", "")
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
            return json.dumps({"path": path, "content": decoded})
        except Exception:
            return json.dumps({"path": path, "content": raw})

    if name == "cr_get_review_comments":
        repo = input["repo"]
        number = str(input["number"])
        result = _gh(
            "api", f"repos/{repo}/pulls/{number}/comments",
            "--paginate",
            "--jq", "[.[] | {path: .path, line: .line, body: .body, user: .user.login, created_at: .created_at}]",
        )
        return json.dumps(result)

    if name == "cr_submit_review":
        repo = input["repo"]
        number = str(input["number"])
        event = input["event"]
        body = input["body"]
        comments = input.get("comments", [])

        # Build the review payload for the GitHub API
        payload = {
            "event": event,
            "body": body,
        }
        if comments:
            payload["comments"] = [
                {"path": c["path"], "line": c["line"], "body": c["body"]}
                for c in comments
            ]

        # Use gh api to submit the review
        args = [
            "api", f"repos/{repo}/pulls/{number}/reviews",
            "--method", "POST",
            "--input", "-",
        ]
        try:
            proc = subprocess.run(
                ["gh", *args],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return json.dumps({"error": proc.stderr.strip()})
            try:
                return json.dumps(json.loads(proc.stdout))
            except json.JSONDecodeError:
                return json.dumps({"output": proc.stdout.strip()})
        except FileNotFoundError:
            return json.dumps({"error": "gh CLI not found. Install from https://cli.github.com/"})
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "gh CLI timed out after 30s"})

    if name == "cr_post_comment":
        repo = input["repo"]
        number = str(input["number"])
        path = input["path"]
        line = input["line"]
        body = input["body"]

        # First get the latest commit SHA for the PR
        pr_info = _gh(
            "pr", "view", number,
            "--repo", repo,
            "--json", "headRefOid",
        )
        if "error" in pr_info:
            return json.dumps(pr_info)
        commit_id = pr_info.get("headRefOid", "")
        if not commit_id:
            return json.dumps({"error": "Could not determine PR head commit"})

        payload = {
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": "RIGHT",
        }
        try:
            proc = subprocess.run(
                ["gh", "api", f"repos/{repo}/pulls/{number}/comments",
                 "--method", "POST", "--input", "-"],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                return json.dumps({"error": proc.stderr.strip()})
            try:
                return json.dumps(json.loads(proc.stdout))
            except json.JSONDecodeError:
                return json.dumps({"output": proc.stdout.strip()})
        except FileNotFoundError:
            return json.dumps({"error": "gh CLI not found. Install from https://cli.github.com/"})
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "gh CLI timed out after 30s"})

    return json.dumps({"error": f"Unknown tool: {name}"})
