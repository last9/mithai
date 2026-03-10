"""Skill: Slack-specific tools — history fetch, user lookup."""

import json

# Injected at startup via bind() — None when not running on a Slack adapter
_adapter = None


TOOLS = [
    {
        "name": "slack_get_history",
        "description": (
            "Fetch recent messages from a Slack channel. "
            "Returns formatted messages (oldest first) and a user ID → name map. "
            "Use this to understand channel context, team members, and recurring topics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Slack channel ID (e.g. C01234ABC). Defaults to the current channel.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of recent messages to fetch (default 100, max 500).",
                },
            },
            "required": [],
        },
    },
]


def bind(engine, adapter):
    """Store the Slack adapter so tools can call its API client."""
    global _adapter
    try:
        from mithai.adapters.slack import SlackAdapterBase
        if isinstance(adapter, SlackAdapterBase):
            _adapter = adapter
    except ImportError:
        pass


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "slack_get_history":
        if _adapter is None:
            return json.dumps({"error": "Slack adapter not available in this context"})

        channel_id = input.get("channel_id") or ctx.get("channel_id", "")
        if not channel_id:
            return json.dumps({"error": "channel_id is required"})

        limit = min(int(input.get("limit", 100)), 500)
        messages, user_map = _adapter._fetch_channel_history(channel_id, limit)

        return json.dumps({
            "messages": messages,
            "user_map": user_map,
            "count": len(messages),
        })

    return json.dumps({"error": f"Unknown tool: {name}"})
