---
title: "Troubleshooting"
description: "Diagnose and fix skill loading failures, Slack connectivity issues, approval problems, and config errors."
---


This guide covers the most common problems with a running mithai agent: skill loading failures, unexpected agent behavior, approval flow problems, configuration issues, and memory failures. Each section shows how to diagnose the problem and fix it.

> The examples below use `services` as a sample skill — the one built in [Build your first skill](your-first-skill.md). It is not a built-in skill; substitute your own skill name.

---

## On this page

- [Checking logs](#checking-logs)
- [Validating your setup](#validating-your-setup)
- [Skill issues](#skill-issues)
- [Agent behavior](#agent-behavior)
- [Approval issues](#approval-issues)
- [Configuration](#configuration)
- [Memory](#memory)

---

## Checking logs

Run with `--verbose` to see debug-level output including skill loading, config parsing, LLM calls, tool routing, and approval decisions:

```bash
mithai run --verbose
```

or for the chat interface:

```bash
mithai chat --verbose
```

Key patterns to look for:

| Log message | Means |
|---|---|
| `Loaded skill: services (3 tools)` | Skill loaded successfully |
| `Skill services: missing TOOLS export` | `tools.py` does not define `TOOLS` |
| `Skill services: missing handle() function` | `tools.py` does not define `handle` |
| `Skipping services: missing SKILL.md` | Directory is incomplete |
| `Human MCP: requesting approve for services__restart_service` | Approval gate triggered |
| `Executing tool: services__restart_service` | Tool approved and running |
| `Tool denied by human: services__restart_service` | User clicked Deny |
| `LLM response: stop_reason=tool_use` | LLM called a tool |
| `LLM response: stop_reason=end_turn` | LLM finished responding |
| `reflection: spawning background task (N tool calls)` | Reflection triggered after the turn |
| `reflection: skipped — disabled in config` | Reflection off (`learning.reflection` not set) |
| `reflection: skipped — no tool calls this turn` | Nothing happened worth reflecting on |
| `reflection: wrote N learning(s) to daily/YYYY-MM-DD.md` | Reflection saved a summary |
| `reflection: nothing learned this turn` | Reflection ran but recorded nothing |

For production deployments running as systemd:

```bash
sudo journalctl -u mithai -f
```

---

## Validating your setup

### mithai skill validate

Checks all skills in `./skills/` for structural correctness — presence of `SKILL.md`, and when `tools.py` exists: valid `TOOLS` schema, required `handle` function, and valid `"human"` levels:

```bash
mithai skill validate
```

Validate a single skill:

```bash
mithai skill validate services
```

A passing skill reports:

```
✓ services: OK (3 tools)
```

A failing skill reports the exact error:

```
✗ services: FAILED
    - Tool 2: missing 'description'
    - tools.py does not export handle() function
```

### mithai doctor

Runs a full connectivity check — LLM API, Slack/Telegram tokens, kubectl clusters if configured, skill loading, and filesystem permissions:

```bash
mithai doctor
```

Exit code is `0` when all checks pass, `1` if any fail. Use this in a deployment pipeline after config changes.

---

## Skill issues

### Skill not loading

**Symptom:** The agent does not use a skill you added. `mithai skill list` does not show it. With `--verbose`, you see `Skipping services: missing SKILL.md`.

**Diagnosis:** Check that your skill directory has both required files:

```bash
ls skills/services/
# Expected: SKILL.md  (and tools.py if the skill defines native tools)
```

Check that the directory name does not start with `.` or `_` — those are silently skipped by the loader.

Check that the skill directory is under a path listed in `skills.paths` in `config.yaml`:

```yaml
skills:
  paths:
    - ./skills       # relative to where mithai is run from
```

If the path is correct but the skill still does not appear, run `mithai skill validate services` to catch import errors that would otherwise be swallowed.

**Fix:** Ensure `SKILL.md` exists and is non-empty, and the path in `config.yaml` points at the parent directory containing the skill folder.

> **Note:** If you have two skill directories with the same name on different paths, the one from the path listed *later* in `skills.paths` wins. This is intentional — it lets you override bundled skills with local versions.

---

### Tool not appearing in the agent's tool list

**Symptom:** The agent ignores a specific tool or says it cannot perform an action that the tool handles. `--verbose` shows the skill loaded but the tool is never called.

**Diagnosis:** The tool is not making it into the LLM's context for one of these reasons:

1. The tool definition is missing from `TOOLS`. Verify:

```python
# In tools.py — is your tool here?
TOOLS = [
    {"name": "restart_service", ...},
]
```

2. The `TOOLS` list has a syntax error that truncates it at parse time. Run `mithai skill validate services` — it imports `tools.py` and checks every entry.

3. The tool description is ambiguous and the LLM is not selecting it. Improve the `"description"` field to be specific about when to use the tool.

**Fix:** Run `mithai skill validate` and correct any reported errors. If the tool definition is correct but the LLM is not calling it, rewrite the `"description"` to be more explicit, and update `SKILL.md` to tell the agent under what conditions to use this tool.

---

### handle() returning wrong type

**Symptom:** The agent's response includes raw Python objects, `None`, or an error like `TypeError: Object of type X is not JSON serializable`.

**Diagnosis:** `handle()` must return a `str`. The engine passes this string directly to the LLM as the tool result. Any non-string return will cause a serialization error.

Check your `handle` signature:

```python
def handle(name: str, input: dict, ctx: dict) -> str:
    # Must return str in every branch
    if name == "list_services":
        services = ctx.get("config", {}).get("services", {})
        return json.dumps({"services": services})   # correct: str

    # Wrong — returning a dict
    # return {"services": services}

    # Wrong — forgetting the fallthrough
    # (no return means None is returned)
    return json.dumps({"error": f"unknown tool: {name}"})
```

Every code path in `handle()` must return a `str`. The last line handling unknown tool names is a required safety net.

**Fix:** Add `-> str` as the return type annotation and return `json.dumps(...)` in every branch, including the fallthrough case.

---

### resolve_human not being called

**Symptom:** The agent runs a tool that should require approval without asking. Or the tool always asks for approval when you expected it to auto-execute based on `resolve_human` logic.

**Diagnosis:** `resolve_human` is only called when the tool's `"human"` field is set to `"dynamic"`. Check the tool definition:

```python
TOOLS = [
    {
        "name": "restart_service",
        "description": "...",
        "input_schema": {...},
        "human": "dynamic",   # required for resolve_human to be called
    },
]
```

If `"human"` is set to `"approve"` or omitted, `resolve_human` is never called — the static level is used directly.

Also verify that `resolve_human` is exported at the module level (not nested inside a class or function):

```python
# Correct — top-level function
def resolve_human(name: str, input: dict, ctx: dict) -> str | None:
    ...
```

Run `mithai skill validate services` — it checks that `handle` is callable but does not currently validate `resolve_human`. Add a quick Python import check if in doubt:

```bash
python3 -c "from skills.services.tools import resolve_human; print('ok')"
```

**Fix:** Set `"human": "dynamic"` on the tool definition and ensure `resolve_human` is a top-level function in `tools.py`.

---

## Agent behavior

### Agent not responding in Slack

**Symptom:** You send a message in Slack and nothing happens. No response, no error visible in Slack.

**Diagnosis — check in this order:**

1. **Is the bot running?**

```bash
# systemd
sudo systemctl status mithai

# Docker
docker compose ps
```

2. **Is the bot invited to the channel?**

The bot must be a member of the channel before it receives messages. In Slack, type `/invite @your-bot-name` in the channel.

3. **Is the bot token valid?**

Run `mithai doctor` — it calls `auth.test` against the Slack API and reports the workspace name on success or the exact error on failure.

4. **Is the app token configured for Socket Mode?**

The Socket Mode adapter (`adapter.types: [slack]`) requires both a bot token (`xoxb-...`) and an app-level token (`xapp-...`). The app-level token must have the `connections:write` scope. Check your app at `https://api.slack.com/apps` under **Basic Information → App-Level Tokens**.

5. **Check the process logs:**

```bash
sudo journalctl -u mithai -n 50
```

Look for `invalid_auth`, `missing_scope`, or connection error messages.

---

### Agent responding to everything instead of only mentions

**Symptom:** The bot replies to every message in a channel, not just messages that `@mention` it.

**Diagnosis:** Check `respond` in `config.yaml`:

```yaml
adapter:
  slack:
    respond: mentions    # correct: only reply to @mentions
    # respond: all       # this causes the bot to reply to everything
```

The default when `respond` is omitted is `"all"`. Set it explicitly to `"mentions"` for normal Slack deployments.

**Fix:**

```yaml
adapter:
  slack:
    bot_token: ${SLACK_BOT_TOKEN}
    app_token:  ${SLACK_APP_TOKEN}
    respond: mentions
```

Restart the agent after changing this.

---

### Agent is silent in Slack Connect channels

**Symptom:** The bot reacts to a message or runs tools, but does not post text back in a Slack Connect / externally shared channel. Internal channels still receive replies.

**Diagnosis:** Check whether external-channel posting is disabled:

```yaml
adapter:
  slack:
    allow_posting_in_external_channels: false
```

When this setting is `false`, mithai uses Slack `conversations.info` metadata to detect external shared channels and suppresses adapter-originated text posts there. This includes final assistant replies, Slack send-message tools, approval prompts, onboarding messages, and canned app-mention replies.

Look for this log line when metadata cannot be fetched:

```text
Could not resolve Slack channel info for external posting guard
```

If metadata lookup fails, mithai fails closed and suppresses the attempted text post.

**Fix:** If the silence is expected, no action is needed. If the channel should receive replies, either set `allow_posting_in_external_channels: true` or move the workflow to an internal or Enterprise Grid org-shared channel. If the channel is internal but is being treated as external, verify the Slack app can call `conversations.info` for that channel and inspect the channel metadata returned by Slack.

---

### Agent forgetting context between messages

**Symptom:** The agent does not remember things said earlier in the same thread. Each message is treated as a new conversation.

**Diagnosis:** The agent scopes sessions to `thread_ts` in Slack. If you are messaging in the channel top-level (not in a thread), each top-level message starts a new session scoped to `channel_id`. The agent accumulates history within a thread, not across the channel.

Check session storage:

```bash
ls .mithai/state/sessions/
```

If no session files exist, the state backend is not writing to disk. Verify the path is correct and writable:

```yaml
state:
  backend: filesystem
  filesystem:
    path: ./.mithai/state
```

Check the directory permissions:

```bash
ls -la .mithai/state/
```

Also confirm `sessions.max_history` is not set to `0`:

```yaml
sessions:
  max_history: 10    # number of turns to replay into context
  max_stored: 50     # number of turns to keep on disk
```

**Fix:** Start a thread by replying to the bot's first message — sessions accumulate within a thread. For cross-thread memory, the agent uses `MEMORY.md` via the memory skill.

---

### Agent stuck in a tool loop

**Symptom:** The agent calls tools repeatedly without producing a final response. It looks like it is looping — calling the same tool or a sequence of tools over and over.

**Diagnosis:** This happens when:

1. A tool is returning an error and the agent retries it. Check what the tool returns — if it returns `{"error": "..."}`, the LLM may attempt the same call with slightly different inputs hoping to succeed.

2. A tool is returning data the LLM does not understand and it keeps trying to reinterpret it. Check the JSON shape your tool returns and whether it matches what `SKILL.md` describes.

3. The LLM is calling a tool to complete a previous tool call's intent (chaining too deep). Check `--verbose` logs to see the full sequence of tool calls.

**Fix options:**

- Fix the underlying tool error so it returns useful results.
- Make `handle()` return clearer error messages that tell the LLM when to stop retrying.
- Add guidance to `SKILL.md` telling the agent to stop after N failed attempts.
- Use the `human.overrides` config to force a specific tool to require approval, giving you a chance to interrupt the loop manually:

```yaml
human:
  overrides:
    services__restart_service: approve
```

---

## Approval issues

### Approval request never appears

**Symptom:** You expect an approval prompt but the tool runs automatically.

**Diagnosis — check in order:**

1. **Is the `"human"` field set on the tool?**

```python
TOOLS = [
    {
        "name": "restart_service",
        "description": "...",
        "input_schema": {...},
        "human": "approve",   # this must be present
    },
]
```

Run `mithai skill validate services` — it reports missing or invalid `"human"` values.

2. **Is there a config override removing the approval requirement?**

Check `config.yaml`:

```yaml
human:
  overrides:
    services__restart_service: null   # null removes the requirement
```

3. **If using `"human": "dynamic"`, is `resolve_human` returning `None`?**

Add a temporary `print` or log statement to `resolve_human` to confirm it is being called and what it returns. The approval is skipped if `resolve_human` returns `None`.

4. **Has auto-promote kicked in?**

After `learning.approval_auto_promote` consecutive approvals (default: 3) with no denials for the same tool+input combination, mithai auto-promotes that specific input to auto-execute. This is stored in `memory/approvals.json`. Check:

```bash
cat memory/approvals.json
```

**Fix:** If auto-promote removed an approval you want to keep, delete the relevant entry from `memory/approvals.json`, or set `learning.approval_auto_promote: 0` to disable auto-promotion entirely.

---

### Approval times out before user responds

**Symptom:** The agent reports the tool was denied even though no one clicked Deny. Logs show a timeout.

**Diagnosis:** The default approval timeout is 300 seconds (5 minutes). Check your config:

```yaml
adapter:
  slack:
    approval_timeout: 300   # seconds
```

If your team needs more time, increase this. Note that the agent thread blocks while waiting for approval — a very long timeout (e.g. `3600`) means the agent holds an open connection to Slack for that duration.

**Fix:** Increase `approval_timeout`:

```yaml
adapter:
  slack:
    approval_timeout: 600   # 10 minutes
```

---

### Approved command runs again with approval next time

**Symptom:** You approved a command, it ran successfully, but the next time the same command runs it still asks for approval. Auto-promote is not working.

**Diagnosis:** Auto-promote requires the same tool *and* the same normalized input to be approved `approval_auto_promote` times (default: 3) with zero denials. Check:

1. Is `learning.enabled: true` in `config.yaml`?
2. Is `memory` configured and the memory directory writable?
3. Is the approval count in `memory/approvals.json` actually incrementing?

```bash
cat memory/approvals.json
```

You should see something like:

```json
{
  "services__restart_service": {
    "{\"environment\":\"staging\",\"service\":\"auth\"}": {
      "approved": 2,
      "denied": 0
    }
  }
}
```

The input key is the JSON-serialized input dict with sorted keys. If the inputs differ between calls (e.g. different whitespace or key order), each is tracked as a separate entry.

4. Is `approval_auto_promote` set to a value other than `3`?

```yaml
learning:
  approval_auto_promote: 3   # promote after this many consecutive approvals
```

**Fix:** Verify the memory backend is writing (`cat memory/approvals.json` shows real data), check the `approved` count is incrementing, and confirm the threshold matches your config.

---

## Configuration

### Environment variable not substituted (shows ${VAR} literally)

**Symptom:** The agent fails to connect and logs show `invalid_auth` or a similar error. Inspecting the behavior reveals the token is literally the string `${SLACK_BOT_TOKEN}`.

**Diagnosis:** The environment variable is not set in the environment when `mithai run` starts. Check:

```bash
echo $SLACK_BOT_TOKEN
```

If it prints nothing, the variable is not exported. Also check whether a `.env` file is present in the same directory as `config.yaml`:

```bash
ls -la .env
```

mithai calls `python-dotenv`'s `load_dotenv()` on the `.env` file in the config directory at startup. If the file does not exist or is not in the right directory, variables are not loaded.

> **Note:** `${VAR:-default}` syntax provides a fallback: `${PORT:-8080}` resolves to `8080` if `PORT` is not set. Use this for non-secret values with sensible defaults.

**Fix:** Create `.env` in the same directory as `config.yaml`:

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ANTHROPIC_API_KEY=sk-ant-...
# Or for the bedrock provider:
# AWS_ACCESS_KEY_ID=AKIA...
# AWS_SECRET_ACCESS_KEY=...
# AWS_REGION=us-east-1
```

Then restart mithai.

---

### bedrock provider crashes at startup with KeyError or "boto3 is required"

**Symptom:** `mithai run` exits with `KeyError: 'access_key_id'`, a ClickException `bedrock provider requires llm.bedrock.access_key_id...`, or `RuntimeError: boto3 is required for Bedrock`.

**Diagnosis:**

- Missing config keys: `llm.provider: bedrock` is set, but the `llm.bedrock:` block is missing one or more of `access_key_id`, `secret_access_key`, `region`.
- boto3 not installed: the `bedrock` extra was not installed.

**Fix:**

```bash
pip install 'mithai[bedrock]'
```

Then ensure `config.yaml` has the full Bedrock block:

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-sonnet-4-20250514-v1:0
  bedrock:
    access_key_id: ${AWS_ACCESS_KEY_ID}
    secret_access_key: ${AWS_SECRET_ACCESS_KEY}
    region: ${AWS_REGION}
```

And the three `AWS_*` variables are in `.env` or the process environment.

---

### bedrock provider rejects credentials at runtime

**Symptom:** `RuntimeError: bedrock converse failed for model ...: ExpiredToken`, `AccessDenied`, or `InvalidClientTokenId`.

**Diagnosis:** The credentials are rejected when invoking the requested Bedrock model — usually one of:

- The IAM principal lacks `bedrock:InvokeModel` for the model ID.
- The model is not enabled in the configured region (Bedrock requires explicit model access).
- Temporary STS credentials have expired (`ExpiredToken`).
- Temporary credentials are configured without their session token (`InvalidClientTokenId`) — STS-issued keys are only valid together with `AWS_SESSION_TOKEN`.

**Fix:**

- Attach an IAM policy that grants `bedrock:InvokeModel` on `arn:aws:bedrock:<region>::foundation-model/<model-id>`.
- In the AWS Bedrock console, request access to the foundation model in the target region.
- For temporary credentials, set `llm.bedrock.session_token: ${AWS_SESSION_TOKEN}` in `config.yaml` and keep all three values (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) from the same STS response. When they expire, refresh them and update `.env`.

---

### config.yaml parse error

**Symptom:** `mithai run` exits immediately with a YAML parse error or `Config validation failed`.

**Diagnosis:** YAML is whitespace-sensitive. Common mistakes:

- Tabs instead of spaces (YAML requires spaces)
- Inconsistent indentation
- Unquoted strings containing `:` or `#`
- A multiline `system_prompt` missing the `|` block scalar indicator

Test the file standalone:

```bash
python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"
```

If it raises a `ScannerError`, it reports the line number. If it parses fine, the error is a schema validation failure — run `mithai doctor` for a clearer error message.

**Fix:** Correct the YAML at the reported line. For multiline strings, use the block scalar:

```yaml
bot:
  system_prompt: |
    You are a helpful operations assistant.
    You have access to skills that let you interact with infrastructure.
```

---

### Skill config not reaching ctx["config"]

**Symptom:** `ctx.get("config", {})` returns `{}` in your skill, but you have config defined in `config.yaml`.

**Diagnosis:** The `config` key in `ctx` is populated from `skills.config.<skill_name>`. The skill name must match the directory name exactly.

Check `config.yaml`:

```yaml
skills:
  config:
    services:          # this key must match the skill directory name
      services:
        checkout:
          url: https://...
```

Check the skill directory name:

```bash
ls skills/
# Should show: services/
```

If the skill is named `service` (singular) but the config key is `services` (plural), `ctx["config"]` will be `{}`.

**Fix:** Make the key under `skills.config` match the skill directory name exactly.

---

## Memory

### memory_write failing with path validation error

**Symptom:** The memory skill's `memory_write` tool returns an error like `Invalid path: ../etc/passwd` or `ValueError: Invalid path`.

**Diagnosis:** The memory backend enforces a path traversal guard. Any path that resolves outside the memory root directory is rejected. This includes paths starting with `../`, absolute paths starting with `/`, and any path that after normalization escapes the root.

Valid paths:
- `MEMORY.md`
- `daily/2024-01-15.md`
- `notes/services.md`

Invalid paths:
- `../config.yaml`
- `/etc/passwd`
- `../../secrets`

**Fix:** Use relative paths that stay within the memory root. The memory root is set by `learning.memory.filesystem.path` in `config.yaml` (default: `./memory`).

---

### Memory not loading into system prompt

**Symptom:** The agent does not remember facts you told it to remember. `MEMORY.md` exists on disk but the agent behaves as if it has never seen it.

**Diagnosis:** Check that `learning.enabled: true` and the memory backend is correctly configured:

```yaml
learning:
  enabled: true
  memory:
    backend: filesystem
    filesystem:
      path: ./memory
```

Verify the file exists and is non-empty:

```bash
cat memory/MEMORY.md
```

With `--verbose`, look for the memory injection in the startup logs. The engine calls `self._memory.read("MEMORY.md")` on every request. If it returns `None` or empty string, nothing is injected.

Check the memory path is an absolute path or is correctly relative to the working directory from which `mithai run` is executed. If you run `mithai run` from `/home/user/mybot/` but the config says `path: ./memory`, the backend expects `/home/user/mybot/memory/`.

**Fix:** Run `mithai run` from the project root (where `config.yaml` lives), or use an absolute path:

```yaml
learning:
  memory:
    backend: filesystem
    filesystem:
      path: /var/lib/mithai/memory
```

---

← [Security considerations](security.md) | [Examples](examples.md) →
