---
title: "Your first skill"
description: "Build a working skill from scratch — service health checks with human-in-the-loop approval."
---


In this tutorial you'll build a **service health checker** — a skill that reports on whether your team's endpoints are up, and requires approval before triggering a restart.

---

## On this page

- [What we're building](#what-were-building)
- [Step 1: Create the skill folder](#step-1-create-the-skill-folder)
- [Step 2: Write SKILL.md](#step-2-write-skillmd)
- [Step 3: Implement tools.py](#step-3-implement-toolspy)
- [Step 4: Configure and test](#step-4-configure-and-test)
- [Step 5: Make approval smarter with resolve_human](#step-5-make-approval-smarter-with-resolve_human)
- [Step 6: Use skill config for the service list](#step-6-use-skill-config-for-the-service-list)
- [Step 7: Write to memory](#step-7-write-to-memory)
- [The complete skill](#the-complete-skill)
- [Anatomy of a skill call](#anatomy-of-a-skill-call)
- [What's next](#whats-next)

Along the way you'll learn:

- The skill file structure (`SKILL.md` + `tools.py`)
- How to define tools with input schemas
- How to implement the `handle` function
- How to require human approval for sensitive actions
- How to use `resolve_human` for runtime approval decisions
- How to read per-skill configuration from `config.yaml`
- How the `ctx` object gives tools access to memory, state, and config

By the end, you'll have a fully working skill and a clear mental model for building any skill you need.

> **Before you begin**
> - mithai is installed and `mithai chat` works ([Getting started](getting-started.md))
> - You have a project directory with a `config.yaml` and a `skills/` folder (`mithai init`)
> - You're comfortable with Python — no advanced knowledge needed

---

## What we're building

A `services` skill with three tools:

| Tool | What it does | Approval |
|---|---|---|
| `list_services` | List the services defined in config | Auto |
| `check_health` | HTTP health check on a service URL | Auto |
| `restart_service` | Trigger a restart | **Approve** |

You'll ask the agent: *"Are all services healthy?"* and it will check each one and summarize. If you ask it to restart something, it will pause and show you what it's about to do.

---

## Step 1: Create the skill folder

```bash
mithai skill create services
```

This creates:

```
skills/
└── services/
    ├── SKILL.md
    └── tools.py
```

You can also create these files manually — there's no magic here.

---

## Step 2: Write `SKILL.md`

`SKILL.md` is read by the agent at startup and injected into its system prompt. It tells the AI what this skill can do and when to use it.

**`skills/services/SKILL.md`**

```markdown
You can check the health of services and restart them.

Use `list_services` to see what services are configured.
Use `check_health` to test whether a service endpoint is responding.
Use `restart_service` when a service needs to be restarted — this requires human approval.

Always check health before suggesting a restart.
Report response times and status codes clearly.
```

Keep `SKILL.md` focused on behavior: what tools exist, when to use them, and any rules or constraints the AI should follow for this skill.

---

## Step 3: Implement `tools.py`

`tools.py` has two required parts:

1. **`TOOLS`** — a list of tool definitions. This is what gets sent to the LLM so it knows what it can call.
2. **`handle(name, input, ctx)`** — a function that receives the tool call and returns a result.

### Start with the basics

**`skills/services/tools.py`**

```python
import json
import time
import urllib.request
import urllib.error


TOOLS = [
    {
        "name": "list_services",
        "description": "List all configured services and their health check URLs.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_health",
        "description": "Check whether a service is healthy by calling its health endpoint.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "The service name (e.g. 'checkout', 'billing')",
                },
                "url": {
                    "type": "string",
                    "description": "The URL to check (e.g. 'https://checkout.internal/health')",
                },
            },
            "required": ["service", "url"],
        },
    },
    {
        "name": "restart_service",
        "description": "Restart a service. Use this when a service is unhealthy and needs recovery.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "The service name to restart",
                },
                "environment": {
                    "type": "string",
                    "description": "The environment: 'staging' or 'production'",
                    "enum": ["staging", "production"],
                },
            },
            "required": ["service", "environment"],
        },
        "human": "approve",   # <-- pause and ask before running this
    },
]
```

> **The `"human"` key** controls whether this tool requires approval. `"approve"` shows the user the tool name and inputs with an Approve/Deny button. `"confirm"` requires typing a confirmation string. Omit it (or set to `null`) for auto-execute.

### Implement `handle`

```python
def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "list_services":
        return _list_services(ctx)
    elif name == "check_health":
        return _check_health(input["service"], input["url"])
    elif name == "restart_service":
        return _restart_service(input["service"], input["environment"], ctx)
    return json.dumps({"error": f"unknown tool: {name}"})
```

`handle` is your router. Return a JSON string for every branch — the LLM reads this result and incorporates it into its response.

### Implement each tool

```python
def _list_services(ctx: dict) -> str:
    config = ctx.get("config", {})
    services = config.get("services", {})
    if not services:
        return json.dumps({"message": "No services configured. Add them under skills.config.services in config.yaml."})
    return json.dumps({"services": services})


def _check_health(service: str, url: str) -> str:
    start = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            elapsed_ms = round((time.time() - start) * 1000)
            return json.dumps({
                "service": service,
                "url": url,
                "status": resp.status,
                "response_ms": elapsed_ms,
                "healthy": 200 <= resp.status < 400,
            })
    except urllib.error.URLError as e:
        elapsed_ms = round((time.time() - start) * 1000)
        return json.dumps({
            "service": service,
            "url": url,
            "error": str(e.reason),
            "response_ms": elapsed_ms,
            "healthy": False,
        })
    except Exception as e:
        return json.dumps({"service": service, "url": url, "error": str(e), "healthy": False})


def _restart_service(service: str, environment: str, ctx: dict) -> str:
    # In a real skill, this would call your deployment API, kubectl, etc.
    # For this tutorial, we just return a success message.
    return json.dumps({
        "restarted": True,
        "service": service,
        "environment": environment,
        "message": f"Restart initiated for {service} in {environment}.",
    })
```

`ctx.get("config", {})` gives you the skill-specific config block from `config.yaml` (more on this in Step 5). If the key is missing, you get an empty dict — always safe to use `.get()` with a default.

---

## Step 4: Configure and test

### Add services to `config.yaml`

```yaml
skills:
  paths:
    - ./skills
  config:
    services:
      services:
        checkout:
          url: https://httpbin.org/status/200    # use a real URL in practice
          environment: production
        billing:
          url: https://httpbin.org/status/200
          environment: production
        auth:
          url: https://httpbin.org/status/503   # simulate an unhealthy service
          environment: production
```

### Start the agent

```bash
mithai chat
```

### Try it

```
> are all services healthy?
```

The agent calls `list_services`, then calls `check_health` for each one, and summarizes:

```
checkout: healthy (200 OK, 44ms)
billing:  healthy (200 OK, 61ms)
auth:     unhealthy (503, 38ms)

The auth service is returning 503. Would you like me to restart it?
```

```
> yes, restart auth in production
```

Because `restart_service` has `"human": "approve"`, the agent pauses:

```
  Tool: services__restart_service
  service: auth
  environment: production

  [Approve]  [Deny]
```

Type `approve` (or press the button in Slack). The agent runs the tool and reports back.

---

## Step 5: Make approval smarter with `resolve_human`

Static approval levels are a good starting point, but sometimes the right level depends on *what the tool is doing*. Restarting a staging service is low-stakes. Restarting production is not.

Add `resolve_human` to `tools.py`:

```python
def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    """Decide the approval level at runtime based on what's being done."""
    if name == "restart_service":
        if input.get("environment") == "production":
            return "approve"     # always ask for production
        return None              # staging: run automatically
    return None
```

When `resolve_human` is present, it overrides the static `"human"` key for that tool call. Return:
- `None` — auto-execute
- `"approve"` — show approve/deny button
- `"confirm"` — require typing a confirmation string

Remove the `"human": "approve"` from the `restart_service` tool definition, since the runtime function now handles it:

```python
{
    "name": "restart_service",
    "description": "Restart a service. Use this when a service is unhealthy and needs recovery.",
    "input_schema": { ... },
    # "human" key removed — resolve_human handles this now
},
```

Now `restart_service` in staging runs automatically, but production always asks. The AI doesn't need to know this distinction — it just calls the tool, and the framework handles the routing.

---

## Step 6: Use skill config for the service list

Rather than hardcoding service URLs, read them from `ctx["config"]`. You already did this in `_list_services`. Let's extend the pattern so `check_health` can look up URLs by name:

```python
def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "list_services":
        return _list_services(ctx)

    elif name == "check_health":
        service = input["service"]
        url = input.get("url")

        # If no URL provided, try to look it up from config
        if not url:
            config = ctx.get("config", {})
            service_config = config.get("services", {}).get(service)
            if service_config:
                url = service_config.get("url")
        if not url:
            return json.dumps({"error": f"No URL provided and '{service}' not found in config"})

        return _check_health(service, url)

    elif name == "restart_service":
        return _restart_service(input["service"], input["environment"], ctx)

    return json.dumps({"error": f"unknown tool: {name}"})
```

Now users can say: *"check the auth service"* and the agent knows the URL without asking.

---

## Step 7: Write to memory

The `ctx["memory"]` object gives you access to persistent storage that survives restarts and persists across conversations. Use it to record facts the agent should remember.

```python
def _restart_service(service: str, environment: str, ctx: dict) -> str:
    # ... perform the restart ...

    # Record this in memory so the agent can reference it later
    memory = ctx.get("memory")
    if memory:
        memory.write(
            "restarts.md",
            f"- Restarted {service} ({environment}) — approver: {ctx.get('user_id', 'unknown')}\n",
            append=True,
        )

    return json.dumps({
        "restarted": True,
        "service": service,
        "environment": environment,
    })
```

Later the agent can answer: *"When was auth last restarted?"* because the fact is in `memory/restarts.md`.

---

## The complete skill

Here's the final `tools.py` with everything in one place:

```python
"""Skill: Service health checker with approval-gated restarts."""

import json
import time
import urllib.request
import urllib.error


TOOLS = [
    {
        "name": "list_services",
        "description": "List all configured services and their health check URLs.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_health",
        "description": "Check whether a service is healthy by calling its health endpoint.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name (e.g. 'checkout', 'billing')",
                },
                "url": {
                    "type": "string",
                    "description": "URL to check. Optional if service is defined in config.",
                },
            },
            "required": ["service"],
        },
    },
    {
        "name": "restart_service",
        "description": "Restart a service. Always check health first. Requires approval in production.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "Service name to restart",
                },
                "environment": {
                    "type": "string",
                    "description": "Target environment",
                    "enum": ["staging", "production"],
                },
            },
            "required": ["service", "environment"],
        },
    },
]


def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    """Require approval only for production restarts."""
    if name == "restart_service" and input.get("environment") == "production":
        return "approve"
    return None


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "list_services":
        return _list_services(ctx)

    elif name == "check_health":
        service = input["service"]
        url = input.get("url") or _lookup_url(service, ctx)
        if not url:
            return json.dumps({"error": f"No URL for '{service}'. Provide a url or add it to config."})
        return _check_health(service, url)

    elif name == "restart_service":
        return _restart_service(input["service"], input["environment"], ctx)

    return json.dumps({"error": f"unknown tool: {name}"})


# ── implementations ──────────────────────────────────────────────────────────

def _list_services(ctx: dict) -> str:
    services = ctx.get("config", {}).get("services", {})
    if not services:
        return json.dumps({"message": "No services configured in config.yaml."})
    return json.dumps({"services": services})


def _lookup_url(service: str, ctx: dict) -> str | None:
    services = ctx.get("config", {}).get("services", {})
    return services.get(service, {}).get("url")


def _check_health(service: str, url: str) -> str:
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            elapsed_ms = round((time.time() - start) * 1000)
            return json.dumps({
                "service": service,
                "status": resp.status,
                "response_ms": elapsed_ms,
                "healthy": 200 <= resp.status < 400,
            })
    except urllib.error.URLError as e:
        return json.dumps({
            "service": service,
            "error": str(e.reason),
            "response_ms": round((time.time() - start) * 1000),
            "healthy": False,
        })
    except Exception as e:
        return json.dumps({"service": service, "error": str(e), "healthy": False})


def _restart_service(service: str, environment: str, ctx: dict) -> str:
    # Replace with your actual restart mechanism (kubectl, API call, etc.)
    memory = ctx.get("memory")
    if memory:
        memory.write(
            "restarts.md",
            f"- Restarted {service} ({environment})\n",
            append=True,
        )
    return json.dumps({
        "restarted": True,
        "service": service,
        "environment": environment,
    })
```

And the matching `config.yaml` block:

```yaml
skills:
  paths:
    - ./skills
  config:
    services:
      services:
        checkout:
          url: https://checkout.internal/health
        billing:
          url: https://billing.internal/health
        auth:
          url: https://auth.internal/health
```

---

## Anatomy of a skill call

Here's what happens end-to-end when you ask: *"restart auth in production"*

```
   User           Adapter            Engine          resolve_human      handle()          LLM
    │                │                  │                  │               │               │
    │ "restart auth  │                  │                  │               │               │
    │  in production"│                  │                  │               │               │
    ├───────────────►│                  │                  │               │               │
    │                │ engine.handle()  │                  │               │               │
    │                ├─────────────────►│                  │               │               │
    │                │                  │  Claude call     │               │               │
    │                │                  │  (history +      │               │               │
    │                │                  │   tools)         │               │               │
    │                │                  ├────────────────────────────────────────────────►│
    │                │                  │                  │               │               │
    │                │                  │◄────────────────────────────────────────────────┤
    │                │                  │  tool call:      │               │               │
    │                │                  │  services__restart_service       │               │
    │                │                  │                  │               │               │
    │                │                  │ resolve_human()  │               │               │
    │                │                  ├─────────────────►│               │               │
    │                │                  │                  │               │               │
    │                │                  │◄─────────────────┤               │               │
    │                │                  │  returns "approve"               │               │
    │                │                  │  (production env)                │               │
    │                │  approval request│                  │               │               │
    │                │◄─────────────────┤                  │               │               │
    │  "Approve /    │                  │                  │               │               │
    │   Deny?"       │                  │                  │               │               │
    │◄───────────────┤                  │                  │               │               │
    │                │                  │                  │               │               │
    │   approve      │                  │                  │               │               │
    ├───────────────►│                  │                  │               │               │
    │                │ approved         │                  │               │               │
    │                ├─────────────────►│                  │               │               │
    │                │                  │ handle()         │               │               │
    │                │                  ├──────────────────────────────────►               │
    │                │                  │                  │               │               │
    │                │                  │◄──────────────────────────────────               │
    │                │                  │  JSON result     │               │               │
    │                │                  │                  │               │               │
    │                │                  │  Claude call (with tool result)  │               │
    │                │                  ├────────────────────────────────────────────────►│
    │                │                  │◄────────────────────────────────────────────────┤
    │                │                  │  final text response             │               │
    │                │ send response    │                  │               │               │
    │                │◄─────────────────┤                  │               │               │
    │   response     │                  │                  │               │               │
    │◄───────────────┤                  │                  │               │               │
```

The `services__` prefix is added automatically. All tool names are namespaced as `skillname__toolname` to prevent collisions between skills.

---

## What's next

- **[Core concepts →](concepts.md)** — Go deeper on skills, Human MCP approval levels, sessions, memory, and adapters.
- **[Skills reference →](skills-reference.md)** — All the hooks a skill can export: `handle`, `resolve_human`, `startup`, `bind`, `MCP_TOOLS`.
- **[Configuration reference →](configuration.md)** — Every option in `config.yaml`.

---

← [Getting started](getting-started.md) | [Core concepts →](concepts.md)
