"""Skill: Kubernetes operations, self-healing agent, security auditing, and manifest generation."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Late-bind state — set by bind() after engine + adapters are ready
# ---------------------------------------------------------------------------

_engine = None

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
        "description": "Get recent logs from a pod. Use previous=true to get logs from a crashed/previous container instance.",
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
                "previous": {
                    "type": "boolean",
                    "description": "Get logs from previous (crashed) container instance. Useful for CrashLoopBackOff.",
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
    {
        "name": "cluster_health_score",
        "description": (
            "Run a comprehensive cluster health assessment and return a score from 0-100. "
            "Checks node health, pod issues, CrashLoopBackOff count, privileged containers, "
            "resource limits, PVC status, and warning event volume. "
            "Use this for a quick 'how healthy is my cluster?' overview."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "security_audit",
        "description": (
            "Audit the cluster (or a specific namespace) for security issues: "
            "privileged containers, root-running pods, host namespace access, "
            "missing resource limits, default service accounts, wildcard RBAC, "
            "and missing NetworkPolicies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {
                    "type": "string",
                    "description": "Namespace to audit. Omit to audit all namespaces.",
                },
            },
        },
    },
    {
        "name": "get_resource_usage",
        "description": "Show live CPU and memory usage for pods or nodes (requires metrics-server).",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["pods", "nodes"],
                    "description": "Whether to show pod or node resource usage (default: pods)",
                },
                "namespace": {
                    "type": "string",
                    "description": "Namespace for pods (default: all namespaces)",
                },
            },
        },
    },
    {
        "name": "generate_manifest",
        "description": (
            "Generate a production-ready Kubernetes YAML manifest. "
            "Supported types: deployment, statefulset, service, ingress, configmap, "
            "secret, pvc, networkpolicy, hpa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["deployment", "statefulset", "service", "ingress", "configmap", "secret", "pvc", "networkpolicy", "hpa"],
                    "description": "Resource type to generate",
                },
                "name": {"type": "string", "description": "Resource name"},
                "namespace": {"type": "string", "description": "Target namespace (default: default)"},
            },
            "required": ["kind", "name"],
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
    {
        "name": "drain_node",
        "description": "Safely cordon and drain a node for maintenance (evicts all non-daemonset pods).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name to drain"},
                "force": {
                    "type": "boolean",
                    "description": "Force drain even if pods can't be gracefully evicted",
                },
            },
            "required": ["node"],
        },
        "human": "approve",
    },
    {
        "name": "uncordon_node",
        "description": "Uncordon a node after maintenance so it can accept new pods again.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name to uncordon"},
            },
            "required": ["node"],
        },
        "human": "approve",
    },
]

# ---------------------------------------------------------------------------
# kubectl helper
# ---------------------------------------------------------------------------

_KUBECTL_TIMEOUT = 30
_SCRIPTS_DIR = Path(__file__).parent / "scripts"


def _run_script(script_name: str, *args, timeout: int = 60) -> dict:
    """Run a bundled shell script and return parsed JSON output."""
    script = _SCRIPTS_DIR / script_name
    if not script.exists():
        return {"error": f"Script not found: {script_name}"}
    try:
        result = subprocess.run(
            ["bash", str(script), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # Scripts write diagnostics to stderr and JSON to stdout
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if not stdout:
            return {"error": stderr or "Script produced no output", "returncode": result.returncode}
        try:
            return {"result": json.loads(stdout), "log": stderr}
        except json.JSONDecodeError:
            return {"output": stdout, "log": stderr}
    except subprocess.TimeoutExpired:
        return {"error": f"Script timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


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
        if input.get("previous"):
            cmd += ["--previous"]
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

    if name == "cluster_health_score":
        # Pass kubectl context/kubeconfig via env if configured
        result = _run_script("cluster-health-check.sh", timeout=60)
        return json.dumps(result)

    if name == "security_audit":
        args_list = []
        if input.get("namespace"):
            args_list.append(input["namespace"])
        result = _run_script("security-audit.sh", *args_list, timeout=60)
        return json.dumps(result)

    if name == "get_resource_usage":
        kind = input.get("kind", "pods")
        if kind == "nodes":
            result = _kubectl("top", "nodes", *flags)
        else:
            ns = input.get("namespace")
            if ns:
                result = _kubectl("top", "pods", "-n", ns, *flags)
            else:
                result = _kubectl("top", "pods", "-A", *flags)
        return json.dumps(result)

    if name == "generate_manifest":
        kind = input["kind"]
        resource_name = input["name"]
        ns = input.get("namespace", "default")
        result = _run_script("generate-manifest.sh", kind, resource_name, ns, timeout=10)
        return json.dumps(result)

    if name == "drain_node":
        node = input["node"]
        args_list = [node]
        if input.get("force"):
            args_list.append("--force")
        result = _run_script("node-maintenance.sh", *args_list, timeout=360)
        return json.dumps(result)

    if name == "uncordon_node":
        result = _kubectl("uncordon", input["node"], *flags)
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
    """Background daemon loop: scan cluster, post Slack alerts, and auto-investigate."""
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
    auto_investigate = skill_config.get("auto_investigate", True)
    logger.info(
        "kubernetes: healing loop started — channel=%s interval=%dm cooldown=%dm auto_investigate=%s",
        alert_channel, poll_interval, cooldown_minutes, auto_investigate,
    )

    while True:
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
                    resp = client.chat_postMessage(channel=alert_channel, **payload)
                    thread_ts = resp["ts"]

                    # Auto-investigate in the alert thread
                    if auto_investigate and _engine is not None:
                        try:
                            _investigate_issue(issue, alert_channel, thread_ts, client)
                        except Exception:
                            logger.exception("kubernetes: investigation failed for %s/%s", issue["namespace"], issue["name"])

                except SlackApiError as e:
                    logger.warning("kubernetes: failed to post alert: %s", e)

        except Exception:
            logger.exception("kubernetes: error during healing loop scan")

        time.sleep(poll_interval * 60)


# ---------------------------------------------------------------------------
# BackgroundAdapter — used for auto-investigations (no human in the loop)
# ---------------------------------------------------------------------------

class _BackgroundAdapter:
    """Minimal adapter for background-triggered engine calls.

    Auto-denies all human approval requests so investigations stay read-only.
    The LLM can still recommend fixes in its text response.
    """

    def start(self, on_message=None):
        pass

    def stop(self):
        pass

    def send(self, message):
        pass

    def request_human_approval(self, request, channel_id):
        logger.debug("kubernetes: background adapter denied tool %s", request.tool_name)
        return False


# ---------------------------------------------------------------------------
# Late-bind hook — receives engine + adapter after full initialization
# ---------------------------------------------------------------------------

def bind(engine, adapter) -> None:
    """Receive the engine reference for background investigations."""
    global _engine
    _engine = engine
    logger.info("kubernetes: bind() received engine — auto-investigation enabled")


# ---------------------------------------------------------------------------
# Auto-investigation — runs after an alert is posted
# ---------------------------------------------------------------------------

def _investigate_issue(issue: dict, alert_channel: str, thread_ts: str, client) -> None:
    """Call the engine to investigate an issue and post findings as a thread reply."""
    if _engine is None:
        logger.debug("kubernetes: skipping investigation — engine not bound")
        return

    from mithai.adapters.base import IncomingMessage

    resource = f"{issue['namespace']}/{issue['name']}"
    reason = issue["reason"]
    restart_info = f" ({issue['restart_count']} restarts)" if issue.get("restart_count") else ""
    container_info = f", container: {issue['container']}" if issue.get("container") else ""
    message_info = f"\nError details: {issue['message']}" if issue.get("message") else ""

    prompt = (
        f"Investigate this Kubernetes issue: {issue['kind']} {resource}{container_info} — {reason}{restart_info}\n"
        f"{message_info}\n\n"
        f"Steps:\n"
        f"1. Call get_logs (with previous=true for CrashLoopBackOff/OOMKilled), describe_pod, and get_events for this resource\n"
        f"2. Analyze the data\n"
        f"3. Respond using EXACTLY this format (no other tools, no extra steps):\n\n"
        f"*Root Cause*\n"
        f"<1-2 sentence summary of why this is happening>\n\n"
        f"*Evidence*\n"
        f"<key log lines or events that confirm the cause — use `code blocks` for log snippets>\n\n"
        f"*Fix*\n"
        f"<numbered steps to resolve, with exact commands where applicable>\n\n"
        f"*Severity*: high/medium/low — <1 sentence justification>\n\n"
        f"IMPORTANT: Only use kubernetes tools (get_logs, describe_pod, get_events). Do NOT use shell, memory, or scan_cluster tools. Keep the response under 500 words."
    )

    msg = IncomingMessage(
        text=prompt,
        channel_id=alert_channel,
        user_id="healing-loop",
        platform="background",
        thread_id=thread_ts,
    )

    try:
        response = _engine.handle(msg, _BackgroundAdapter())
        # Format for Slack and post as thread reply
        from mithai.adapters.formatters import SlackFormatter
        from slack_sdk.errors import SlackApiError
        formatter = SlackFormatter()
        chunks = formatter.format(response)
        try:
            for chunk in chunks:
                client.chat_postMessage(
                    channel=alert_channel,
                    text=chunk,
                    thread_ts=thread_ts,
                )
        except SlackApiError as e:
            logger.warning("kubernetes: failed to post investigation reply: %s", e)
    except Exception:
        logger.exception("kubernetes: auto-investigation failed for %s", resource)


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
