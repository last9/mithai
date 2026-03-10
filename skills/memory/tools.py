"""Skill: Persistent memory for learning across conversations."""

import json


TOOLS = [
    {
        "name": "memory_read",
        "description": "Read a file from the bot's persistent memory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to memory/ (e.g., 'MEMORY.md', 'playbooks/restart-daemonset.md')",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "memory_write",
        "description": "Write to the bot's persistent memory. Use this to save infrastructure facts, error patterns, corrections, and playbooks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to memory/ (e.g., 'MEMORY.md', 'playbooks/restart-daemonset.md')",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
                "mode": {
                    "type": "string",
                    "enum": ["append", "overwrite"],
                    "description": "Write mode: 'append' adds to end, 'overwrite' replaces file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "memory_search",
        "description": "Search across all memory files by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword to search for",
                },
            },
            "required": ["query"],
        },
    },
]


def handle(name: str, input: dict, ctx: dict) -> str:
    memory = ctx.get("memory")
    if memory is None:
        return json.dumps({"error": "Memory backend not configured"})

    if name == "memory_read":
        if not memory.validate_path(input["path"]):
            return json.dumps({"error": "Invalid path"})
        content = memory.read(input["path"])
        if content is None:
            return json.dumps({"error": f"File not found: {input['path']}", "hint": "Use memory_search to find files"})
        return json.dumps({"path": input["path"], "content": content})

    elif name == "memory_write":
        if not memory.validate_path(input["path"]):
            return json.dumps({"error": "Invalid path"})
        mode = input.get("mode", "append")
        if mode == "append":
            memory.write(input["path"], input["content"] + "\n", append=True)
        else:
            memory.write(input["path"], input["content"], append=False)
        return json.dumps({"written": input["path"], "mode": mode})

    elif name == "memory_search":
        search_results = memory.search(input["query"])
        results = [
            {"file": sr.path, "matches": [{"line": m.line, "text": m.text} for m in sr.matches[:5]]}
            for sr in search_results
        ]
        return json.dumps({"results": results, "query": input["query"]})

    return json.dumps({"error": f"Unknown tool: {name}"})
