---
title: "Security considerations"
description: "Secrets management, approval level guidelines, what should never auto-execute, and a skill review checklist."
---


mithai executes real actions against real systems. This page covers what to think about before connecting it to production.

---

## On this page

- [Secrets management](#secrets-management)
- [Approval level guidelines](#approval-level-guidelines)
- [What should never auto-execute](#what-should-never-auto-execute)
- [Network and access control](#network-and-access-control)
- [Slack security](#slack-security)
- [Skill code review checklist](#skill-code-review-checklist)
- [Memory and data handling](#memory-and-data-handling)

---

## Secrets management

All secrets belong in `.env`, never in `config.yaml`.

**`config.yaml`** — safe to commit:
```yaml
llm:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}   # reference only
```

**`.env`** — never commit:
```
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...
```

`mithai init` adds `.env` to `.gitignore` automatically. Verify it's there:

```bash
grep .env .gitignore
```

> **Warning:** If you accidentally commit secrets, rotate them immediately. Remove the commit from history with `git filter-branch` or BFG Repo Cleaner — simply deleting the file in a new commit is not enough.

In production, prefer a secrets manager (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) over `.env` files. Set environment variables at the process level rather than from a file on disk.

---

## Approval level guidelines

The default approval level for any new tool should be `"approve"`. Downgrade to auto-execute only for operations you'd be comfortable with the agent running without oversight.

**Safe to auto-execute:**
- Read-only queries (list, get, describe, search)
- Health checks and status lookups
- Writing to the agent's own memory files
- Operations that are trivially reversible

**Should require `"approve"`:**
- Any write, update, or delete operation
- Sending messages or notifications on behalf of the team
- Starting or stopping processes
- Running shell commands not on your allowlist
- Anything that costs money

**Should require `"confirm"`:**
- Deleting data that cannot be recovered
- Modifying production infrastructure
- Operations affecting many users or resources at once
- Anything you'd want a second human to review

Use `resolve_human` to make approval contextual. Requiring `"approve"` for all restarts is too conservative in staging and not conservative enough in production:

```python
def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    if name == "restart_service":
        env = input.get("environment", "production")
        if env == "production":
            return "confirm"    # type to confirm for production
        if env == "staging":
            return "approve"    # button for staging
        return None             # dev: auto-execute
    return None
```

---

## What should never auto-execute

Regardless of how trusted a user is, some operations should always require human approval. Enforce this at the skill level — don't rely on configuration overrides that could be accidentally removed.

Hardcode `"approve"` or `"confirm"` directly on the tool definition (not via `resolve_human`) for:

- `DROP`, `DELETE`, `TRUNCATE` on databases
- `kubectl delete` on namespaces or deployments
- `terraform destroy` or `terraform apply` on production
- Any operation that terminates or terminates+replaces running instances
- Sending external communications (email, webhooks, pagerduty escalations)
- Modifying IAM roles, access policies, or firewall rules

```python
TOOLS = [
    {
        "name": "delete_database",
        "description": "Drop a database. Irreversible.",
        "input_schema": { ... },
        "human": "confirm",    # always — do not use "dynamic" here
    },
]
```

> **Note:** `"human": "dynamic"` means the skill's `resolve_human` function decides. If that function has a bug and returns `None` by mistake, the operation runs automatically. For truly irreversible operations, use a static `"confirm"` that cannot be bypassed.

---

## Network and access control

**Principle of least privilege.** Give mithai only the permissions it actually needs.

- Create a dedicated service account (AWS IAM role, GCP service account, k8s service account) for mithai with read-only permissions by default
- Add write permissions only for the specific resources and operations the agent needs to manage
- Avoid using admin credentials or root accounts

**Network isolation.** Run mithai inside your private network:

- It uses Socket Mode for Slack — outbound only, no inbound port needed
- It should not be exposed to the public internet
- Skills that call internal APIs should use private DNS names, not public endpoints

**Audit logging.** mithai logs every tool call and approval decision. Forward these logs to your SIEM or log aggregator. The memory system writes approval history to `memory/approvals.json` — include this in your backup and audit rotation.

---

## Slack security

**Scope only what you need.** The [`slack-manifest.yaml`](slack-manifest.yaml) includes `message.channels` for `respond: all` mode. If you're using `respond: mentions` (the default), you can remove `message.channels`, `message.groups`, and `message.im` from the event subscriptions — the bot only needs `app_mention`.

**Verify the workspace.** mithai responds to anyone who can @mention it. If your Slack workspace allows external guests, they can interact with the agent. Restrict the bot to specific channels using Slack's channel restrictions feature, or add an `allowed_user_ids` list in your skill's `resolve_human`.

**Rotate tokens regularly.** Slack bot tokens don't expire by default, but should be rotated when team members with access leave. Revoke tokens under **OAuth & Permissions → Revoke Token**.

> **Warning:** Anyone who can @mention the bot and get an approval request through can trigger approved actions. Keep your Slack workspace access controls tight, and review who has access to channels where the bot operates.

---

## Skill code review checklist

Before deploying a new skill to production, review it against this checklist:

**Input handling:**
- [ ] All inputs from `input` dict are validated before use
- [ ] File paths are not constructed directly from user input (path traversal)
- [ ] Shell commands are not constructed by concatenating user input (command injection)
- [ ] SQL queries use parameterized statements, not string formatting
- [ ] External URLs from input are validated before fetching

**Approval levels:**
- [ ] Every tool that writes, deletes, or modifies has `"human": "approve"` or higher
- [ ] Irreversible operations use a static `"confirm"`, not `"dynamic"`
- [ ] `resolve_human` has a safe default (`return "approve"` not `return None`) when conditions are unclear

**Secrets:**
- [ ] No API keys or passwords hardcoded in the skill
- [ ] Credentials read from `ctx["config"]` which comes from env-var-substituted config
- [ ] No sensitive values written to memory files

**Error handling:**
- [ ] `handle()` never raises an unhandled exception (wrap in try/except, return error JSON)
- [ ] Timeouts set on all network requests
- [ ] Returns a valid JSON string in all code paths

---

## Memory and data handling

The `memory/` directory and `.mithai/state/` are plain files on disk. Treat them like application data:

- **Backup** both directories. Loss of state means the agent loses session history and approval records.
- **Do not store secrets in memory.** The agent may write things to memory automatically via reflection. Review `memory/daily/` logs periodically and remove any credentials or PII that shouldn't be there.
- **Access control.** The process running mithai should have write access to `memory/` and `.mithai/state/`. No other process should need write access.
- **Memory injection.** `memory/MEMORY.md` is injected into every conversation. Review it periodically — if it contains stale or incorrect information, the agent will act on it.

---

← [Deploy to production](deployment.md) | [Troubleshooting](troubleshooting.md) →
