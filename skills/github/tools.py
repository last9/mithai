"""GitHub skill — all tools provided via MCP."""

import json

TOOLS = []

MCP_TOOLS = [
    {
        "server": "github",
        "tools": "*",
        "human": None,
        "human_overrides": {
            "create_branch": "approve",
            "push_files": "approve",
            "create_or_update_file": "approve",
            "create_pull_request": "approve",
            "merge_pull_request": "approve",
            "create_repository": "approve",
            "fork_repository": "approve",
            "create_pull_request_review": "approve",
            "update_pull_request_branch": "approve",
        },
    },
]


def handle(name, input, ctx):
    """No native tools — all tools are MCP-backed."""
    return json.dumps({"error": f"Unknown native tool: {name}"})
