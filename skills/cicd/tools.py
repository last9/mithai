"""Skill: CI/CD status via GitHub CLI (gh)."""

import json
import subprocess


TOOLS = [
    {
        "name": "list_runs",
        "description": "List recent GitHub Actions workflow runs for a repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "Repository in owner/name format (e.g., nishantmodak/mithai)",
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
        "name": "get_run",
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
        "name": "rerun_failed",
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
]


def _gh(*args, timeout=30) -> dict:
    """Run a gh CLI command and return structured result."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
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
    if name == "list_runs":
        repo = input["repo"]
        limit = str(input.get("limit", 5))
        result = _gh(
            "run", "list",
            "--repo", repo,
            "--limit", limit,
            "--json", "databaseId,displayTitle,status,conclusion,headBranch,createdAt",
        )
        return json.dumps(result)

    elif name == "get_run":
        repo = input["repo"]
        run_id = input["run_id"]
        result = _gh(
            "run", "view", run_id,
            "--repo", repo,
            "--json", "databaseId,displayTitle,status,conclusion,jobs,headBranch,createdAt,updatedAt",
        )
        return json.dumps(result)

    elif name == "rerun_failed":
        repo = input["repo"]
        run_id = input["run_id"]
        result = _gh(
            "run", "rerun", run_id,
            "--repo", repo,
            "--failed",
        )
        return json.dumps(result)

    return json.dumps({"error": f"Unknown tool: {name}"})
