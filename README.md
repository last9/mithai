# mithai

AI agent framework for infrastructure operations.

**[Documentation →](https://docs.mithai.dev)**

mithai gives your team an AI-powered ops agent that lives in Slack, Telegram, and a terminal — simultaneously. It can query your infrastructure, take actions, and ask a human before doing anything dangerous.

## How it works

```
You (in Slack/Telegram/CLI)
     |
     v
  Adapter  -->  Engine  -->  LLM (Anthropic API or AWS Bedrock)
                  |              |
              Skills         Tool calls
           (shell, memory,    (namespaced as
            scheduling, etc.)  skill__tool)
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
git clone https://github.com/last9/mithai.git
cd mithai
pip install -e ".[dev]"
mithai init
mithai chat
```

To use AWS Bedrock instead of the Anthropic API, install the `bedrock` extra and configure your AWS credentials — see [LLM providers](#llm-providers) below:

```bash
pip install 'mithai[bedrock]'
```

Telemetry is optional. Install `mithai[telemetry]` only when you want
OpenTelemetry export and Last9 GenAI span enrichment; the core package runs
without those dependencies.

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
| `shell` | Run allowlisted shell commands | dynamic |
| `memory` | Persistent memory across conversations (MEMORY.md) | auto |
| `sessions` | Inspect past conversation sessions per channel | auto |
| `scheduling` | Create recurring cron-based tasks via Slack | confirm |
| `kubernetes` | Inspect pods, deployments, events, logs, and resource descriptions | auto |

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
    kubernetes__get_pods: null     # keep read-only pod listing auto-executed
```

## Scheduling

The scheduling skill lets the agent create recurring tasks. A cron job posts a Slack message mentioning the bot — the bot processes it like any other @mention.

Two backends:

| Backend | Config | Storage |
|---------|--------|---------|
| `crontab` (default) | No config needed | Local crontab |
| `agent_cloud_platform` | URL + token | Central platform, survives restarts |

```yaml
skills:
  config:
    scheduling:
      backend: agent_cloud_platform
      scheduling_backend_url: https://your-platform/api
      scheduling_backend_token: ${SCHEDULING_BACKEND_TOKEN}
```

## Configuration

```yaml
# config.yaml
bot:
  name: mithai
  system_prompt: |
    You are an ops assistant. Be concise.

adapter:
  # Run one adapter:
  type: slack

  # Or run multiple adapters simultaneously:
  # types:
  #   - slack
  #   - telegram

  slack:
    bot_token: ${SLACK_BOT_TOKEN}
    app_token: ${SLACK_APP_TOKEN}
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_chat_ids:
      - ${TELEGRAM_CHAT_ID}

llm:
  provider: anthropic
  model: claude-sonnet-4-6
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}

skills:
  paths:
    - ./skills
  config:
    shell:
      allowed_commands: ["df -h", "free -h", "uptime"]
    scheduling:
      backend: crontab  # or agent_cloud_platform
```

When using `types`, each adapter runs in its own thread sharing the same engine and skills. Human MCP approvals route back through whichever platform the message came from.

### LLM providers

mithai supports two LLM providers. Select via `llm.provider` in `config.yaml`.

**Anthropic (default)** — direct Claude API access via the `anthropic` Python SDK:

```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-6
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
```

**AWS Bedrock** — unified Converse API across model families (Anthropic / Llama / Cohere / Mistral on Bedrock). Requires `pip install 'mithai[bedrock]'`:

```yaml
llm:
  provider: bedrock
  model: anthropic.claude-sonnet-4-20250514-v1:0   # Bedrock model ID
  bedrock:
    access_key_id: ${AWS_ACCESS_KEY_ID}
    secret_access_key: ${AWS_SECRET_ACCESS_KEY}
    region: ${AWS_REGION}
```

The IAM principal needs `bedrock:InvokeModel` for each model the agent will use. For full provider details, model IDs, and troubleshooting see the [Configuration › LLM](docs/configuration.md#llm) and [Troubleshooting](docs/troubleshooting.md) docs.

## Onboarding

When the bot is added to a Slack channel — or on startup for channels in `allowed_channels` — it runs an onboarding flow: learning who's in the channel, what it's used for, and introducing itself.

Enable it in `config.yaml`:

```yaml
onboarding:
  enabled: true
```

**What it does:**

1. Reads its existing memory (`MEMORY.md`) to recall what it already knows about the org from other channels
2. Fetches the full channel member roster via `slack_get_members`
3. Reads recent channel history via `slack_get_history`
4. Merges any new facts into `MEMORY.md` — updating or correcting existing entries rather than duplicating them
5. Posts a short intro message to the channel

On startup, channels listed in `allowed_channels` that haven't been onboarded yet are onboarded automatically (one per thread, concurrently).

**Customising the onboarding prompt:**

Drop an `onboarding.md` file in your project root (alongside `config.yaml`). The engine loads it instead of the built-in prompt. Two placeholders are available:

```markdown
You just joined #{channel_name} (ID: {channel_id}).

This team uses a monorepo. Start by reading memory, fetching the member
roster, and scanning the last 50 messages. Then update MEMORY.md and
write a one-paragraph intro. No bullet points, no emojis.
```

Literal `{` and `}` in the template (e.g. JSON examples) are safe — only `{channel_id}` and `{channel_name}` are substituted.

**Memory model:**

mithai serves one organisation across multiple Slack channels. Knowledge is shared — facts learned in `#infra` are available in `#backend`. `MEMORY.md` is the single source of truth. The onboarding flow reads before writing and merges rather than appends, so joining a new channel enriches the shared knowledge without duplicating it.

## CLI

```
mithai init                    # scaffold project
mithai run                     # start all configured adapters
mithai run --adapter slack     # start only one adapter
mithai chat                    # CLI REPL for development
mithai skill create <name>     # create a new skill
mithai skill list              # list loaded skills
mithai skill validate          # validate all skills
mithai agent create <id>       # scaffold a new agent (multi-agent mode)
mithai agent list              # list configured agents
mithai agent info <id>         # show agent details
mithai agent validate          # validate all agent configs
```

### Multi-agent scaffolding

`mithai agent create` generates the directory structure and config for a new agent:

```bash
mithai agent create devops --name "DevOps Agent" --skills shell,memory,http_checker
```

This creates `agents/devops/` with `memory/`, `.env.example`, and `system_prompt.md`, and appends the agent config block to `config.yaml`.

To customize the onboarding flow for all agents, drop an `onboarding.md` in the project root (see [Onboarding](#onboarding)).

## License

Apache 2.0
