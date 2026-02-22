"""Skill: Shell command runner with allowlist."""

import json
import shlex
import subprocess


TOOLS = [
    {
        "name": "run_command",
        "description": "Run any shell command. Requires human approval. Commands in the allowlist run directly after approval; commands outside the allowlist also run if the human approves.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run",
                },
            },
            "required": ["command"],
        },
        "human": "approve",
    },
    {
        "name": "list_allowed",
        "description": "List the commands that are allowed to run.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

DEFAULT_ALLOWED = ["df -h", "free -h", "uptime", "whoami", "uname -a", "ps aux"]


def handle(name: str, input: dict, ctx: dict) -> str:
    config = ctx.get("config", {})
    allowed = config.get("allowed_commands", DEFAULT_ALLOWED)
    timeout = config.get("timeout", 30)

    if name == "list_allowed":
        return json.dumps({"allowed_commands": allowed})

    elif name == "run_command":
        command = input["command"]
        human_approved = ctx.get("human_approved", False)

        # If a human approved this via Human MCP, skip the allowlist —
        # the human already reviewed the exact command.
        if not human_approved and command not in allowed:
            return json.dumps({
                "error": f"Command not in allowlist: {command}",
                "allowed": allowed,
            })

        try:
            result = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return json.dumps({
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Command timed out after {timeout}s", "command": command})
        except Exception as e:
            return json.dumps({"error": str(e), "command": command})

    return json.dumps({"error": f"Unknown tool: {name}"})
