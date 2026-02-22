"""mithai init — scaffold a new mithai project."""

import click
from pathlib import Path


CONFIG_TEMPLATE = """# mithai configuration
# Docs: https://github.com/nishantmodak/mithai

bot:
  name: mithai
  system_prompt: |
    You are a helpful operations assistant.
    You have access to skills that let you interact with infrastructure.
    Be concise and precise. Explain before acting.

adapter:
  # Single adapter: type: cli
  # Multiple adapters: types: [slack, telegram]
  type: {adapter_type}
  slack:
    bot_token: ${{SLACK_BOT_TOKEN}}
    app_token: ${{SLACK_APP_TOKEN}}
  telegram:
    bot_token: ${{TELEGRAM_BOT_TOKEN}}
    allowed_chat_ids:
      - ${{TELEGRAM_CHAT_ID}}

llm:
  provider: anthropic
  model: claude-sonnet-4-6
  max_tokens: 4096
  anthropic:
    api_key: ${{ANTHROPIC_API_KEY}}

skills:
  paths:
    - ./skills

state:
  backend: filesystem
  filesystem:
    path: ./.mithai/state
"""

ENV_TEMPLATE = """# mithai secrets (never commit this)

# LLM API key (required)
ANTHROPIC_API_KEY=

# Slack (if using slack adapter)
# SLACK_BOT_TOKEN=xoxb-...
# SLACK_APP_TOKEN=xapp-...

# Telegram (if using telegram adapter)
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_CHAT_ID=
"""

GITIGNORE_TEMPLATE = """.env
.mithai/
__pycache__/
*.pyc
"""

EXAMPLE_PROMPT = """You can check the health of HTTP endpoints.
Report status codes, response times, and whether services are reachable.
"""

EXAMPLE_TOOLS = '''"""Example skill: HTTP health checker."""

import json
import time
import urllib.request
import urllib.error


TOOLS = [
    {
        "name": "check_url",
        "description": "Check if a URL is reachable and return its status code and response time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to check (e.g., https://example.com)",
                },
            },
            "required": ["url"],
        },
    },
]


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "check_url":
        url = input["url"]
        start = time.time()
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                elapsed = round((time.time() - start) * 1000)
                return json.dumps({
                    "url": url,
                    "status": resp.status,
                    "response_time_ms": elapsed,
                    "healthy": 200 <= resp.status < 400,
                })
        except urllib.error.URLError as e:
            elapsed = round((time.time() - start) * 1000)
            return json.dumps({
                "url": url,
                "error": str(e.reason),
                "response_time_ms": elapsed,
                "healthy": False,
            })
        except Exception as e:
            return json.dumps({"url": url, "error": str(e), "healthy": False})

    return json.dumps({"error": f"Unknown tool: {name}"})
'''


@click.command()
@click.option(
    "--adapter",
    "adapter_type",
    type=click.Choice(["cli", "slack", "telegram"]),
    default=None,
    help="Messaging adapter to configure",
)
@click.option("--dir", "target_dir", default=".", help="Directory to initialize in")
def init(adapter_type, target_dir):
    """Scaffold a new mithai project with config, skills, and .env."""
    target = Path(target_dir)

    if adapter_type is None:
        adapter_type = click.prompt(
            "Which adapter?",
            type=click.Choice(["cli", "slack", "telegram"]),
            default="cli",
        )

    # Create config.yaml
    config_path = target / "config.yaml"
    if config_path.exists():
        if not click.confirm("config.yaml already exists. Overwrite?", default=False):
            click.echo("Skipping config.yaml")
        else:
            config_path.write_text(CONFIG_TEMPLATE.format(adapter_type=adapter_type))
            click.echo("Created config.yaml")
    else:
        config_path.write_text(CONFIG_TEMPLATE.format(adapter_type=adapter_type))
        click.echo("Created config.yaml")

    # Create .env
    env_path = target / ".env"
    if not env_path.exists():
        env_path.write_text(ENV_TEMPLATE)
        click.echo("Created .env")

    # Create .gitignore
    gitignore_path = target / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE_TEMPLATE)
        click.echo("Created .gitignore")

    # Create example skill
    skill_dir = target / "skills" / "http_checker"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "prompt.md").write_text(EXAMPLE_PROMPT)
    (skill_dir / "tools.py").write_text(EXAMPLE_TOOLS)
    click.echo("Created skills/http_checker/ (example skill)")

    click.echo("\nDone! Next steps:")
    click.echo("  1. Edit .env with your API keys")
    click.echo("  2. Run: mithai chat")
