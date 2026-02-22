"""Skill: Kubernetes operations via kubectl."""

import json
import subprocess


TOOLS = [
    {
        "name": "get_pods",
        "description": "List pods in a namespace with their status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace (default: 'default')",
                    "default": "default",
                },
            },
        },
    },
    {
        "name": "get_logs",
        "description": "Get recent logs from a pod.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pod": {"type": "string", "description": "Pod name"},
                "namespace": {"type": "string", "default": "default"},
                "tail": {
                    "type": "integer",
                    "description": "Number of log lines (default: 50)",
                    "default": 50,
                },
            },
            "required": ["pod"],
        },
    },
    {
        "name": "restart_deployment",
        "description": "Restart a deployment by performing a rollout restart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Deployment name"},
                "namespace": {"type": "string", "default": "default"},
            },
            "required": ["deployment"],
        },
        "human": "approve",
    },
    {
        "name": "describe_pod",
        "description": "Get detailed information about a pod (events, conditions, containers).",
        "input_schema": {
            "type": "object",
            "properties": {
                "pod": {"type": "string", "description": "Pod name"},
                "namespace": {"type": "string", "default": "default"},
            },
            "required": ["pod"],
        },
    },
]


def _kubectl(*args, timeout=30) -> dict:
    """Run a kubectl command and return structured result."""
    try:
        result = subprocess.run(
            ["kubectl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        return {"output": result.stdout.strip()}
    except FileNotFoundError:
        return {"error": "kubectl not found. Is it installed and in PATH?"}
    except subprocess.TimeoutExpired:
        return {"error": f"kubectl timed out after {timeout}s"}


def handle(name: str, input: dict, ctx: dict) -> str:
    config = ctx.get("config", {})
    default_ns = config.get("default_namespace", "default")

    if name == "get_pods":
        ns = input.get("namespace", default_ns)
        result = _kubectl("get", "pods", "-n", ns, "-o", "wide")
        return json.dumps(result)

    elif name == "get_logs":
        ns = input.get("namespace", default_ns)
        tail = str(input.get("tail", 50))
        result = _kubectl("logs", input["pod"], "-n", ns, f"--tail={tail}")
        return json.dumps(result)

    elif name == "restart_deployment":
        ns = input.get("namespace", default_ns)
        dep = input["deployment"]
        result = _kubectl("rollout", "restart", f"deployment/{dep}", "-n", ns)
        return json.dumps(result)

    elif name == "describe_pod":
        ns = input.get("namespace", default_ns)
        result = _kubectl("describe", "pod", input["pod"], "-n", ns)
        return json.dumps(result)

    return json.dumps({"error": f"Unknown tool: {name}"})
