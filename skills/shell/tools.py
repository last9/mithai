"""Skill: Shell command runner with dynamic approval."""

import json
import shlex
import subprocess


TOOLS = [
    {
        "name": "run_command",
        "description": "Run a shell command on the host system.",
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
        "human": "dynamic",
    },
]

DEFAULT_ALLOWED = ["df -h", "free -h", "uptime", "whoami", "uname -a", "ps aux"]


def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    """Determine approval level at runtime.

    Commands in the allowlist auto-execute.
    Everything else requires human approval.
    """
    if name != "run_command":
        return None

    config = ctx.get("config", {})
    allowed = config.get("allowed_commands", DEFAULT_ALLOWED)
    command = input.get("command", "")

    if command in allowed:
        return None  # auto-execute
    return "approve"


def handle(name: str, input: dict, ctx: dict) -> str:
    config = ctx.get("config", {})
    timeout = config.get("timeout", 30)

    if name == "run_command":
        command = input["command"]
        try:
            result = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
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
