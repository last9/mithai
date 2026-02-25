"""Linear test skill — purely MCP-backed, no native tools."""

import json

TOOLS = []

MCP_TOOLS = [
    {
        "server": "linear",
        "tools": "*",  # Pull all 41 tools from Linear's official MCP server
        "human": "approve",  # Default: require approval for writes
        "human_overrides": {
            # Read-only tools auto-execute
            "get_issue": None,
            "list_issues": None,
            "list_issue_statuses": None,
            "get_issue_status": None,
            "list_issue_labels": None,
            "list_comments": None,
            "list_projects": None,
            "get_project": None,
            "list_milestones": None,
            "get_milestone": None,
            "list_teams": None,
            "get_team": None,
            "list_users": None,
            "get_user": None,
            "list_cycles": None,
            "get_document": None,
            "list_documents": None,
            "get_attachment": None,
            "search_documentation": None,
            "list_customers": None,
            "list_initiatives": None,
            "get_initiative": None,
            "get_status_updates": None,
            "list_project_labels": None,
            "extract_images": None,
        },
    },
]


def handle(name, input, ctx):
    """No native tools — all tools are MCP-backed."""
    return json.dumps({"error": f"Unknown native tool: {name}"})
