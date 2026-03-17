---
title: "Examples"
description: "Three complete skills: dynamic approval, background polling with startup, and proactive alerts with bind."
---


Three complete skill examples, each highlighting a different framework pattern.

---


---

## Pattern 1: Read/write with dynamic approval

**The pattern:** Read operations auto-execute. Write operations need approval in production, auto-execute in staging.

This is the right pattern for most skills that touch real data.

**`skills/tickets/prompt.md`**
```markdown
You can look up and update support tickets.

Use `get_ticket` to fetch a ticket by ID.
Use `list_tickets` to search open tickets by status or assignee.
Use `update_ticket` to change a ticket's status or add a note.

Always fetch the ticket before updating it. Show the current state before proposing changes.
```

**`skills/tickets/tools.py`**
```python
"""Skill: Support ticket manager with environment-aware approval."""

import json
import urllib.request
import urllib.error

TOOLS = [
    {
        "name": "get_ticket",
        "description": "Fetch a ticket by ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id": {"type": "string", "description": "Ticket ID (e.g. 'TICKET-123')"},
            },
            "required": ["ticket_id"],
        },
        # No "human" key — auto-execute
    },
    {
        "name": "list_tickets",
        "description": "List tickets filtered by status and/or assignee.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status":   {"type": "string", "enum": ["open", "in_progress", "resolved"]},
                "assignee": {"type": "string", "description": "Username to filter by"},
            },
            "required": [],
        },
    },
    {
        "name": "update_ticket",
        "description": "Update a ticket's status or add a note. Requires approval in production.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticket_id":   {"type": "string"},
                "status":      {"type": "string", "enum": ["open", "in_progress", "resolved"]},
                "note":        {"type": "string", "description": "Comment to append"},
                "environment": {"type": "string", "enum": ["staging", "production"]},
            },
            "required": ["ticket_id"],
        },
        "human": "dynamic",
    },
]


def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    """Require approval for production writes; auto-execute everything else."""
    if name == "update_ticket":
        if input.get("environment", "production") == "production":
            return "approve"
        return None     # staging: auto-execute
    return None


def handle(name: str, input: dict, ctx: dict) -> str:
    config = ctx.get("config", {})
    base_url = config.get("api_url", "https://tickets.internal/api")

    if name == "get_ticket":
        return _api_get(f"{base_url}/tickets/{input['ticket_id']}")

    elif name == "list_tickets":
        params = {k: v for k, v in input.items() if v is not None}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return _api_get(f"{base_url}/tickets?{query}")

    elif name == "update_ticket":
        ticket_id = input.pop("ticket_id")
        input.pop("environment", None)
        return _api_patch(f"{base_url}/tickets/{ticket_id}", input)

    return json.dumps({"error": f"unknown tool: {name}"})


def _api_get(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code}", "url": url})
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


def _api_patch(url: str, body: dict) -> str:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PATCH",
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        return json.dumps({"error": f"HTTP {e.code}", "url": url})
    except Exception as e:
        return json.dumps({"error": str(e), "url": url})
```

**`config.yaml` block:**
```yaml
skills:
  config:
    tickets:
      api_url: https://tickets.internal/api
```

---

## Pattern 2: Background polling with `startup`

**The pattern:** A skill that runs a background loop when the agent starts — checking for conditions and writing results to memory so the agent can answer questions about them.

Use `startup` for anything that needs to run before the first message arrives: prefetching data, establishing connections, or kicking off a polling loop.

**`skills/alerts/prompt.md`**
```markdown
You can check for active infrastructure alerts and their current status.

Use `get_alerts` to list alerts that fired in the last hour.
Use `acknowledge_alert` to mark an alert as seen and add a note.

The background poller refreshes alert data every 60 seconds. Data may be up to 60 seconds stale.
```

**`skills/alerts/tools.py`**
```python
"""Skill: Alert poller — fetches alert state in background, exposes it to the agent."""

import json
import threading
import time
import urllib.request

TOOLS = [
    {
        "name": "get_alerts",
        "description": "List active alerts from the last hour.",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "enum": ["critical", "warning", "info"],
                    "description": "Filter by severity. Omit to get all.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "acknowledge_alert",
        "description": "Acknowledge an alert and add a note.",
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_id": {"type": "string"},
                "note":     {"type": "string", "description": "What you're doing about it"},
            },
            "required": ["alert_id"],
        },
        "human": "approve",
    },
]

_config: dict = {}
_cache: dict = {"alerts": [], "fetched_at": None}
_lock = threading.Lock()


def startup(config: dict) -> None:
    """Start background polling when the engine starts."""
    global _config
    _config = config
    interval = config.get("poll_interval", 60)
    t = threading.Thread(target=_poll_loop, args=(interval,), daemon=True)
    t.start()


def _poll_loop(interval: int) -> None:
    while True:
        try:
            _refresh_alerts()
        except Exception:
            pass  # don't crash the background thread
        time.sleep(interval)


def _refresh_alerts() -> None:
    url = _config.get("alerts_url", "https://alerts.internal/api/v1/alerts")
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    with _lock:
        _cache["alerts"] = data.get("alerts", [])
        _cache["fetched_at"] = time.time()


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "get_alerts":
        with _lock:
            alerts = _cache["alerts"]
            fetched_at = _cache["fetched_at"]

        severity = input.get("severity")
        if severity:
            alerts = [a for a in alerts if a.get("severity") == severity]

        age_s = round(time.time() - fetched_at) if fetched_at else None
        return json.dumps({
            "alerts": alerts,
            "count": len(alerts),
            "data_age_seconds": age_s,
        })

    elif name == "acknowledge_alert":
        url = _config.get("alerts_url", "https://alerts.internal/api/v1/alerts")
        data = json.dumps({
            "alert_id": input["alert_id"],
            "note": input.get("note", ""),
            "acknowledged_by": ctx.get("user_id", "unknown"),
        }).encode()
        req = urllib.request.Request(
            f"{url}/{input['alert_id']}/ack",
            data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode()

    return json.dumps({"error": f"unknown tool: {name}"})
```

