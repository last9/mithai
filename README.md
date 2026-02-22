# mithai

AI agent framework for infrastructure operations.

mithai gives your team an AI-powered ops agent that lives in Slack, Telegram, or a terminal. It can query your infrastructure, take actions, and ask a human before doing anything dangerous.

## How it works

```
You (in Slack/Telegram/CLI)
     |
     v
  Adapter  -->  Engine  -->  LLM (Claude)
                  |              |
              Skills         Tool calls
           (k8s, AWS,        (namespaced as
            shell, etc.)      skill__tool)
                  |
             Human MCP
          (approve/confirm
           before risky ops)
```

**Skills** are plugins — a folder with `prompt.md` (tells the AI what the skill does) and `tools.py` (defines tools the AI can call). The AI decides which tools to use based on your message.

**Human MCP** is human-in-the-loop as a protocol. Skills declare which tools need human approval (`"human": "approve"`) or confirmation (`"human": "confirm"`). Read-only tools run automatically.

## Quick start

```bash
pip install mithai
mithai init
# Edit .env with your ANTHROPIC_API_KEY
mithai chat
```

Or from source:

```bash
git clone https://github.com/nishantmodak/mithai.git
cd mithai
pip install -e ".[dev]"
mithai init
mithai chat
```

## Creating a skill

```bash
mithai skill create my_skill
```

This creates `skills/my_skill/` with two files:

**`prompt.md`** — what the AI knows about your skill:
```markdown
You can check the health of HTTP endpoints.
Report status codes and response times.
```

**`tools.py`** — what the AI can do:
```python
import json

TOOLS = [
    {
        "name": "check_url",
        "description": "Check if a URL is reachable",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to check"},
            },
            "required": ["url"],
        },
        # Add "human": "approve" for risky operations
    },
]

def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "check_url":
        # Your implementation here
        return json.dumps({"status": 200, "healthy": True})
    return json.dumps({"error": f"Unknown tool: {name}"})
```

That's it. Drop the folder in `skills/`, restart mithai, and the AI can use it.

## Built-in skills

| Skill | What it does | Human MCP |
|-------|-------------|-----------|
| `http_checker` | Check URL health, status codes, response times | auto |
| `shell` | Run allowlisted shell commands | approve |
| `kubernetes` | Pods, logs, restart deployments | restart: approve |
| `aws` | EC2, S3, costs, stop instances | stop: approve |
| `cicd` | GitHub Actions runs, re-run failed | rerun: approve |

## Human MCP

Tools declare their human-in-the-loop requirement:

```python
TOOLS = [
    {"name": "get_pods", ...},                          # auto-execute
    {"name": "restart", ..., "human": "approve"},       # click approve/deny
    {"name": "delete_ns", ..., "human": "confirm"},     # type confirmation
]
```

Override in `config.yaml`:
```yaml
human:
  timeout_seconds: 300
  overrides:
    shell__run_command: confirm    # escalate
    kubernetes__get_pods: null     # de-escalate
```

## Configuration

```yaml
# config.yaml
bot:
  name: mithai
  system_prompt: |
    You are an ops assistant. Be concise.

adapter:
  type: slack  # or: telegram, cli
  slack:
    bot_token: ${SLACK_BOT_TOKEN}
    app_token: ${SLACK_APP_TOKEN}

llm:
  provider: anthropic
  model: claude-sonnet-4-5-20241022
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}

skills:
  paths:
    - ./skills
  config:
    kubernetes:
      default_namespace: production
    shell:
      allowed_commands: ["df -h", "free -h", "uptime"]
```

## CLI

```
mithai init                    # scaffold project
mithai run                     # start with configured adapter
mithai chat                    # CLI REPL for development
mithai skill create <name>     # create a new skill
mithai skill list              # list loaded skills
mithai skill validate          # validate all skills
```

## License

Apache 2.0
