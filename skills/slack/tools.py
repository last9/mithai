"""Skill: Slack-specific tools — history fetch, send message."""

import json

# SlackClient injected at startup via bind() — None when not running on a Slack adapter
_client = None


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
    {
        "name": "slack_send_message",
        "description": (
            "Post a message to a Slack channel or thread. "
            "Use for proactive notifications, summaries, or pinging teammates."
        ),
        "human": "approve",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": "Slack channel ID. Defaults to current channel.",
                },
                "text": {
                    "type": "string",
                    "description": "Message text (Slack mrkdwn supported).",
                },
                "thread_ts": {
                    "type": "string",
                    "description": "Reply in a thread. Optional.",
                },
            },
            "required": ["text"],
        },
    },
]


def bind(engine, adapter):
    """Store the SlackClient so tools can make Slack API calls."""
    global _client
    if hasattr(adapter, "slack_client"):
        _client = adapter.slack_client


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "slack_get_history":
        if _client is None:
            return json.dumps({"error": "Slack adapter not available in this context"})

        channel_id = input.get("channel_id") or ctx.get("channel_id", "")
        if not channel_id:
            return json.dumps({"error": "channel_id is required"})

        limit = min(int(input.get("limit", 100)), 500)
        messages, user_map = _client.get_history(channel_id, limit)

        return json.dumps({
            "messages": messages,
            "user_map": user_map,
            "count": len(messages),
        })

    if name == "slack_send_message":
        if _client is None:
            return json.dumps({"error": "Slack adapter not available in this context"})

        text = input.get("text", "")
        if not text:
            return json.dumps({"error": "text is required"})

        channel_id = input.get("channel_id") or ctx.get("channel_id", "")
        if not channel_id:
            return json.dumps({"error": "channel_id is required"})

        thread_ts = input.get("thread_ts")
        result = _client.post_message(channel_id, text, thread_ts=thread_ts)
        return json.dumps(result)

    return json.dumps({"error": f"Unknown tool: {name}"})
