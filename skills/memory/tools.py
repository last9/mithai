"""Skill: Persistent memory for learning across conversations."""

import json
import os
from pathlib import Path


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


def _memory_dir(ctx: dict) -> Path:
    """Get the memory directory from config or default."""
    config = ctx.get("config", {})
    base = config.get("memory_dir", "./memory")
    return Path(base).resolve()


def _safe_path(memory_dir: Path, relative: str) -> Path | None:
    """Resolve a relative path safely within the memory directory."""
    target = (memory_dir / relative).resolve()
    if not str(target).startswith(str(memory_dir)):
        return None  # Path escape attempt
    return target


def handle(name: str, input: dict, ctx: dict) -> str:
    mem_dir = _memory_dir(ctx)

    if name == "memory_read":
        path = _safe_path(mem_dir, input["path"])
        if path is None:
            return json.dumps({"error": "Invalid path"})
        if not path.exists():
            return json.dumps({"error": f"File not found: {input['path']}", "hint": "Use memory_search to find files"})
        return json.dumps({"path": input["path"], "content": path.read_text()})

    elif name == "memory_write":
        path = _safe_path(mem_dir, input["path"])
        if path is None:
            return json.dumps({"error": "Invalid path"})
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = input.get("mode", "append")
        if mode == "append":
            with open(path, "a") as f:
                f.write(input["content"] + "\n")
        else:
            path.write_text(input["content"])
        return json.dumps({"written": input["path"], "mode": mode})

    elif name == "memory_search":
        query = input["query"].lower()
        results = []
        if not mem_dir.exists():
            return json.dumps({"results": [], "query": input["query"]})

        for md_file in mem_dir.rglob("*.md"):
            try:
                content = md_file.read_text()
            except Exception:
                continue
            if query in content.lower():
                # Find matching lines for context
                matches = []
                for i, line in enumerate(content.splitlines(), 1):
                    if query in line.lower():
                        matches.append({"line": i, "text": line.strip()[:200]})
                rel = str(md_file.relative_to(mem_dir))
                results.append({"file": rel, "matches": matches[:5]})

        return json.dumps({"results": results, "query": input["query"]})

    return json.dumps({"error": f"Unknown tool: {name}"})
