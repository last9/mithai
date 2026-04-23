---
title: "Deploy to production"
description: "Run mithai as a systemd service or Docker container, persist memory, and upgrade safely."
---


This guide covers running mithai as a long-lived process in production: as a systemd service on Linux, as a Docker container, and the operational concerns that apply to both.

---

## On this page

- [Before you deploy](#before-you-deploy)
- [Running as a systemd service](#running-as-a-systemd-service)
- [Running with Docker](#running-with-docker)
- [Environment and secrets](#environment-and-secrets)
- [Keeping memory persistent](#keeping-memory-persistent)
- [Webhook / headless mode](#webhook--headless-mode)
- [Multiple instances](#multiple-instances)
- [Health checking](#health-checking)
- [Upgrading](#upgrading)

---

## Before you deploy

Work through this checklist before starting the process:

- [ ] Secrets are not in `config.yaml`. All tokens are referenced as `${ENV_VAR}` and set in the environment or a `.env` file outside the repository.
- [ ] Approval levels are reviewed. Every tool that can modify infrastructure has `"human": "approve"` or uses `resolve_human()`. Run `mithai skill validate` to catch missing fields.
- [ ] The memory directory (`./memory/` by default) and state directory (`./.mithai/state/`) are backed up or snapshotted.
- [ ] `mithai doctor` passes with no issues.
- [ ] `mithai skill validate` passes for all skills.

```bash
mithai doctor
mithai skill validate
```

> **Warning:** If `mithai doctor` reports an LLM connectivity failure, the agent will start but every message will result in an error. Resolve connectivity issues before deploying.

---

## Running as a systemd service

This is the recommended approach for running mithai on a Linux host directly.

### Create a service user

```bash
sudo useradd --system --no-create-home --shell /bin/false mithai
```

### Install mithai

Install into a virtualenv owned by the service user:

```bash
sudo mkdir -p /opt/mithai
sudo python3 -m venv /opt/mithai/venv
sudo /opt/mithai/venv/bin/pip install "mithai[slack]"
```

### Place configuration

```bash
sudo mkdir -p /etc/mithai
sudo cp config.yaml /etc/mithai/config.yaml
sudo cp .env /etc/mithai/.env          # contains secrets
sudo chmod 600 /etc/mithai/.env
sudo chown -R mithai:mithai /etc/mithai
```

### Create data directories

```bash
sudo mkdir -p /var/lib/mithai/memory /var/lib/mithai/state
sudo chown -R mithai:mithai /var/lib/mithai
```

Update `config.yaml` to point at these directories:

```yaml
learning:
  memory:
    backend: filesystem
    filesystem:
      path: /var/lib/mithai/memory

state:
  backend: filesystem
  filesystem:
    path: /var/lib/mithai/state
```

### Write the unit file

Create `/etc/systemd/system/mithai.service`:

```ini
[Unit]
Description=mithai AI agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mithai
Group=mithai
WorkingDirectory=/opt/mithai
ExecStart=/opt/mithai/venv/bin/mithai run --config /etc/mithai/config.yaml
EnvironmentFile=/etc/mithai/.env
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=mithai

# Prevent interactive prompts from hanging the process
StandardInput=null

[Install]
WantedBy=multi-user.target
```

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable mithai
sudo systemctl start mithai
sudo systemctl status mithai
```

### View logs

```bash
# Follow live logs
sudo journalctl -u mithai -f

# Last 100 lines
sudo journalctl -u mithai -n 100

# Since last boot
sudo journalctl -u mithai -b
```

> **Tip:** Add `--verbose` to `ExecStart` during initial deployment to see debug-level logs including skill loading, LLM calls, and tool routing. Remove it once stable to reduce log volume.

---

## Running with Docker

### Dockerfile

The project ships a production-ready Dockerfile at `deploy/Dockerfile`. It uses a two-stage build: a builder stage installs the virtualenv, and a minimal runtime stage contains only what the process needs.

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/

RUN python -m venv /app/.venv && \
    /app/.venv/bin/pip install --upgrade pip --quiet && \
    /app/.venv/bin/pip install ".[slack,mcp]" --quiet

FROM python:3.11-slim AS runtime

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY skills/ /app/skills/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /app/.mithai/state /app/memory

ENTRYPOINT ["mithai", "run", "--config", "/config/config.yaml"]
```

Build it:

```bash
docker build -f deploy/Dockerfile -t mithai:latest .
```

### docker-compose.yml

```yaml
services:
  mithai:
    image: mithai:latest
    restart: unless-stopped
    env_file: .env                         # secrets, never committed
    volumes:
      - ./config.yaml:/config/config.yaml:ro
      - mithai-memory:/app/memory
      - mithai-state:/app/.mithai/state
    stdin_open: false
    tty: false

volumes:
  mithai-memory:
  mithai-state:
```

> **Warning:** Do not mount `config.yaml` with write permissions in the container. Use `:ro` (read-only) to prevent the process from accidentally overwriting your configuration.

Start it:

```bash
docker compose up -d
docker compose logs -f mithai
```

### Passing config to the container

`config.yaml` references `${ENV_VAR}` placeholders. These are resolved at startup from the process environment. Pass them via `env_file`:

**.env** (never commit this file):

```
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
ANTHROPIC_API_KEY=sk-ant-...
```

The `.env` file in `env_file` is loaded by Docker Compose directly into the container environment before mithai starts.

---

## Environment and secrets

`config.yaml` uses `${VAR}` syntax for any value that should come from the environment:

```yaml
llm:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}

adapter:
  slack:
    bot_token: ${SLACK_BOT_TOKEN}
    app_token:  ${SLACK_APP_TOKEN}
```

At startup, `load_config()` calls `_resolve_env_vars()` which walks the entire config tree and substitutes every `${VAR}` with the corresponding environment variable value. If the variable is not set, the literal string `${VAR}` is kept — which will cause an authentication failure at startup, not a config parse error.

You can use default values with `${VAR:-default}`:

```yaml
llm:
  max_tokens: ${LLM_MAX_TOKENS:-4096}
```

**Rules:**

- Never write secret values directly in `config.yaml`.
- Never commit `.env` to your repository. Add it to `.gitignore`.
- For production workloads, use your platform's secret manager (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault, Kubernetes secrets) to inject variables into the process environment at runtime. The mechanism is the same: the variable arrives in the environment, and `${VAR}` is substituted by mithai.

> **Warning:** If `config.yaml` is committed with a live token in it and pushed to a repository, rotate the token immediately. `${VAR}` references in YAML are the only safe pattern for secrets.

---

## Keeping memory persistent

mithai stores two kinds of persistent data:

| What | Default path | Contains |
|---|---|---|
| Memory | `./memory/` | `MEMORY.md`, daily logs, approval history, channel context |
| State | `./.mithai/state/` | Session history (conversation turns) |

If either directory is lost, the agent loses its accumulated knowledge and conversation history. Users will experience the agent as amnesiac — it will not remember previous interactions or facts it was told.

**For Docker:** mount both as named volumes or bind mounts:

```yaml
volumes:
  - mithai-memory:/app/memory
  - mithai-state:/app/.mithai/state
```

Named Docker volumes survive container restarts and image updates. Bind mounts (absolute host paths) make the data easier to back up manually.

**For systemd:** the data directories are at `/var/lib/mithai/memory` and `/var/lib/mithai/state` (as configured above). Back these up with your normal host backup strategy. A nightly `rsync` or snapshot of `/var/lib/mithai/` is sufficient for most deployments.

> **Note:** Memory and state are plain files. `memory/MEMORY.md` is Markdown. Session state files are JSON. You can inspect and edit them directly if needed.

---

## Webhook / headless mode

Use the `api` adapter when you want the agent to receive messages via HTTP instead of Slack or Telegram — for example, as the target of a webhook, a cron scheduler, or a CI pipeline.

```bash
MITHAI_UI_PORT=8080 MITHAI_UI_TOKEN=secret mithai run --adapter api
```

The embedded server listens on `127.0.0.1:8080`. Proxy it through nginx or an SSH tunnel if you need external access. Send messages with:

```bash
curl -X POST http://localhost:8080/api/trigger \
  -H "Authorization: Bearer secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "summarize recent alerts", "channel_id": "cron"}'
# → 202 {"status": "accepted", "channel_id": "cron"}
```

The `channel_id` field acts as a session namespace. Use distinct values to keep different triggers isolated (e.g., `"cron-daily"` vs `"webhook-deploy"`).

**systemd unit for API mode:**

```ini
[Service]
ExecStart=/opt/mithai/venv/bin/mithai run --config /etc/mithai/config.yaml --adapter api
Environment=MITHAI_UI_PORT=8080
Environment=MITHAI_UI_TOKEN=secret
```

> **Note:** Human approval requests are auto-denied in API mode. Do not use this adapter for skills that require interactive approval for sensitive operations.

---

## Multiple instances

Do not run two mithai processes pointed at the same state directory. Both processes will read and write session files concurrently and corrupt each other's state.

If you need multiple agents (for example, a DevOps agent and a Tester agent with different skills), use multi-agent mode:

```yaml
agents:
  devops:
    name: "DevOps Agent"
    skills:
      allowed: [shell, kubernetes, memory]
    memory:
      path: ./memory/devops          # separate memory per agent
    adapter:
      slack:
        bot_token: ${DEVOPS_BOT_TOKEN}
        app_token:  ${DEVOPS_APP_TOKEN}

  tester:
    name: "Tester Agent"
    skills:
      allowed: [shell, http_checker, memory]
    memory:
      path: ./memory/tester
    adapter:
      slack:
        bot_token: ${TESTER_BOT_TOKEN}
        app_token:  ${TESTER_APP_TOKEN}
```

With `agents:` configured, `mithai run` starts all agents in the same process. They share one state backend but each agent's sessions are namespaced by `agent_id`, so there are no collisions.

> **Warning:** Do not run `mithai run` twice from the same working directory with the same config. Use multi-agent mode or separate working directories with separate state paths.

---

## Health checking

### mithai status

Check what is configured and loaded:

```bash
mithai status
```

This reads `config.yaml` without connecting to any external service. It reports the LLM provider and model, loaded skills and their tool counts, configured adapters, session counts, and memory backend.

### mithai doctor

Run a full connectivity check:

```bash
mithai doctor
```

This attempts a real LLM API call, verifies Slack and Telegram tokens, tests kubectl connectivity if the kubernetes skill is configured, and checks that data directories are writable. Exit code is `0` when all checks pass, `1` if any fail.

Use `mithai doctor` in a deployment pipeline as a smoke test after deploying a new configuration.

### What to monitor

For production monitoring, instrument these:

- **Process liveness:** the mithai process is running (systemd `ActiveState=active` or Docker container health).
- **LLM error rate:** `mithai doctor` failures in scheduled checks.
- **Log errors:** `journalctl -u mithai` for `ERROR` and `Exception` lines.
- **Disk space:** the memory and state directories grow over time. Session files accumulate at `.mithai/state/sessions/`. Prune old ones if disk becomes a concern.

If you have telemetry configured (`telemetry.enabled: true` in `config.yaml`), mithai emits OpenTelemetry traces for each request and tool call. The `mithai.request` span and per-tool spans give you latency and approval-rate data.

---

## Upgrading

### Upgrade mithai

For a virtualenv install:

```bash
/opt/mithai/venv/bin/pip install --upgrade "mithai[slack]"
```

For Docker, rebuild the image:

```bash
docker build -f deploy/Dockerfile -t mithai:latest .
docker compose up -d --no-deps mithai
```

### After upgrading

1. Run `mithai skill validate` to confirm all skills still pass validation against the new version.
2. Run `mithai doctor` to confirm connectivity.
3. Restart the service:

```bash
# systemd
sudo systemctl restart mithai

# Docker Compose
docker compose restart mithai
```

### Upgrading individual skills

If you installed optional skills with `mithai skill install`, upgrade them individually:

```bash
mithai skill upgrade kubernetes
mithai skill upgrade github
```

Core skills (shell, memory, sessions, http_checker) are bundled with the mithai binary and upgrade with it.

> **Note:** Skill upgrades do not affect data in `memory/` or `.mithai/state/`. Your agent's accumulated knowledge is safe across upgrades.

---

← [Testing your skill](testing.md) | [Security considerations](security.md) →
