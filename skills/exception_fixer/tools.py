"""Exception fixer skill — orchestrates Last9 MCP + GitHub native tools."""

import json
from urllib.parse import quote

TOOLS = [
    {
        "name": "format_pr_body",
        "description": (
            "Build a formatted PR body for an exception fix. "
            "Call this to generate the PR description, then pass the result "
            "as the body to github__create_pull_request."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "exception_type": {
                    "type": "string",
                    "description": "The exception class/type (e.g. NullPointerException, IndexOutOfBoundsError)",
                },
                "service_name": {
                    "type": "string",
                    "description": "The service where the exception was observed",
                },
                "exception_message": {
                    "type": "string",
                    "description": "The exception message text",
                },
                "frequency": {
                    "type": "string",
                    "description": "How often it occurs (e.g. '42 occurrences in last 24 hours')",
                },
                "root_cause": {
                    "type": "string",
                    "description": "Explanation of why this exception occurs",
                },
                "fix_description": {
                    "type": "string",
                    "description": "What was changed and why",
                },
                "files_changed": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": ["path", "summary"],
                    },
                    "description": "List of changed files with one-line summaries",
                },
            },
            "required": [
                "exception_type",
                "service_name",
                "exception_message",
                "frequency",
                "root_cause",
                "fix_description",
                "files_changed",
            ],
        },
    },
]

# Only Last9 tools come via MCP. GitHub tools are native (from github skill).
MCP_TOOLS = [
    {
        "server": "last9",
        "tools": [
            "get_exceptions",
            "get_service_traces",
            "get_service_summary",
            "get_logs",
        ],
        "human": None,
    },
]


def _format_pr_body(inp: dict) -> str:
    """Build the standardized PR body markdown."""
    service = inp["service_name"]
    last9_url = f"https://app.last9.io/last9/exceptions?service={quote(service)}"

    files_section = "\n".join(
        f"- `{f['path']}` — {f['summary']}" for f in inp["files_changed"]
    )

    return f"""\
## Exception Fix: `{inp["exception_type"]}`

### Context
- **Service:** `{service}`
- **Exception:** `{inp["exception_message"]}`
- **Frequency:** {inp["frequency"]}
- **Last9 Link:** [View in Last9]({last9_url})

### Root Cause
{inp["root_cause"]}

### Fix
{inp["fix_description"]}

### Files Changed
{files_section}"""


def handle(name, inp, ctx):
    if name == "format_pr_body":
        return _format_pr_body(inp)
    return json.dumps({"error": f"Unknown tool: {name}"})
