"""Skill: Kubernetes operations and self-healing agent."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    # --- Read-only tools (auto-execute) ---
    {
        "name": "get_pods",
        "description": "List pods in a namespace with their status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Kubernetes namespace (default: 'default')",
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
                },
                "container": {
                    "type": "string",
                    "description": "Container name (optional, for multi-container pods)",
                },
            },
            "required": ["pod"],
        },
    },
    {
        "name": "describe_pod",
        "description": "Get detailed information about a pod (events, conditions, containers, resource limits).",
        "input_schema": {
            "type": "object",
            "properties": {
                "pod": {"type": "string", "description": "Pod name"},
                "namespace": {"type": "string", "default": "default"},
            },
            "required": ["pod"],
        },
    },
    {
        "name": "get_events",
        "description": "Get recent Kubernetes events for a namespace, sorted by time. Useful for diagnosing why pods are failing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Namespace to get events for"},
                "resource": {
                    "type": "string",
                    "description": "Optional: filter events for a specific resource name (pod or deployment name)",
                },
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "get_node_status",
        "description": "Get node conditions, capacity, and allocatable resources to diagnose Pending pod issues.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Optional: specific node name. Omit to get all nodes.",
                },
            },
        },
    },
    {
        "name": "rollout_status",
        "description": "Check the rollout status and revision history of a deployment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Deployment name"},
                "namespace": {"type": "string", "default": "default"},
            },
            "required": ["deployment"],
        },
    },
    {
        "name": "scan_cluster",
        "description": (
            "Scan namespaces for unhealthy pods and deployments. "
            "Detects: CrashLoopBackOff, OOMKilled, Pending, ImagePullBackOff, failed rollouts. "
            "Use this first when asked to diagnose or heal a cluster."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespaces": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Namespaces to scan. Omit or empty to scan all non-system namespaces.",
                },
            },
        },
    },
    # --- Mutating tools (require Slack approval) ---
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
        "name": "rollback_deployment",
        "description": "Roll back a deployment to its previous revision.",
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Deployment name"},
                "namespace": {"type": "string", "default": "default"},
                "revision": {
                    "type": "integer",
                    "description": "Target revision number. Omit to roll back to the previous revision.",
                },
            },
            "required": ["deployment"],
        },
        "human": "approve",
    },
    {
        "name": "scale_deployment",
        "description": "Scale a deployment to a specific number of replicas.",
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Deployment name"},
                "namespace": {"type": "string", "default": "default"},
                "replicas": {"type": "integer", "description": "Target replica count"},
            },
            "required": ["deployment", "replicas"],
        },
        "human": "approve",
    },
    {
        "name": "patch_resources",
        "description": "Patch resource limits/requests on a deployment container (e.g. to fix OOMKilled).",
        "input_schema": {
            "type": "object",
            "properties": {
                "deployment": {"type": "string", "description": "Deployment name"},
                "namespace": {"type": "string", "default": "default"},
                "container": {"type": "string", "description": "Container name to patch"},
                "memory_limit": {
                    "type": "string",
                    "description": "New memory limit (e.g. '512Mi', '2Gi')",
                },
                "cpu_limit": {
                    "type": "string",
                    "description": "New CPU limit (e.g. '500m', '2')",
                },
                "memory_request": {"type": "string", "description": "New memory request"},
                "cpu_request": {"type": "string", "description": "New CPU request"},
            },
            "required": ["deployment", "container"],
        },
        "human": "approve",
    },
]

# ---------------------------------------------------------------------------
# kubectl helper
# ---------------------------------------------------------------------------

_KUBECTL_TIMEOUT = 30


def _kubectl(*args, timeout=_KUBECTL_TIMEOUT) -> dict:
    """Run a kubectl command and return a structured result."""
    cmd = ["kubectl"]
    cmd.extend(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or result.stdout.strip()}
        return {"output": result.stdout.strip()}
    except FileNotFoundError:
        return {"error": "kubectl not found — is it installed and in PATH?"}
    except subprocess.TimeoutExpired:
        return {"error": f"kubectl timed out after {timeout}s"}


def _kubectl_flags(config: dict) -> list[str]:
    """Build global kubectl flags from skill config (context, kubeconfig)."""
    flags = []
    if config.get("context"):
        flags += ["--context", config["context"]]
    if config.get("kubeconfig"):
        flags += ["--kubeconfig", config["kubeconfig"]]
    return flags


# ---------------------------------------------------------------------------
# Scan / detection logic
# ---------------------------------------------------------------------------

_SYSTEM_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease"}

_UNHEALTHY_STATUSES = {
    "CrashLoopBackOff",
    "OOMKilled",
    "Error",
    "ImagePullBackOff",
    "ErrImagePull",
    "Pending",
    "Evicted",
    "ContainerStatusUnknown",
    "CreateContainerConfigError",
    "InvalidImageName",
}


def _list_namespaces(flags: list[str]) -> list[str]:
    result = _kubectl("get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}", *flags)
    if "error" in result:
        return []
    return result["output"].split()


def _scan_namespace(ns: str, flags: list[str]) -> list[dict]:
    """Return a list of issue dicts for unhealthy resources in a namespace."""
    issues = []

    # --- Pod scan ---
    pods_result = _kubectl("get", "pods", "-n", ns, "-o", "json", *flags)
    if "error" not in pods_result:
        try:
            pods = json.loads(pods_result["output"]).get("items", [])
        except json.JSONDecodeError:
            pods = []

        for pod in pods:
            pod_name = pod["metadata"]["name"]
            phase = pod["status"].get("phase", "Unknown")
            container_statuses = (
                pod["status"].get("containerStatuses", [])
                + pod["status"].get("initContainerStatuses", [])
            )

            issue_added = False
            for cs in container_statuses:
                waiting = cs.get("state", {}).get("waiting", {})
                terminated = cs.get("state", {}).get("terminated", {})
                reason = waiting.get("reason") or terminated.get("reason", "")
                restart_count = cs.get("restartCount", 0)

                if reason in _UNHEALTHY_STATUSES:
                    severity = _severity_for(reason, restart_count)
                    issues.append({
                        "kind": "Pod",
                        "namespace": ns,
                        "name": pod_name,
                        "container": cs["name"],
                        "reason": reason,
                        "restart_count": restart_count,
                        "severity": severity,
                        "message": waiting.get("message") or terminated.get("message", ""),
                    })
                    issue_added = True
                    break  # one issue per pod

            # Catch Pending pods that never started (no container statuses yet)
            if not issue_added and phase == "Pending":
                issues.append({
                    "kind": "Pod",
                    "namespace": ns,
                    "name": pod_name,
                    "container": "",
                    "reason": "Pending",
                    "restart_count": 0,
                    "severity": "low",
                    "message": "Pod has not been scheduled or containers have not started",
                })

    # --- Deployment rollout scan ---
    deps_result = _kubectl("get", "deployments", "-n", ns, "-o", "json", *flags)
    if "error" not in deps_result:
        try:
            deps = json.loads(deps_result["output"]).get("items", [])
        except json.JSONDecodeError:
            deps = []

        for dep in deps:
            dep_name = dep["metadata"]["name"]
            status = dep.get("status", {})
            conditions = status.get("conditions", [])
            for cond in conditions:
                if cond.get("type") == "Available" and cond.get("status") == "False":
                    issues.append({
                        "kind": "Deployment",
                        "namespace": ns,
                        "name": dep_name,
                        "reason": "UnavailableDeployment",
                        "severity": "high",
                        "message": cond.get("message", "Deployment not available"),
                    })
                    break
                if cond.get("type") == "Progressing" and cond.get("reason") == "ProgressDeadlineExceeded":
                    issues.append({
                        "kind": "Deployment",
                        "namespace": ns,
                        "name": dep_name,
                        "reason": "ProgressDeadlineExceeded",
                        "severity": "high",
                        "message": cond.get("message", "Rollout stalled"),
                    })
                    break

    return issues


def _severity_for(reason: str, restart_count: int) -> str:
    if reason in {"OOMKilled", "CrashLoopBackOff"}:
        return "high" if restart_count >= 5 else "medium"
    if reason in {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}:
        return "medium"
    if reason == "Pending":
        return "low"
    return "medium"


def _scan_namespaces(namespaces: list[str], config: dict) -> list[dict]:
    """Scan a list of namespaces and return all issues found."""
    flags = _kubectl_flags(config)
    exclude = set(config.get("exclude_namespaces", list(_SYSTEM_NAMESPACES)))

    if not namespaces:
        all_ns = _list_namespaces(flags)
        namespaces = [ns for ns in all_ns if ns not in exclude]

    issues = []
    for ns in namespaces:
        issues.extend(_scan_namespace(ns, flags))
    return issues


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

def handle(name: str, input: dict, ctx: dict) -> str:  # noqa: A002
    config = ctx.get("config", {})
    flags = _kubectl_flags(config)
    default_ns = config.get("default_namespace", "default")

    if name == "get_pods":
        ns = input.get("namespace", default_ns)
        result = _kubectl("get", "pods", "-n", ns, "-o", "wide", *flags)
        return json.dumps(result)

    if name == "get_logs":
        ns = input.get("namespace", default_ns)
        tail = str(input.get("tail", 50))
        cmd = ["logs", input["pod"], "-n", ns, f"--tail={tail}"]
        if input.get("container"):
            cmd += ["-c", input["container"]]
        result = _kubectl(*cmd, *flags)
        return json.dumps(result)

    if name == "describe_pod":
        ns = input.get("namespace", default_ns)
        result = _kubectl("describe", "pod", input["pod"], "-n", ns, *flags)
        return json.dumps(result)

    if name == "get_events":
        ns = input["namespace"]
        cmd = ["get", "events", "-n", ns, "--sort-by=.lastTimestamp"]
        if input.get("resource"):
            cmd += ["--field-selector", f"involvedObject.name={input['resource']}"]
        result = _kubectl(*cmd, *flags)
        return json.dumps(result)

    if name == "get_node_status":
        if input.get("node"):
            result = _kubectl("describe", "node", input["node"], *flags)
        else:
            result = _kubectl("get", "nodes", "-o", "wide", *flags)
        return json.dumps(result)

    if name == "rollout_status":
        ns = input.get("namespace", default_ns)
        dep = input["deployment"]
        status = _kubectl("rollout", "status", f"deployment/{dep}", "-n", ns, *flags)
        history = _kubectl("rollout", "history", f"deployment/{dep}", "-n", ns, *flags)
        return json.dumps({"status": status, "history": history})

    if name == "scan_cluster":
        namespaces = input.get("namespaces") or []
        issues = _scan_namespaces(namespaces, config)
        return json.dumps({
            "issues_found": len(issues),
            "issues": issues,
            "scanned_at": datetime.utcnow().isoformat() + "Z",
        })

    if name == "restart_deployment":
        ns = input.get("namespace", default_ns)
        dep = input["deployment"]
        result = _kubectl("rollout", "restart", f"deployment/{dep}", "-n", ns, *flags)
        return json.dumps(result)

    if name == "rollback_deployment":
        ns = input.get("namespace", default_ns)
        dep = input["deployment"]
        cmd = ["rollout", "undo", f"deployment/{dep}", "-n", ns]
        if input.get("revision"):
            cmd += [f"--to-revision={input['revision']}"]
        result = _kubectl(*cmd, *flags)
        return json.dumps(result)

    if name == "scale_deployment":
        ns = input.get("namespace", default_ns)
        dep = input["deployment"]
        replicas = input["replicas"]
        result = _kubectl("scale", f"deployment/{dep}", "-n", ns, f"--replicas={replicas}", *flags)
        return json.dumps(result)

    if name == "patch_resources":
        ns = input.get("namespace", default_ns)
        dep = input["deployment"]
        container = input["container"]

        resources: dict = {}
        if input.get("memory_limit") or input.get("cpu_limit"):
            resources["limits"] = {}
            if input.get("memory_limit"):
                resources["limits"]["memory"] = input["memory_limit"]
            if input.get("cpu_limit"):
                resources["limits"]["cpu"] = input["cpu_limit"]
        if input.get("memory_request") or input.get("cpu_request"):
            resources["requests"] = {}
            if input.get("memory_request"):
                resources["requests"]["memory"] = input["memory_request"]
            if input.get("cpu_request"):
                resources["requests"]["cpu"] = input["cpu_request"]

        if not resources:
            return json.dumps({"error": "No resource fields provided to patch"})

        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": container, "resources": resources}]
                    }
                }
            }
        }
        result = _kubectl(
            "patch", "deployment", dep, "-n", ns,
            "--type=strategic", "-p", json.dumps(patch),
            *flags,
        )
        return json.dumps(result)

    return json.dumps({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Background self-healing polling loop
# ---------------------------------------------------------------------------

# Tracks last alert time per (namespace, name) to suppress storms
_alert_history: dict[tuple[str, str], datetime] = {}
_flap_counts: dict[tuple[str, str], list[datetime]] = {}
_alert_history_lock = threading.Lock()


def _check_and_record_alert(
    key: tuple[str, str], cooldown_minutes: int, flap_window_minutes: int = 60
) -> tuple[bool, int]:
    """Atomically check cooldown and record alert if allowed.

    Returns (should_alert, flap_count). If should_alert is False, the resource
    is still within its cooldown window and no alert should be sent.
    """
    now = datetime.utcnow()
    with _alert_history_lock:
        last = _alert_history.get(key)
        if last is not None and now - last <= timedelta(minutes=cooldown_minutes):
            return False, 0

        _alert_history[key] = now
        history = _flap_counts.setdefault(key, [])
        cutoff = now - timedelta(minutes=flap_window_minutes)
        history[:] = [t for t in history if t > cutoff]
        history.append(now)
        return True, len(history)


def _format_slack_alert(issue: dict, flap_count: int) -> dict:
    """Format a Slack Block Kit message for a detected issue."""
    severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(issue["severity"], "⚪")
    resource = f"{issue['namespace']}/{issue['name']}"
    reason = issue["reason"]
    message = issue.get("message", "")
    restart_info = f" ({issue['restart_count']} restarts)" if issue.get("restart_count") else ""

    if flap_count >= 3:
        header = f"🚨 *Flapping issue — needs investigation* | `{resource}`"
        body = f"*Type*: {reason}{restart_info}\n*Flap count*: {flap_count} alerts in the last hour\n*Details*: {message}"
    else:
        header = f"{severity_emoji} *K8s Issue Detected* | `{resource}`"
        body = f"*Type*: {reason}{restart_info}\n*Details*: {message}"

    return {
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": body}},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Detected at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"},
                ],
            },
        ]
    }


def _healing_loop(alert_channel: str, poll_interval: int, cooldown_minutes: int, skill_config: dict) -> None:
    """Background daemon loop: scan cluster and post Slack alerts for issues."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        logger.warning("kubernetes: SLACK_BOT_TOKEN not set — healing loop cannot post alerts")
        return

    try:
        from slack_sdk import WebClient  # type: ignore
        from slack_sdk.errors import SlackApiError  # type: ignore
    except ImportError:
        logger.warning("kubernetes: slack_sdk not installed — healing loop disabled")
        return

    client = WebClient(token=token)
    logger.info(
        "kubernetes: healing loop started — channel=%s interval=%dm cooldown=%dm",
        alert_channel, poll_interval, cooldown_minutes,
    )

    while True:
        time.sleep(poll_interval * 60)
        try:
            issues = _scan_namespaces(
                namespaces=skill_config.get("namespaces") or [],
                config=skill_config,
            )

            for issue in issues:
                key = (issue["namespace"], issue["name"])
                should_alert, flap_count = _check_and_record_alert(key, cooldown_minutes)
                if not should_alert:
                    continue

                payload = _format_slack_alert(issue, flap_count)

                try:
                    client.chat_postMessage(channel=alert_channel, **payload)
                except SlackApiError as e:
                    logger.warning("kubernetes: failed to post alert: %s", e)

        except Exception:
            logger.exception("kubernetes: error during healing loop scan")


def startup(config: dict) -> None:
    """Start the background self-healing polling loop if configured."""
    alert_channel = config.get("alert_channel")
    if not alert_channel:
        logger.debug("kubernetes: no alert_channel configured — polling loop disabled")
        return

    poll_interval = int(config.get("poll_interval_minutes", 5))
    cooldown_minutes = int(config.get("cooldown_minutes", 30))

    thread = threading.Thread(
        target=_healing_loop,
        args=(alert_channel, poll_interval, cooldown_minutes, config),
        daemon=True,
        name="k8s-healing-loop",
    )
    thread.start()
    logger.info("kubernetes: healing loop thread started")
