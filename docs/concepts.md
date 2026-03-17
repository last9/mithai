---
title: "Core concepts"
description: "How skills, adapters, Human MCP, sessions, and memory fit together."
---


This page explains the core ideas behind mithai. Read it after the [getting started guide](getting-started.md) and [your first skill tutorial](your-first-skill.md).

---

## On this page

- [Skills](#skills)
- [Human MCP](#human-mcp)
- [Adapters](#adapters)
- [Sessions](#sessions)
- [Memory](#memory)
- [How a request flows](#how-a-request-flows)
- [Multi-agent mode](#multi-agent-mode)

---

## Skills

A skill is the unit of extension in mithai. It's a folder with two files:

```
skills/
└── my_skill/
    ├── prompt.md    # what the AI knows about this skill
    └── tools.py     # what the AI can do with this skill
```

### `prompt.md`

Loaded into the system prompt at startup. Tells the AI what the skill does, when to use it, and any constraints.

```markdown
You can check service health and restart services.
Always check health before recommending a restart.
Report response times and status codes clearly.
```

Keep this focused. The AI doesn't need implementation details — just behavior.

### `tools.py`

Defines tools (exposed to the LLM) and implements them (runs on the server).

**Required exports:**

- **`TOOLS`** — list of tool definitions in Anthropic tool schema format
- **`handle(name, input, ctx)`** — receives tool calls, returns a JSON string

**Optional exports:**

- **`resolve_human(name, input, ctx)`** — runtime approval decision (see [Human MCP](#human-mcp) below)
- **`startup(config)`** — called once when the engine starts; use for background loops or connection setup
- **`bind(engine, adapter)`** — called after engine and adapters are initialized; gives the skill access to both
- **`MCP_TOOLS`** — declares which external MCP servers this skill needs

### Tool naming

Tools are automatically namespaced as `skillname__toolname`. A tool named `check_health` in a skill named `services` becomes `services__check_health` in the LLM's tool list. This prevents collisions between skills.

```
skills/
└── services/               ← skill name
    ├── prompt.md           ← injected into system prompt
    └── tools.py            ← exports TOOLS + handle()
         │
         ├── check_health   ← tool name defined in TOOLS
         └── restart_service
              │
              ▼  namespaced automatically by the engine
         services__check_health
         services__restart_service
```

### The `ctx` object

Every call to `handle` and `resolve_human` receives a `ctx` dict:

```python
ctx = {
    "config":     dict,          # the skill's config block from config.yaml
    "state":      StateBackend,  # key-value store (persists across restarts)
    "memory":     MemoryBackend, # file-based storage (markdown, JSON)
    "channel_id": str,           # where the message came from
    "user_id":    str,           # who sent the message
    "logger":     Logger,        # structured logger
}
```

`ctx["config"]` is the most commonly used — it gives you the `skills.config.<skillname>` block from `config.yaml`.

---

## Human MCP

Human MCP (Model Context Protocol) is how mithai keeps humans in the loop for sensitive actions. Every tool has an approval level.

### Approval levels

| Level | Behavior |
|---|---|
| `null` (default) | Auto-execute. No approval needed. |
| `"approve"` | Show the tool name and inputs. User clicks Approve or Deny. |
| `"confirm"` | Higher friction. User must type a confirmation string. |
| `"dynamic"` | The skill's `resolve_human` function decides at runtime. |

Set the level on the tool definition:

```python
TOOLS = [
    {"name": "list_pods", ...},                          # auto-execute
    {"name": "restart", ..., "human": "approve"},        # button
    {"name": "delete_namespace", ..., "human": "confirm"}, # type to confirm
    {"name": "run_command", ..., "human": "dynamic"},    # resolve_human decides
]
```

### `resolve_human`

When `"human": "dynamic"` is set, or when you want to override the static level at runtime, export `resolve_human` from `tools.py`:

```python
def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    if name == "restart":
        # Production requires approval; staging is fine
        if input.get("environment") == "production":
            return "approve"
        return None   # auto-execute
    return None
```

`resolve_human` takes precedence over the static `"human"` key when present.

### Overrides in `config.yaml`

You can override any tool's approval level without touching the skill code:

```yaml
human:
  timeout_seconds: 300
  overrides:
    shell__run_command: confirm      # escalate: require typing
    kubernetes__get_pods: null       # de-escalate: run automatically
```

The key format is `skillname__toolname`.

### What users see

In **Slack**: an inline message in the thread with the tool name, the inputs, and Approve/Deny buttons.

In the **terminal**: a text prompt showing the same information. Type `approve` or `deny`.

In **Telegram**: an inline keyboard with approval buttons.

Approval is scoped to the user who made the request. The response routes back through the same adapter that received the original message.

---

## Adapters

An adapter connects mithai to a communication platform. The same engine and skills run behind every adapter.

```
┌─────────┐   ┌──────────┐   ┌─────┐
│  Slack  │   │ Telegram │   │ CLI │
└────┬────┘   └─────┬────┘   └──┬──┘
     │               │           │
     └───────────────┼───────────┘
                     │
              ┌──────┴──────┐
              │    Engine   │
              │  (shared)   │
              └──────┬──────┘
         ┌───────────┼───────────┐
    ┌────┴────┐ ┌────┴────┐ ┌───┴──────┐
    │ Skills  │ │  Human  │ │  Memory  │
    │         │ │   MCP   │ │  State   │
    └─────────┘ └─────────┘ └──────────┘
```

Human MCP approval requests always route back through the same adapter that received the original message — Slack approvals stay in Slack, CLI prompts stay in the terminal.

### Slack

Real-time via Socket Mode (no public webhook needed). Each Slack thread is an independent session — the agent has full conversation history within a thread, but a fresh context in a new one. Approvals appear as interactive button messages.

```yaml
adapter:
  type: slack
  slack:
    bot_token: ${SLACK_BOT_TOKEN}    # xoxb-...
    app_token: ${SLACK_APP_TOKEN}    # xapp-...
    respond: mentions                # "mentions" or "all"
```

**Thread context**: When you @mention the bot in a thread it didn't start, it fetches prior messages in that thread for context. This means you can drop the bot into an ongoing incident thread and it catches up immediately.

### Telegram

Long-polling (no server needed). Conversations are per-chat. Access control via `allowed_chat_ids`.

```yaml
adapter:
  type: telegram
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_chat_ids:
      - ${TELEGRAM_CHAT_ID}
```

### CLI

Interactive REPL. Good for local development and testing skills before deploying. Supports markdown rendering, slash commands, and tab completion.

```bash
mithai chat
```

### Running multiple adapters

To run Slack and Telegram simultaneously, use `types` instead of `type`:

```yaml
adapter:
  types:
    - slack
    - telegram
  slack:
    bot_token: ${SLACK_BOT_TOKEN}
    app_token: ${SLACK_APP_TOKEN}
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_chat_ids:
      - ${TELEGRAM_CHAT_ID}
```

Each adapter runs in its own thread. They share the same engine and skills. Human MCP approval requests are always routed back through the adapter that received the original message.

---

## Sessions

A session is a conversation with memory. The agent maintains message history within a session so it can reference earlier context.

Session scope depends on the adapter:

| Adapter | Session scope |
|---|---|
| Slack | Per Slack thread (`thread_ts`) |
| Telegram | Per chat |
| CLI | Per process |

In Slack: every thread is its own session. The agent doesn't confuse parallel conversations happening in different threads of the same channel.

Sessions are persisted to `.mithai/state/`. If the bot restarts, it picks up where it left off in the next message.

---

## Memory

Memory is persistent storage that spans sessions and restarts. It's distinct from session history (which is conversation turns) — memory is more like a knowledge base.

### Memory skill

The built-in `memory` skill exposes three tools to the agent:

- `memory_read(path)` — read a file
- `memory_write(path, content, mode)` — write or append
- `memory_search(query)` — keyword search across all files

The agent uses these to record facts, save playbooks, and look up past decisions:

```
User: remember that our database primary is at db-1.internal
Agent: memory_write("MEMORY.md", "- DB primary: db-1.internal\n", mode="append")
       Saved.
```

### Memory in tool handlers

Tools can read and write memory directly via `ctx["memory"]`:

```python
memory = ctx.get("memory")
if memory:
    memory.write("incidents.md", f"- Restarted {service}\n", append=True)
    content = memory.read("playbooks/restart.md")
```

### Memory files

```
memory/
├── MEMORY.md             # main knowledge base, injected into every prompt
├── approvals.json        # approval history (used by shell skill for auto-promote)
├── playbooks/            # step-by-step runbooks
│   └── restart.md
└── daily/
    └── 2026-03-16.md     # daily reflection (written by the agent after each session)
```

`MEMORY.md` is special: it's automatically loaded into every conversation's system prompt. Use it to record facts your agent should always know — team conventions, environment details, known issues.

### Learning and auto-promotion

When the `shell` skill sees a command approved enough times with no denials, it stops asking. This is the auto-promote mechanism:

```yaml
skills:
  config:
    shell:
      approval_auto_promote: 3    # approve 3 times → runs automatically
```

The approval history is stored in `memory/approvals.json`. You can inspect or reset it at any time.

The agent also runs a reflection pass after each conversation (when `learning.reflection: true`), writing a brief summary to `memory/daily/YYYY-MM-DD.md`. Over time this becomes a log of what the agent has learned and done.

---

## How a request flows

Understanding the request lifecycle helps when debugging or extending mithai.

```
  User message
       │
       ▼
┌─────────────┐
│   Adapter   │  (Slack / Telegram / CLI)
└──────┬──────┘
       │  engine.handle(message, adapter)
       ▼
┌─────────────┐     session history +
│   Engine    │ ◄── system prompt + memory
└──────┬──────┘
       │
       ▼
┌─────────────┐
│     LLM     │  (Claude)
└──────┬──────┘
       │
       ▼  tool call in response?
  ┌────┴────┐
  │  yes    │  no ──────────────────────────┐
  ▼         │                               │
resolve_human()                             │
  │                                         │
  ├── null (auto-execute) ──────────┐       │
  │                                 │       │
  └── "approve" / "confirm"         │       │
       │                            │       │
       ▼                            │       │
┌─────────────┐                     │       │
│  Human MCP  │  approval request   │       │
│  via Adapter│ ──► User            │       │
└──────┬──────┘     approve/deny    │       │
       │ approved                   │       │
       └────────────────────────────┘       │
                    │                       │
                    ▼                       │
             handle(name, input, ctx)       │
                    │                       │
                    ▼                       │
             tool_result added to history   │
                    │                       │
                    └──► back to LLM ◄──────┘
                              │  (loop until no tool calls)
                              ▼
                       final text response
                              │
                              ▼
                        ┌───────────┐
                        │  Adapter  │
                        └─────┬─────┘
                              │
                              ▼
                            User
```

---

## Multi-agent mode

For larger organizations, you can run multiple independent agents from a single `config.yaml`. Each agent has its own Slack app, its own skill set, and its own memory.

```yaml
agents:
  devops:
    name: "DevOps Agent"
    system_prompt: "You are a DevOps assistant. Focus on infrastructure."
    skills:
      allowed: [shell, kubernetes, aws, memory]
    adapter:
      slack:
        bot_token: ${DEVOPS_SLACK_BOT_TOKEN}
        app_token: ${DEVOPS_SLACK_APP_TOKEN}
    memory:
      path: ./memory/devops

  triage:
    name: "Triage Agent"
    system_prompt: "You are an incident triage assistant."
    skills:
      allowed: [shell, github, memory]
    adapter:
      slack:
        bot_token: ${TRIAGE_SLACK_BOT_TOKEN}
        app_token: ${TRIAGE_SLACK_APP_TOKEN}
    memory:
      path: ./memory/triage
```

Each agent is isolated. `mithai run` starts all of them.

---

← [Your first skill](your-first-skill.md) | [Skills reference →](skills-reference.md)
