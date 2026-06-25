---
title: "Skills reference"
description: "Complete reference for TOOLS, handle, resolve_human, startup, bind, MCP_TOOLS, VERIFY, and the ctx object."
---


A skill is a folder with a `prompt.md` and a `tools.py`. This page is the complete reference for everything a skill can export and everything the framework gives it.

---

## On this page

- [File structure](#file-structure)
- [prompt.md](#promptmd)
- [tools.py exports](#toolspy-exports)
  - [TOOLS](#tools-required)
  - [handle](#handlename-input-ctx-required)
  - [resolve_human](#resolve_humanname-input-ctx-optional)
  - [startup](#startupconfig-optional)
  - [bind](#bindengine-adapter-optional)
  - [MCP_TOOLS](#mcp_tools-optional)
  - [VERIFY](#verify-optional)
- [The ctx object](#the-ctx-object)
- [Built-in skills](#built-in-skills)
- [Configuration overrides](#configuration-overrides)
- [Loading skills](#loading-skills)

---

## File structure

```
skills/
└── my_skill/
    ├── prompt.md     # required: injected into the system prompt
    └── tools.py      # required: tool definitions and handlers
```

Both files are required. If either is missing, the skill fails to load.

---

## `prompt.md`

Plain markdown. Loaded at startup and injected into the LLM's system prompt alongside all other skill prompts.

Write it from the AI's perspective. Describe:

- What this skill can do
- When to use each tool
- Rules or constraints the AI should follow for this skill

**Example:**

```markdown
You can check service health and trigger restarts.

Use `list_services` to enumerate available services.
Use `check_health` to test an endpoint before reporting on it.
Use `restart_service` when a service is unhealthy — this requires human approval in production.

Always verify health before recommending a restart.
Report response times alongside status codes.
```

---

## `tools.py` exports

### `TOOLS` (required)

A list of tool definitions in [Anthropic tool schema format](https://docs.anthropic.com/en/docs/tool-use).

```python
TOOLS = [
    {
        "name": "check_health",                     # snake_case, unique within the skill
        "description": "Check whether a service endpoint is responding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to check",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Request timeout in milliseconds. Default: 5000",
                },
            },
            "required": ["url"],
        },
        "human": "approve",     # optional: approval level (see below)
    },
]
```

**`"human"` values:**

| Value | Behavior |
|---|---|
| omitted or `null` | Auto-execute |
| `"approve"` | Pause for Approve/Deny |
| `"confirm"` | Require typing a confirmation |
| `"dynamic"` | Defer to `resolve_human` at runtime |

Tool names are automatically prefixed: a tool named `check_health` in a skill named `services` is exposed to the LLM as `services__check_health`.

---

### `handle(name, input, ctx)` (required)

Called when the LLM invokes a tool. Must return a JSON-serializable string.

```python
def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "check_health":
        url = input["url"]
        # ... do work ...
        return json.dumps({"status": 200, "healthy": True})

    return json.dumps({"error": f"unknown tool: {name}"})
```

**Parameters:**

- `name` — the tool name (without the skill prefix)
- `input` — dict of inputs as provided by the LLM, validated against the schema
- `ctx` — the context dict (see [The `ctx` object](#the-ctx-object) below)

**Return value:** A JSON string. The LLM reads this as the tool's result. Return structured data where possible — it's easier for the LLM to reason about than free text.

Always handle the unknown-tool case. Return an error JSON rather than raising an exception.

---

### `resolve_human(name, input, ctx)` (optional)

Called before executing a tool to determine the approval level at runtime. Takes precedence over the static `"human"` key on the tool definition.

```python
def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    if name == "restart_service":
        if input.get("environment") == "production":
            return "approve"
        return None   # staging: auto-execute
    return None       # everything else: auto-execute
```

**Return values:**

| Value | Behavior |
|---|---|
| `None` | Auto-execute |
| `"approve"` | Pause for Approve/Deny |
| `"confirm"` | Require typing a confirmation |

Use `resolve_human` when the approval level depends on the specific inputs, not just the tool name. Common patterns:

- Require approval only in production environments
- Require approval only above a certain threshold (e.g., scaling to >10 replicas)
- Allow known-safe values through automatically, flag unknown ones

---

### `startup(config)` (optional)

Called once when the engine starts, before any messages are processed. Use it for:

- Starting background polling loops
- Establishing persistent connections
- Validating configuration and failing fast if something is missing

```python
import threading

def startup(config: dict) -> None:
    interval = config.get("poll_interval", 60)
    t = threading.Thread(target=_poll_loop, args=(interval,), daemon=True)
    t.start()
```

`config` is the skill's configuration block from `config.yaml` (the same as `ctx["config"]` in handlers).

---

### `bind(engine, adapter)` (optional)

Called after both the engine and adapters are fully initialized. Use it when your skill needs a reference to the engine or to adapter-specific APIs.

```python
_engine = None
_adapter = None

def bind(engine, adapter) -> None:
    global _engine, _adapter
    _engine = engine
    _adapter = adapter
```

This is an escape hatch for advanced integrations (e.g., a skill that proactively sends messages when it detects an alert). Most skills don't need it.

---

### `MCP_TOOLS` (optional)

Declares which external MCP (Model Context Protocol) servers this skill uses. The framework starts only the MCP servers that are actually needed.

```python
MCP_TOOLS = [
    {
        "server": "linear",
        "tools": ["create_issue", "list_issues", "update_issue"],
    },
]
```

MCP servers are declared in `config.yaml` under `mcp_servers`. Tools from MCP servers are namespaced the same way as native tools.

---

### `VERIFY` (optional)

Set to `True` to enable post-turn fact-checking for this skill. After each turn where a verified skill was called, a secondary LLM call checks the agent's response against what the tools actually returned, and annotates the response with ⚠️ if a numerical or factual contradiction is found.

```python
VERIFY = True
```

Use this for skills that query external systems with precise values (counts, sizes, versions, costs) where hallucinated summaries would be harmful. The bundled `kubernetes` skill enables this because cluster status answers should be checked against actual tool output.

To use a cheaper model for the fact-check, configure it in `config.yaml`:

```yaml
verifier:
  model: claude-haiku-4-5
```

---

## The `ctx` object

Every call to `handle` and `resolve_human` receives a `ctx` dict with the following keys:

### `ctx["config"]`

The skill's configuration block from `config.yaml`. Use `.get()` with defaults — the block may be empty if the user hasn't configured the skill.

```yaml
# config.yaml
skills:
  config:
    my_skill:
      api_url: https://api.internal
      timeout: 30
```

```python
def handle(name, input, ctx):
    config = ctx.get("config", {})
    url = config.get("api_url", "https://api.example.com")
    timeout = config.get("timeout", 10)
```

### `ctx["memory"]`

The memory backend. Persists across restarts and sessions.

```python
memory = ctx.get("memory")
if memory:
    # Read a file
    content = memory.read("playbooks/restart.md")

    # Write or append to a file
    memory.write("incidents.md", "- pod OOMKilled at 14:23\n", append=True)

    # Read a JSON file as a dict
    approvals = memory.read_json("approvals.json") or {}
```

Files are stored in `memory/` (configurable). Paths are relative to that root.

### `ctx["state"]`

Key-value store for ephemeral state within a session. Also persisted to disk.

```python
state = ctx.get("state")
if state:
    state.set("last_checked", "2026-03-16T14:23:00Z")
    value = state.get("last_checked")
```

### `ctx["channel_id"]`

String. The channel or chat the message came from. In Slack: the channel ID. In Telegram: the chat ID. In the CLI: `"cli"`.

### `ctx["user_id"]`

String. The user who sent the message. In Slack: the user's Slack ID. In Telegram: the user's Telegram ID. May be `None` if not available.

### `ctx["logger"]`

Structured logger.

```python
logger = ctx.get("logger")
if logger:
    logger.info("checking health", url=url)
    logger.warning("slow response", url=url, ms=elapsed_ms)
    logger.error("request failed", url=url, error=str(e))
```

---

## Built-in skills

These skills ship with mithai and are available immediately after `mithai init`.

### `shell`

Run shell commands on the host. Uses dynamic approval — commands on the allowlist auto-execute, new commands require approval, and commands accumulate approvals until they auto-promote.

**Tools:** `run_command(command)`

**Approval:** Dynamic. Allowlisted commands: auto. New commands: approve. Auto-promoted commands: auto.

**Config:**

```yaml
skills:
  config:
    shell:
      allowed_commands:
        - "df -h"
        - "free -h"
        - "uptime"
      approval_auto_promote: 3    # number of approvals before auto-exec
      timeout: 30                 # seconds before command is killed
```

Shell operators (`|`, `&&`, `>`, `;`) are detected automatically and run with `shell=True`. All commands run with `stdin=DEVNULL` to prevent interactive hangs.

---

### `memory`

Read and write persistent files. The agent uses this to record facts, save playbooks, and look up past decisions.

**Tools:** `memory_read(path)`, `memory_write(path, content, mode)`, `memory_search(query)`

**Approval:** Auto for all operations.

`memory/MEMORY.md` is automatically injected into every conversation. Write facts there that the agent should always have available.

---

### `http_checker`

Check whether HTTP endpoints are reachable.

**Tools:** `check_url(url)`

**Approval:** Auto.

**Returns:** `{ url, status, response_time_ms, healthy }`

---

### `kubernetes`

Inspect Kubernetes clusters through read-only `kubectl` commands.

**Tools:** `get_pods(namespace, all_namespaces)`, `get_deployments(namespace, all_namespaces)`, `get_events(namespace, all_namespaces)`, `get_logs(pod, namespace, container, previous, tail_lines)`, `describe_resource(resource_type, name, namespace)`

**Approval:** Auto for all operations. The bundled skill does not create, update, restart, scale, or delete resources.

**Requires:** `kubectl` on `PATH` and access to the target cluster.

**Config:**

```yaml
skills:
  config:
    kubernetes:
      default_namespace: default
      context: ""
      kubeconfig: ""
      timeout: 30
```

`context` and `kubeconfig` are passed as structured `kubectl` arguments, not through a shell.

---

## Configuration overrides

Any tool's approval level can be overridden in `config.yaml` without modifying the skill:

```yaml
human:
  timeout_seconds: 300    # how long to wait for approval before timing out
  overrides:
    shell__run_command: confirm       # escalate: require typing
    kubernetes__get_pods: null        # keep read-only pod listing auto-executed
    services__restart_service: null   # trust this operation completely
```

Key format: `skillname__toolname`.

---

## Loading skills

By default, mithai loads skills from `./skills`. Add additional paths in `config.yaml`:

```yaml
skills:
  paths:
    - ./skills
    - ./custom_skills
    - /opt/shared/mithai-skills
```

Skills are loaded in order. If two skills define a tool with the same prefixed name, the last one wins.

```bash
mithai skill list       # list loaded skills (and their tools)
mithai skill validate   # validate all skills without starting the engine
```

---

← [Core concepts](concepts.md) | [Configuration →](configuration.md)
