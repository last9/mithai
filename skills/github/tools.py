"""Skill: GitHub operations via gh CLI."""

import json
import subprocess


TOOLS = [
    # --- Pull Requests ---
    {
        "name": "gh_list_prs",
        "description": "List pull requests for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "state": {
                    "type": "string",
                    "description": "Filter by state: open, closed, merged, all (default: open)",
                    "default": "open",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of PRs to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "gh_get_pr",
        "description": "Get details of a specific pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "number": {"type": "integer", "description": "PR number"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "name": "gh_create_pr",
        "description": "Create a new pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "title": {"type": "string", "description": "PR title"},
                "body": {"type": "string", "description": "PR description"},
                "base": {"type": "string", "description": "Base branch (default: repo default branch)"},
                "head": {"type": "string", "description": "Head branch containing changes"},
            },
            "required": ["repo", "title", "head"],
        },
        "human": "approve",
    },
    {
        "name": "gh_merge_pr",
        "description": "Merge a pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "number": {"type": "integer", "description": "PR number to merge"},
                "method": {
                    "type": "string",
                    "description": "Merge method: merge, squash, rebase (default: merge)",
                    "default": "merge",
                },
            },
            "required": ["repo", "number"],
        },
        "human": "approve",
    },
    # --- Issues ---
    {
        "name": "gh_list_issues",
        "description": "List issues for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "state": {
                    "type": "string",
                    "description": "Filter by state: open, closed, all (default: open)",
                    "default": "open",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of issues to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "gh_get_issue",
        "description": "Get details of a specific issue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "number": {"type": "integer", "description": "Issue number"},
            },
            "required": ["repo", "number"],
        },
    },
    {
        "name": "gh_create_issue",
        "description": "Create a new issue.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body/description"},
            },
            "required": ["repo", "title"],
        },
        "human": "approve",
    },
    {
        "name": "gh_add_comment",
        "description": "Add a comment to an issue or pull request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "number": {"type": "integer", "description": "Issue or PR number"},
                "body": {"type": "string", "description": "Comment text"},
            },
            "required": ["repo", "number", "body"],
        },
        "human": "approve",
    },
    # --- Actions ---
    {
        "name": "gh_list_runs",
        "description": "List recent GitHub Actions workflow runs for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of runs to show (default: 5)",
                    "default": 5,
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "gh_get_run",
        "description": "Get details of a specific workflow run including job status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "run_id": {"type": "string", "description": "Workflow run ID"},
            },
            "required": ["repo", "run_id"],
        },
    },
    {
        "name": "gh_rerun_failed",
        "description": "Re-run failed jobs in a workflow run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "run_id": {"type": "string", "description": "Workflow run ID to re-run"},
            },
            "required": ["repo", "run_id"],
        },
        "human": "approve",
    },
    # --- Releases ---
    {
        "name": "gh_list_releases",
        "description": "List releases for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of releases to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "gh_create_release",
        "description": "Create a new release with a tag.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository (owner/name)"},
                "tag": {"type": "string", "description": "Tag name for the release (e.g., v1.2.3)"},
                "title": {"type": "string", "description": "Release title (defaults to tag name)"},
                "notes": {"type": "string", "description": "Release notes body"},
                "draft": {
                    "type": "boolean",
                    "description": "Create as draft release (default: false)",
                    "default": False,
                },
                "prerelease": {
                    "type": "boolean",
                    "description": "Mark as prerelease (default: false)",
                    "default": False,
                },
            },
            "required": ["repo", "tag"],
        },
        "human": "approve",
    },
    # --- Repository ---
    {
        "name": "gh_repo_info",
        "description": "Get repository metadata (description, stars, forks, default branch, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "gh_list_branches",
        "description": "List branches for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format",
                },
            },
            "required": ["repo"],
        },
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
    # --- Pull Requests ---
    if name == "gh_list_prs":
        repo = input["repo"]
        state = input.get("state", "open")
        limit = str(input.get("limit", 10))
        result = _gh(
            "pr", "list",
            "--repo", repo,
            "--state", state,
            "--limit", limit,
            "--json", "number,title,state,author,headRefName,baseRefName,createdAt,updatedAt",
        )
        return json.dumps(result)

    if name == "gh_get_pr":
        repo = input["repo"]
        number = str(input["number"])
        result = _gh(
            "pr", "view", number,
            "--repo", repo,
            "--json", "number,title,state,body,author,headRefName,baseRefName,mergeable,reviewDecision,commits,files,createdAt,updatedAt",
        )
        return json.dumps(result)

    if name == "gh_create_pr":
        repo = input["repo"]
        args = [
            "pr", "create",
            "--repo", repo,
            "--title", input["title"],
            "--head", input["head"],
        ]
        if input.get("body"):
            args += ["--body", input["body"]]
        if input.get("base"):
            args += ["--base", input["base"]]
        result = _gh(*args)
        return json.dumps(result)

    if name == "gh_merge_pr":
        repo = input["repo"]
        number = str(input["number"])
        method = input.get("method", "merge")
        result = _gh(
            "pr", "merge", number,
            "--repo", repo,
            f"--{method}",
        )
        return json.dumps(result)

    # --- Issues ---
    if name == "gh_list_issues":
        repo = input["repo"]
        state = input.get("state", "open")
        limit = str(input.get("limit", 10))
        result = _gh(
            "issue", "list",
            "--repo", repo,
            "--state", state,
            "--limit", limit,
            "--json", "number,title,state,author,labels,createdAt,updatedAt",
        )
        return json.dumps(result)

    if name == "gh_get_issue":
        repo = input["repo"]
        number = str(input["number"])
        result = _gh(
            "issue", "view", number,
            "--repo", repo,
            "--json", "number,title,state,body,author,labels,comments,createdAt,updatedAt",
        )
        return json.dumps(result)

    if name == "gh_create_issue":
        repo = input["repo"]
        args = [
            "issue", "create",
            "--repo", repo,
            "--title", input["title"],
        ]
        if input.get("body"):
            args += ["--body", input["body"]]
        result = _gh(*args)
        return json.dumps(result)

    if name == "gh_add_comment":
        repo = input["repo"]
        number = str(input["number"])
        result = _gh(
            "issue", "comment", number,
            "--repo", repo,
            "--body", input["body"],
        )
        return json.dumps(result)

    # --- Actions ---
    if name == "gh_list_runs":
        repo = input["repo"]
        limit = str(input.get("limit", 5))
        result = _gh(
            "run", "list",
            "--repo", repo,
            "--limit", limit,
            "--json", "databaseId,displayTitle,status,conclusion,headBranch,createdAt",
        )
        return json.dumps(result)

    if name == "gh_get_run":
        repo = input["repo"]
        run_id = input["run_id"]
        result = _gh(
            "run", "view", run_id,
            "--repo", repo,
            "--json", "databaseId,displayTitle,status,conclusion,jobs,headBranch,createdAt,updatedAt",
        )
        return json.dumps(result)

    if name == "gh_rerun_failed":
        repo = input["repo"]
        run_id = input["run_id"]
        result = _gh(
            "run", "rerun", run_id,
            "--repo", repo,
            "--failed",
        )
        return json.dumps(result)

    # --- Releases ---
    if name == "gh_list_releases":
        repo = input["repo"]
        limit = str(input.get("limit", 10))
        result = _gh(
            "release", "list",
            "--repo", repo,
            "--limit", limit,
        )
        return json.dumps(result)

    if name == "gh_create_release":
        repo = input["repo"]
        tag = input["tag"]
        args = [
            "release", "create", tag,
            "--repo", repo,
        ]
        if input.get("title"):
            args += ["--title", input["title"]]
        if input.get("notes"):
            args += ["--notes", input["notes"]]
        if input.get("draft"):
            args += ["--draft"]
        if input.get("prerelease"):
            args += ["--prerelease"]
        result = _gh(*args)
        return json.dumps(result)

    # --- Repository ---
    if name == "gh_repo_info":
        repo = input["repo"]
        result = _gh(
            "repo", "view", repo,
            "--json", "name,owner,description,defaultBranchRef,stargazerCount,forkCount,isPrivate,url,createdAt,updatedAt",
        )
        return json.dumps(result)

    if name == "gh_list_branches":
        repo = input["repo"]
        # gh api is more reliable for branch listing
        result = _gh(
            "api", f"repos/{repo}/branches",
            "--paginate",
        )
        return json.dumps(result)

    return json.dumps({"error": f"Unknown tool: {name}"})
