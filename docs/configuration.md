---
title: "Configuration reference"
description: "Every key in config.yaml — adapters, LLM, skills, human approval, learning, and multi-agent mode."
---


mithai is configured with a `config.yaml` file. All values support `${ENV_VAR}` interpolation — the framework substitutes environment variables at load time. Secrets should always live in `.env`, not in `config.yaml`.

---

## On this page

- [Quick example](#quick-example)
- [bot](#bot)
- [adapter](#adapter)
- [llm](#llm)
- [skills](#skills)
- [human](#human)
- [learning](#learning)
- [state](#state)
- [mcp_servers](#mcp_servers)
- [agents (multi-agent mode)](#agents-multi-agent-mode)
- [Environment variables](#environment-variables)
- [CLI flags](#cli-flags)

---

## Quick example

```yaml
bot:
  name: mithai
  system_prompt: |
    You are a concise assistant. Always confirm before irreversible actions.

adapter:
  type: slack
  slack:
    bot_token: ${SLACK_BOT_TOKEN}
    app_token: ${SLACK_APP_TOKEN}
    respond: mentions

llm:
  provider: anthropic
  model: claude-sonnet-4-6
  max_tokens: 4096
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}

skills:
  paths:
    - ./skills
  config:
    shell:
      allowed_commands: ["df -h", "uptime"]
```

---

## `bot`

```yaml
bot:
  name: mithai                  # the agent's name; shown in responses and UI
  system_prompt: |              # prepended to every conversation
    You are a concise assistant.
```

`system_prompt` is added before skill prompts and memory. Use it to set the agent's personality, scope, and any organization-wide rules.

---

## `adapter`

Adapters connect the agent to communication platforms.

### Single adapter

```yaml
adapter:
  type: slack           # slack | telegram | cli
  slack: { ... }
```

### Multiple adapters

```yaml
adapter:
  types:
    - slack
    - telegram
  slack: { ... }
  telegram: { ... }
```

All adapters share the same engine and skills. Human MCP approvals always route back through the adapter that received the original message.

### Slack

```yaml
adapter:
  slack:
    bot_token: ${SLACK_BOT_TOKEN}    # xoxb-... (Bot User OAuth Token)
    app_token: ${SLACK_APP_TOKEN}    # xapp-... (App-Level Token, Socket Mode)
    respond: mentions                # "mentions" (default) or "all"
```

`respond: mentions` — the bot only responds when @mentioned. Use `respond: all` to respond to every message in channels it's invited to.

### Telegram

```yaml
adapter:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_chat_ids:
      - ${TELEGRAM_CHAT_ID}          # whitelist of chat IDs
```

### CLI

No configuration required. Use `mithai chat` to start an interactive session.

---

## `llm`

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-6    # or claude-opus-4-6, claude-haiku-4-5
  max_tokens: 4096
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
```

**Recommended models:**

| Model | When to use |
|---|---|
| `claude-sonnet-4-6` | Default. Best balance of capability and speed. |
| `claude-opus-4-6` | Complex reasoning, multi-step tasks, high-stakes decisions. |
| `claude-haiku-4-5` | High-volume, latency-sensitive, simple queries. |

`max_tokens` controls the maximum length of each LLM response. 4096 is a good default. Raise it to 8192 or higher for skills that produce long outputs (e.g., log analysis, code review).

---

## `skills`

```yaml
skills:
  paths:
    - ./skills                  # directories to scan for skills
    - /opt/shared/skills        # additional paths
  config:
    shell:                      # skill name → config dict passed as ctx["config"]
      allowed_commands:
        - "df -h"
        - "uptime"
      approval_auto_promote: 3
    services:
      services:
        checkout:
          url: https://checkout.internal/health
        billing:
          url: https://billing.internal/health
```

`skills.paths` lists directories. Each subdirectory with a `prompt.md` and `tools.py` is loaded as a skill.

`skills.config` maps skill names to arbitrary config dicts. A skill receives its config as `ctx["config"]` in every handler call.

---

## `human`

Controls the human-in-the-loop protocol globally.

```yaml
human:
  timeout_seconds: 300     # how long to wait for approval before timing out (default: 300)
  overrides:
    shell__run_command: confirm        # escalate a tool's approval level
    kubernetes__get_pods: null         # de-escalate to auto-execute
    services__restart_service: approve # override regardless of resolve_human
```

`overrides` keys are `skillname__toolname`. Valid values: `null`, `"approve"`, `"confirm"`.

Overrides take effect after `resolve_human` — they are the final word on approval level.

---

## `learning`

Controls the agent's memory and self-learning behaviors.

```yaml
learning:
  enabled: true
  reflection: true             # write a daily reflection after each session
  approval_auto_promote: 3     # approve N times with 0 denials → auto-execute
  memory:
    backend: filesystem
    filesystem:
      path: ./memory           # root directory for memory files
```

`reflection: true` runs a background LLM call after each conversation and appends a brief summary to `memory/daily/YYYY-MM-DD.md`.

`approval_auto_promote` is the global default. Skills can override it in their own config (e.g., `skills.config.shell.approval_auto_promote`).

---

## `state`

Persistent key-value store for session state.

```yaml
state:
  backend: filesystem
  filesystem:
    path: ./.mithai/state
```

The state backend stores session history and tool metadata. Don't change the path unless you know what you're doing — the agent won't find past sessions if you move it.

---

## `mcp_servers`

External [Model Context Protocol](https://modelcontextprotocol.io/) servers. Skills declare which servers they use via `MCP_TOOLS`. The framework starts only the servers that are needed.

```yaml
mcp_servers:
  linear:
    transport: sse
    url: https://mcp.linear.app/sse
    headers:
      Authorization: Bearer ${LINEAR_API_KEY}

  github:
    transport: sse
    url: https://api.githubcopilot.com/mcp/
    headers:
      Authorization: Bearer ${GITHUB_TOKEN}
```

---

## `agents` (multi-agent mode)

Run multiple independent agents from a single process. Each agent has its own adapter, skill set, system prompt, and memory.

```yaml
agents:
  devops:
    name: "DevOps Agent"
    system_prompt: |
      You are a DevOps assistant. Focus on infrastructure and deployments.
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
    system_prompt: |
      You are an incident triage assistant.
    skills:
      allowed: [shell, github, memory]
    adapter:
      slack:
        bot_token: ${TRIAGE_SLACK_BOT_TOKEN}
        app_token: ${TRIAGE_SLACK_APP_TOKEN}
    memory:
      path: ./memory/triage
```

When `agents` is present, the top-level `adapter`, `skills`, and `llm` blocks act as defaults inherited by each agent. Agent-level config overrides defaults.

---

## Environment variables

Any `${VAR}` in `config.yaml` is substituted at load time from the process environment. mithai also loads `.env` in the working directory automatically.

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

If a referenced variable is not set, mithai logs a warning and substitutes an empty string.

---

## CLI flags

A subset of config values can be overridden with CLI flags:

```bash
mithai run --adapter slack         # override adapter.type
mithai run --config path/to/config.yaml   # use a different config file
mithai run --verbose               # enable debug logging
mithai chat --agent devops         # use a specific agent in multi-agent mode
```

CLI flags take precedence over `config.yaml`.

---

← [Skills reference](skills-reference.md) | [Testing your skill →](testing.md)
