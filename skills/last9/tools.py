"""Last9 observability skill — all tools provided via MCP."""

import json

TOOLS = []

MCP_TOOLS = [
    {
        "server": "last9",
        "tools": "*",  # Import all tools from Last9 MCP server
        "human": None,  # Default: auto-execute (read-heavy observability tools)
    },
]


def handle(name, input, ctx):
    """No native tools — all tools are MCP-backed."""
    return json.dumps({"error": f"Unknown native tool: {name}"})
