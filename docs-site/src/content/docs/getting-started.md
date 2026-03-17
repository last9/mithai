---
title: "Getting started"
description: "Install mithai, run your first conversation, and connect to Slack."
---


This guide walks you through installing mithai, configuring it, and having your first conversation. By the end, you'll have a working agent running in your terminal.

---


---

## Prerequisites

- Python 3.11 or later
- An [Anthropic API key](https://console.anthropic.com/)
- `uv` (recommended) or `pip`

---

## Install

**From PyPI:**

```bash
pip install mithai
```

**From source** (to use the latest or contribute):

```bash
git clone https://github.com/nishantmodak/mithai.git
cd mithai
pip install -e ".[dev]"
```

Verify the install:

```bash
mithai --version
```

---

## Initialize a project

Run `mithai init` in an empty directory. It creates a `config.yaml`, a `.env` file, and a `skills/` folder.

```bash
mkdir my-agent && cd my-agent
mithai init
```

You'll see:

```
✓ Created config.yaml
✓ Created .env
✓ Created skills/
✓ Created memory/

Next: add your ANTHROPIC_API_KEY to .env, then run `mithai chat`
```

Open `.env` and add your key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

> **Never commit `.env`.** `mithai init` adds it to `.gitignore` automatically.

---

## Start the CLI

```bash
mithai chat
```

This starts an interactive session in your terminal. The agent comes with three built-in skills: `shell` (run commands), `http_checker` (check URLs), and `memory` (persistent notes).

Try a few things:

```
> what's the disk usage on this machine?
> is https://example.com reachable?
> remember that our staging environment is at staging.internal
```

### How approval works

The `shell` skill uses **dynamic approval** — commands on the allowlist run automatically, but anything new asks first:

```
> count the number of lines in /etc/hosts

  Tool: shell__run_command
  Command: wc -l /etc/hosts

  [Approve]  [Deny]
```

Approve it. The agent runs the command and shows the result. Approve the same command three times (with no denials), and it auto-promotes — next time it just runs.

```
  User message
       │
       ▼
  Agent calls tool
       │
       ▼
  resolve_human?
  ┌────┴────┐
  │         │
auto      ask user
execute    │
  │      ┌─┴──────┐
  │      │        │
  │    deny     approve
  │      │        │
  │      ▼        │
  │    (tool      │
  │   skipped)    │
  │               │
  └───────────────┘
         │
      handle()
         │
         ▼
    LLM response
         │
         ▼
       User
```

Auto-promotion shortens this loop over time: once a specific command has been approved `approval_auto_promote` times with no denials, it jumps straight to `handle()` without asking.

### Slash commands in the terminal

The CLI supports a few built-in commands:

```
/help       show what the agent can do
/skills     list loaded skills and their tools
/memory     browse the memory files
/sessions   list past sessions
/clear      start a fresh conversation
```

---

## Explore built-in skills

```bash
mithai skill list
```

```
shell         1 tool    run_command       approval: dynamic
http_checker  1 tool    check_url         approval: auto
memory        3 tools   read/write/search approval: auto
```

To inspect a skill:

```bash
mithai skill show shell
```

---

## Connect to Slack

If you want the agent in your team's Slack workspace, you need a Slack app with Socket Mode enabled.

### Create a Slack app

The fastest way is to use the pre-built manifest file, which configures all the required scopes and settings in one step.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App → From a manifest**.
2. Select your workspace and click **Next**.
3. Paste the contents of [`docs/slack-manifest.yaml`](slack-manifest.yaml) and click **Next → Create**.
4. Under **Socket Mode**, confirm it's enabled. Click **Generate Token** to get your **App-Level Token** (`xapp-...`). Save it.
5. Under **OAuth & Permissions**, click **Install to Workspace**. Save the **Bot User OAuth Token** (`xoxb-...`).

> **Creating manually instead?** Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App → From scratch**, then add the scopes listed in [`docs/slack-manifest.yaml`](slack-manifest.yaml) by hand under **OAuth & Permissions**. Enable Socket Mode and interactivity separately.

### Add tokens to `.env`

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

### Update `config.yaml`

```yaml
adapter:
  type: slack
  slack:
    bot_token: ${SLACK_BOT_TOKEN}
    app_token: ${SLACK_APP_TOKEN}
    respond: mentions   # respond only when @mentioned; use "all" to respond to every message
```

### Start the bot

```bash
mithai run
```

Invite the bot to a channel (`/invite @mithai`) and @mention it. Approval requests appear as inline buttons in the Slack thread. Each thread is its own isolated conversation with history.

---

## What's next

- **[Your first skill →](/your-first-skill/)** — Build a real skill from scratch. Learn how to define tools, handle calls, add approval levels, and read per-skill config.
- **[Core concepts →](/concepts/)** — Understand the architecture: how skills, adapters, Human MCP, sessions, and memory work together.

