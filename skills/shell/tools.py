"""Skill: Shell command runner with dynamic approval."""

import json
import re
import shlex
import subprocess

# Shell operators that require sh -c to interpret
_SHELL_OPERATORS = re.compile(r"[|&;<>]")


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


def _load_approvals(ctx: dict) -> dict:
    """Load approval history from memory."""
    from pathlib import Path
    config = ctx.get("config", {})
    memory_dir = Path(config.get("memory_dir", "./memory")).resolve()
    approvals_file = memory_dir / "approvals.json"
    if approvals_file.exists():
        try:
            return json.loads(approvals_file.read_text())
        except Exception:
            pass
    return {}


def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    """Determine approval level at runtime.

    Commands in the allowlist auto-execute.
    Commands with enough prior approvals and no denials auto-promote.
    Everything else requires human approval.
    """
    if name != "run_command":
        return None

    config = ctx.get("config", {})
    allowed = config.get("allowed_commands", DEFAULT_ALLOWED)
    command = input.get("command", "")

    # Static allowlist
    if command in allowed:
        return None

    # Learned approvals — auto-promote after threshold
    threshold = config.get("approval_auto_promote", 3)
    approvals = _load_approvals(ctx)
    history = approvals.get("shell__run_command", {}).get(command, {})
    if history.get("approved", 0) >= threshold and history.get("denied", 0) == 0:
        return None  # Auto-promoted by learning

    return "approve"


def handle(name: str, input: dict, ctx: dict) -> str:
    config = ctx.get("config", {})
    timeout = config.get("timeout", 30)

    if name == "run_command":
        command = input["command"]
        try:
            # Use shell mode for pipes, redirects, chained commands
            use_shell = bool(_SHELL_OPERATORS.search(command))
            result = subprocess.run(
                command if use_shell else shlex.split(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                shell=use_shell,
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
