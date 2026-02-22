"""Skill: inspect conversation sessions."""

import json

TOOLS = [
    {
        "name": "list_sessions",
        "description": "List recent conversation sessions with last message preview.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max sessions to return (default 10)",
                },
            },
        },
    },
    {
        "name": "get_session",
        "description": "Get full detail of a session by ID (e.g. 'slack:C1234').",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID (format: platform:channel_id)",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "search_sessions",
        "description": "Search across all sessions by keyword in messages and responses.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword to search for",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10)",
                },
            },
            "required": ["query"],
        },
    },
]


def handle(name: str, input: dict, ctx: dict) -> str:
    from mithai.core.session import SessionManager

    state = ctx["state"]
    mgr = SessionManager(state)

    if name == "list_sessions":
        limit = input.get("limit", 10)
        sessions = mgr.list_sessions(limit=limit)
        return json.dumps({"sessions": sessions, "count": len(sessions)})

    elif name == "get_session":
        session = mgr.get_session(input["session_id"])
        if session is None:
            return json.dumps({"error": f"Session not found: {input['session_id']}"})
        return json.dumps(session)

    elif name == "search_sessions":
        query = input["query"]
        limit = input.get("limit", 10)
        results = mgr.search(query, limit=limit)
        return json.dumps({"results": results, "count": len(results), "query": query})

    return json.dumps({"error": f"Unknown tool: {name}"})
