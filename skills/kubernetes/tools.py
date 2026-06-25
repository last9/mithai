"""Skill: Kubernetes read-only inspection via kubectl."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any


VERIFY = True

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,252}$")
_RESOURCE_TYPES = {
    "pod",
    "pods",
    "deployment",
    "deployments",
    "service",
    "services",
    "node",
    "nodes",
    "namespace",
    "namespaces",
    "ingress",
    "ingresses",
    "statefulset",
    "statefulsets",
    "daemonset",
    "daemonsets",
    "job",
    "jobs",
    "cronjob",
    "cronjobs",
    "replicaset",
    "replicasets",
}


TOOLS = [
    {
        "name": "get_pods",
        "description": "List Kubernetes pods in a namespace or across all namespaces.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace to inspect. Defaults to skills.config.kubernetes.default_namespace.",
                },
                "all_namespaces": {
                    "type": "boolean",
                    "description": "Set true to list pods across all namespaces.",
                },
            },
        },
    },
    {
        "name": "get_deployments",
        "description": "List Kubernetes deployments in a namespace or across all namespaces.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace to inspect. Defaults to skills.config.kubernetes.default_namespace.",
                },
                "all_namespaces": {
                    "type": "boolean",
                    "description": "Set true to list deployments across all namespaces.",
                },
            },
        },
    },
    {
        "name": "get_events",
        "description": "List recent Kubernetes events sorted by creation timestamp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace to inspect. Defaults to skills.config.kubernetes.default_namespace.",
                },
                "all_namespaces": {
                    "type": "boolean",
                    "description": "Set true to list events across all namespaces.",
                },
            },
        },
    },
    {
        "name": "get_logs",
        "description": "Fetch logs for a pod, optionally scoped to a container.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pod": {"type": "string", "description": "Pod name."},
                "namespace": {
                    "type": "string",
                    "description": "Namespace. Defaults to skills.config.kubernetes.default_namespace.",
                },
                "container": {"type": "string", "description": "Optional container name."},
                "previous": {
                    "type": "boolean",
                    "description": "Set true to fetch previous container logs.",
                },
                "tail_lines": {
                    "type": "integer",
                    "description": "Number of log lines to fetch. Default 200, maximum 1000.",
                },
            },
            "required": ["pod"],
        },
    },
    {
        "name": "describe_resource",
        "description": "Run kubectl describe for a named read-only Kubernetes resource.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "description": "Resource type such as pod, deployment, service, node, or namespace.",
                },
                "name": {"type": "string", "description": "Resource name."},
                "namespace": {
                    "type": "string",
                    "description": "Namespace for namespaced resources.",
                },
            },
            "required": ["resource_type", "name"],
        },
    },
]


def _config(ctx: dict) -> dict:
    return ctx.get("config", {}) if isinstance(ctx.get("config", {}), dict) else {}


def _valid_name(value: str | None) -> bool:
    return value is None or bool(_NAME_RE.fullmatch(value))


def _error(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra})


def _base_cmd(config: dict) -> list[str]:
    cmd = ["kubectl"]
    kubeconfig = str(config.get("kubeconfig") or "").strip()
    context = str(config.get("context") or "").strip()
    if kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig])
    if context:
        cmd.extend(["--context", context])
    return cmd


def _namespace_args(input: dict, config: dict) -> list[str]:
    if input.get("all_namespaces"):
        return ["--all-namespaces"]
    namespace = str(input.get("namespace") or config.get("default_namespace") or "").strip()
    if not namespace:
        return []
    if not _valid_name(namespace):
        raise ValueError(f"invalid namespace: {namespace}")
    return ["-n", namespace]


def _run(args: list[str], config: dict) -> dict:
    timeout = int(config.get("timeout", 30))
    if shutil.which("kubectl") is None:
        return {"ok": False, "error": "kubectl not found", "command": args}
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"kubectl timed out after {timeout}s", "command": args}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "command": args}

    payload = {
        "ok": result.returncode == 0,
        "command": args,
        "returncode": result.returncode,
    }
    if result.stdout:
        payload["stdout"] = result.stdout
    if result.stderr:
        payload["stderr"] = result.stderr
    return payload


def _run_json(args: list[str], config: dict) -> str:
    result = _run(args, config)
    if not result.get("ok"):
        return json.dumps(result)
    try:
        result["items"] = json.loads(result.pop("stdout", "{}")).get("items", [])
    except json.JSONDecodeError as exc:
        result["ok"] = False
        result["error"] = f"kubectl returned invalid JSON: {exc}"
    return json.dumps(result)


def _clip(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n...[truncated]", True


def _tail_lines(input: dict) -> int:
    raw = input.get("tail_lines", 200)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 200
    return max(1, min(value, 1000))


def handle(name: str, input: dict, ctx: dict) -> str:
    config = _config(ctx)

    try:
        if name == "get_pods":
            args = _base_cmd(config) + ["get", "pods", "-o", "json"] + _namespace_args(input, config)
            return _run_json(args, config)

        if name == "get_deployments":
            args = (
                _base_cmd(config)
                + ["get", "deployments", "-o", "json"]
                + _namespace_args(input, config)
            )
            return _run_json(args, config)

        if name == "get_events":
            args = (
                _base_cmd(config)
                + ["get", "events", "--sort-by=.metadata.creationTimestamp", "-o", "json"]
                + _namespace_args(input, config)
            )
            return _run_json(args, config)

        if name == "get_logs":
            pod = str(input.get("pod") or "").strip()
            container = str(input.get("container") or "").strip()
            if not pod or not _valid_name(pod):
                return _error("invalid pod name", pod=pod)
            if container and not _valid_name(container):
                return _error("invalid container name", container=container)

            args = _base_cmd(config) + ["logs", pod] + _namespace_args(input, config)
            if container:
                args.extend(["-c", container])
            if input.get("previous"):
                args.append("--previous")
            args.extend(["--tail", str(_tail_lines(input))])

            result = _run(args, config)
            if "stdout" in result:
                result["logs"], result["truncated"] = _clip(result.pop("stdout"), 20000)
            return json.dumps(result)

        if name == "describe_resource":
            resource_type = str(input.get("resource_type") or "").strip().lower()
            resource_name = str(input.get("name") or "").strip()
            if resource_type not in _RESOURCE_TYPES:
                return _error("unsupported resource_type", resource_type=resource_type)
            if not resource_name or not _valid_name(resource_name):
                return _error("invalid resource name", name=resource_name)

            args = (
                _base_cmd(config)
                + ["describe", resource_type, resource_name]
                + _namespace_args(input, config)
            )
            result = _run(args, config)
            if "stdout" in result:
                result["description"], result["truncated"] = _clip(result.pop("stdout"), 20000)
            return json.dumps(result)

    except ValueError as exc:
        return _error(str(exc))

    return _error(f"Unknown tool: {name}")