:::note[Note]
`startup` runs once at engine init, not per message. The background thread is a daemon — it exits when the main process exits. Use `_lock` when sharing state between the polling thread and `handle`.
:::

**`config.yaml` block:**
```yaml
skills:
  config:
    alerts:
      alerts_url: https://alerts.internal/api/v1/alerts
      poll_interval: 60    # seconds between polls
```

---

## Pattern 3: Proactive alerts with `bind`

**The pattern:** A skill that sends messages to Slack unprompted — without a user asking first. Use `bind` to get a reference to the adapter after the engine is ready, then call it from a background thread when something needs attention.

:::note[Note]
This pattern requires the Slack adapter. It won't send messages when running under the CLI adapter.
:::

**`skills/watchdog/prompt.md`**
```markdown
You can check on the watchdog monitor status.

Use `get_watchdog_status` to see what the watchdog is currently monitoring and its last check times.
Use `set_alert_channel` to configure which Slack channel receives watchdog alerts.

The watchdog runs checks in the background and posts to Slack automatically when a check fails.
```

**`skills/watchdog/tools.py`**
```python
"""Skill: Watchdog — monitors endpoints and proactively alerts in Slack."""

import json
import threading
import time
import urllib.request
import urllib.error

TOOLS = [
    {
        "name": "get_watchdog_status",
        "description": "Show what the watchdog monitors and its last check results.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "set_alert_channel",
        "description": "Set the Slack channel for watchdog alerts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Slack channel ID (e.g. C0123ABCDEF)"},
            },
            "required": ["channel_id"],
        },
        "human": "approve",
    },
]

_config: dict = {}
_adapter = None
_status: dict = {}


def bind(engine, adapter) -> None:
    """Called after engine and adapters are initialized. Store adapter reference."""
    global _adapter
    _adapter = adapter


def startup(config: dict) -> None:
    """Start background monitoring."""
    global _config
    _config = config
    interval = config.get("check_interval", 30)
    t = threading.Thread(target=_monitor_loop, args=(interval,), daemon=True)
    t.start()


def _monitor_loop(interval: int) -> None:
    while True:
        endpoints = _config.get("endpoints", [])
        for ep in endpoints:
            name = ep["name"]
            url  = ep["url"]
            ok, status = _check(url)
            was_ok = _status.get(name, {}).get("healthy", True)
            _status[name] = {"healthy": ok, "status": status, "checked_at": time.time()}

            # Alert if status just changed to unhealthy
            if not ok and was_ok:
                _send_alert(f":red_circle: *{name}* is down — {status}")
        time.sleep(interval)


def _check(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return 200 <= resp.status < 400, f"HTTP {resp.status}"
    except urllib.error.URLError as e:
        return False, str(e.reason)
    except Exception as e:
        return False, str(e)


def _send_alert(text: str) -> None:
    if _adapter is None:
        return
    channel = _config.get("alert_channel")
    if not channel:
        return
    try:
        from mithai.adapters.base import OutgoingMessage
        _adapter.send(OutgoingMessage(channel_id=channel, text=text))
    except Exception:
        pass


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "get_watchdog_status":
        return json.dumps({
            "monitoring": list(_status.keys()),
            "checks": _status,
            "alert_channel": _config.get("alert_channel"),
        })

    elif name == "set_alert_channel":
        _config["alert_channel"] = input["channel_id"]
        return json.dumps({"alert_channel": input["channel_id"], "updated": True})

    return json.dumps({"error": f"unknown tool: {name}"})
```

**`config.yaml` block:**
```yaml
skills:
  config:
    watchdog:
      check_interval: 30      # seconds between checks
      alert_channel: C0123ABCDEF   # Slack channel ID
      endpoints:
        - name: checkout
          url: https://checkout.internal/health
        - name: billing
          url: https://billing.internal/health
```

When the watchdog detects a newly-failing endpoint it sends a message to the configured channel without any user prompt:

```
🔴 checkout is down — Connection refused
```

:::tip[Tip]
`bind` receives the adapter currently active. In multi-adapter mode, it receives the first adapter. If you need to send to Slack specifically, check `type(_adapter).__name__ == "SlackAdapter"` before calling `send`.
:::

---

← [Troubleshooting](/troubleshooting/)
