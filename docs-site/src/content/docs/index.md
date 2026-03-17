---
title: "mithai documentation"
description: "A framework for building AI agents for your organization."
---

**A framework for building AI agents for your organization.**

---


mithai lets you build an AI agent that lives where your team works — Slack, Telegram, a terminal — and does real work through skills you define. Skills are the unit of extension: a folder with a prompt and a set of tools. The agent uses them to answer questions, take actions, and ask a human before doing anything sensitive.

---

## Get started

New to mithai? Follow these guides in order:

1. **[Getting started](/getting-started/)** — Install mithai, run your first conversation in the terminal, and optionally connect it to Slack.

2. **[Your first skill](/your-first-skill/)** — Build a working skill from scratch. You'll write a service health checker that reports on endpoint availability and requires approval before triggering a restart.

3. **[Core concepts](/concepts/)** — Understand how skills, adapters, Human MCP, sessions, and memory fit together.

---

## Reference

- **[Skills reference](/skills-reference/)** — Every hook a skill can export (`handle`, `resolve_human`, `startup`, `bind`), the `ctx` object, tool schema format, and built-in skills.
- **[Configuration reference](/configuration/)** — Every key in `config.yaml`, environment variable interpolation, and per-adapter options.

## Operations

- **[Testing your skill](/testing/)** — Unit test `handle()` and `resolve_human()` directly without running the agent. Complete test file for the tutorial skill.
- **[Deploy to production](/deployment/)** — Run mithai as a systemd service or Docker container, persist memory across restarts, and upgrade safely.
- **[Security considerations](/security/)** — Secrets management, approval level guidelines, what should never auto-execute, and a skill code review checklist.

## Help

- **[Troubleshooting](/troubleshooting/)** — Skill not loading, agent not responding, approval issues, config problems, and how to read the logs.
- **[Examples](/examples/)** — Three complete skills: read/write with dynamic approval, background polling with `startup`, and proactive Slack alerts with `bind`.

---

## What mithai is

mithai is an agent framework. You connect it to the tools and systems your organization uses — APIs, databases, CLIs, internal services — and expose them as skills. The agent decides which skills to invoke based on what a user asks. When it needs to take a sensitive action, it pauses and asks for approval first.

```
You (in Slack):  "are all payment services healthy?"

mithai:  → calls services__check_endpoint("https://checkout.internal/health")
         → calls services__check_endpoint("https://billing.internal/health")
         → calls services__check_endpoint("https://fraud.internal/health")

         All three services are healthy.
         checkout: 200 OK (43ms), billing: 200 OK (61ms), fraud: 200 OK (38ms)
```

```
You:  "restart the checkout service"

mithai:  I'll restart checkout-api in production.

         Tool: services__restart_service
         Service: checkout-api  Environment: production

         [Approve]  [Deny]

You:  approve

mithai:  Done. checkout-api is back up. 3/3 instances healthy.
```

**Skills** are how you extend the agent. A skill is a folder with two files:

- `prompt.md` — tells the AI what the skill does and when to use it
- `tools.py` — defines the tools the AI can call and implements them

Drop a folder in `skills/`. Restart. Done.

**Human MCP** is human-in-the-loop as a protocol, built into the framework — not bolted on. Each tool declares its own risk level. Safe reads run automatically. Mutations and sensitive actions pause for approval. You can override any tool's level in `config.yaml`.

**Memory and learning** let the agent accumulate knowledge over time. Approved actions are remembered. After enough approvals with no denials, a command promotes to auto-execute. The agent also reflects after each conversation and records what it learned.

**Sessions** are scoped to context. In Slack, each thread is an isolated conversation with its own history. In Telegram, it's per-chat. In the terminal, it's the current session. The same agent, the same skills, the same memory — whatever surface the user is on.

---

[Getting started →](/getting-started/)
