"""mithai init — interactive setup wizard."""

import os
import subprocess

import click
import yaml
from pathlib import Path
from rich.prompt import Prompt, Confirm, IntPrompt

from mithai.cli.style import (
    banner, console, fail, info, ok, step_header, summary_panel, warn,
)


MITHAI_HOME = Path.home() / ".mithai"


def _mask(secret: str) -> str:
    """Mask a secret for display, showing first 4 and last 4 chars."""
    if len(secret) <= 12:
        return "****"
    return f"{secret[:4]}{'*' * (len(secret) - 8)}{secret[-4:]}"


def _validate_anthropic_key(api_key: str, model: str) -> tuple[bool, str]:
    """Validate an Anthropic API key by making a minimal API call."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model=model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return True, f"model {model} accessible"
    except Exception as e:
        return False, str(e)


def _validate_slack_tokens(bot_token: str, app_token: str) -> tuple[bool, str]:
    """Validate Slack tokens by calling auth.test."""
    try:
        from slack_sdk import WebClient
        client = WebClient(token=bot_token)
        result = client.auth_test()
        team = result.get("team", "unknown")
        user = result.get("user", "unknown")
        return True, f"workspace: {team}, bot: @{user}"
    except Exception as e:
        return False, str(e)


def _validate_telegram_token(bot_token: str) -> tuple[bool, str]:
    """Validate a Telegram bot token by calling getMe."""
    try:
        import requests
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            bot_name = data["result"].get("username", "unknown")
            return True, f"bot: @{bot_name}"
        return False, data.get("description", "Invalid token")
    except Exception as e:
        return False, str(e)


def _discover_k8s_contexts(kubeconfig: str) -> list[dict]:
    """List available kubectl contexts from kubeconfig paths."""
    env = dict(os.environ)
    env["KUBECONFIG"] = kubeconfig
    try:
        result = subprocess.run(
            ["kubectl", "config", "get-contexts", "-o", "name"],
            capture_output=True, text=True, timeout=10, env=env,
        )
        if result.returncode != 0:
            return []
        contexts = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]
        return contexts
    except (subprocess.SubprocessError, FileNotFoundError):
        return []


def _validate_k8s_context(kubeconfig: str, context: str) -> tuple[bool, str]:
    """Validate a kubectl context by running cluster-info."""
    env = dict(os.environ)
    env["KUBECONFIG"] = kubeconfig
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info", "--context", context],
            capture_output=True, text=True, timeout=15, env=env,
        )
        if result.returncode == 0:
            return True, "reachable"
        return False, result.stderr.strip()[:100]
    except subprocess.TimeoutExpired:
        return False, "connection timed out"
    except FileNotFoundError:
        return False, "kubectl not found"


@click.command()
@click.option("--dir", "target_dir", default=None, help="Directory for config files (default: ~/.mithai/)")
def init(target_dir):
    """Interactive setup wizard — configure adapters, LLM, skills, and more."""
    target = Path(target_dir) if target_dir else MITHAI_HOME
    config_path = target / "config.yaml"
    env_path = target / "env"

    from mithai import get_version_string
    banner(get_version_string())
    console.print("  [header]Setup Wizard[/]")
    console.print()

    # Handle existing config
    existing_config = None
    if config_path.exists():
        warn(f"Existing configuration found at [white]{config_path}[/]")
        console.print()
        choice = IntPrompt.ask(
            "    [bright_cyan]1.[/] Start fresh (backup existing)\n"
            "    [bright_cyan]2.[/] Update existing (keep current values)\n"
            "    [bright_cyan]3.[/] Abort\n\n"
            "  Choose",
            choices=["1", "2", "3"],
            default=1,
            console=console,
        )
        if choice == 3:
            console.print("  [muted]Aborted.[/]")
            return
        if choice == 2:
            existing_config = yaml.safe_load(config_path.read_text()) or {}
        elif choice == 1:
            backup = config_path.with_suffix(".yaml.bak")
            config_path.rename(backup)
            info(f"Backed up to [muted]{backup}[/]")

    # Calculate total steps dynamically
    total_steps = 4  # LLM, Adapter, Skills, Write
    step = 0

    # Collect all config values
    config = existing_config or {}

    # Track secrets collected during the wizard
    new_secrets = {}

    # ─── Step 1: LLM Provider ────────────────────────────────────────
    step += 1
    step_header(step, total_steps, "LLM Provider")

    existing_llm = config.get("llm", {})
    provider = Prompt.ask(
        "    Provider",
        default=existing_llm.get("provider", "anthropic"),
        console=console,
    )

    model = Prompt.ask(
        "    Model",
        default=existing_llm.get("model", "claude-sonnet-4-6"),
        console=console,
    )

    max_tokens = IntPrompt.ask(
        "    Max tokens",
        default=existing_llm.get("max_tokens", 16384),
        console=console,
    )

    # Get API key
    existing_key = ""
    if existing_llm.get("anthropic", {}).get("api_key", "").startswith("${"):
        # Key is an env var reference — check if set
        env_var = existing_llm["anthropic"]["api_key"].strip("${}")
        existing_key = os.environ.get(env_var, "")

    if existing_key:
        console.print(f"    API key: [muted]{_mask(existing_key)} (from env)[/]")
        api_key = existing_key
        use_env_ref = True
    else:
        api_key = Prompt.ask("    Anthropic API key", password=True, console=console)
        use_env_ref = False

    # Validate
    info("Validating LLM connection...")
    validated_ok, msg = _validate_anthropic_key(api_key, model)
    if validated_ok:
        ok(msg)
    else:
        fail(msg)
        if not Confirm.ask("    Continue anyway?", default=False, console=console):
            console.print("  [muted]Aborted.[/]")
            return

    config["llm"] = {
        "provider": provider,
        "model": model,
        "max_tokens": max_tokens,
        "anthropic": {"api_key": "${ANTHROPIC_API_KEY}"},
    }

    # ─── Step 2: Adapters ────────────────────────────────────────────
    step += 1
    step_header(step, total_steps, "Adapters")

    existing_adapters = config.get("adapter", {})
    existing_types = existing_adapters.get("types", [existing_adapters.get("type", "cli")])

    adapter_choices = []
    for adapter_type in ["slack", "telegram", "cli"]:
        default = adapter_type in existing_types
        icon = {"slack": "#", "telegram": "@", "cli": ">"}[adapter_type]
        if Confirm.ask(
            f"    [{icon}] Enable [bright_cyan]{adapter_type}[/]?",
            default=default,
            console=console,
        ):
            adapter_choices.append(adapter_type)

    if not adapter_choices:
        adapter_choices = ["cli"]
        warn("No adapters selected — defaulting to cli")

    config["adapter"] = {"types": adapter_choices}

    # Slack config
    if "slack" in adapter_choices:
        existing_slack = existing_adapters.get("slack", {})
        console.print()
        console.print("    [header]Slack Configuration[/]")
        bot_token = Prompt.ask(
            "    Bot Token",
            default="(existing)" if existing_slack.get("bot_token") else "",
            password=True,
            console=console,
        )
        if bot_token == "(existing)":
            bot_token = ""

        app_token = Prompt.ask(
            "    App Token",
            default="(existing)" if existing_slack.get("app_token") else "",
            password=True,
            console=console,
        )
        if app_token == "(existing)":
            app_token = ""

        config["adapter"]["slack"] = {
            "bot_token": "${SLACK_BOT_TOKEN}",
            "app_token": "${SLACK_APP_TOKEN}",
        }

        if bot_token:
            new_secrets["SLACK_BOT_TOKEN"] = bot_token
        if app_token:
            new_secrets["SLACK_APP_TOKEN"] = app_token

        # Validate Slack
        actual_bot = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        if actual_bot:
            actual_app = app_token or os.environ.get("SLACK_APP_TOKEN", "")
            info("Validating Slack...")
            validated_ok, msg = _validate_slack_tokens(actual_bot, actual_app)
            if validated_ok:
                ok(msg)
            else:
                fail(msg)
        else:
            console.print("    [muted]Skipping Slack validation (no token provided)[/]")

    # Telegram config
    if "telegram" in adapter_choices:
        existing_tg = existing_adapters.get("telegram", {})
        console.print()
        console.print("    [header]Telegram Configuration[/]")
        tg_token = Prompt.ask(
            "    Bot Token",
            default="(existing)" if existing_tg.get("bot_token") else "",
            password=True,
            console=console,
        )
        if tg_token == "(existing)":
            tg_token = ""

        config["adapter"]["telegram"] = {
            "bot_token": "${TELEGRAM_BOT_TOKEN}",
            "allowed_chat_ids": existing_tg.get("allowed_chat_ids", []),
        }

        if tg_token:
            new_secrets["TELEGRAM_BOT_TOKEN"] = tg_token

        actual_tg = tg_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if actual_tg:
            info("Validating Telegram...")
            validated_ok, msg = _validate_telegram_token(actual_tg)
            if validated_ok:
                ok(msg)
            else:
                fail(msg)

    # ─── Step 3: Skills & Infrastructure ─────────────────────────────
    step += 1
    step_header(step, total_steps, "Skills & Infrastructure")

    from mithai.cli.skill_cmd import (
        CORE_SKILLS, SKILL_DEPS, _available_optional_skills,
        _check_deps, _user_skills_dir,
    )
    import shutil

    user_skills = _user_skills_dir()
    available = _available_optional_skills()

    console.print(
        f"    Core skills (always active): "
        f"[bright_cyan]{', '.join(sorted(CORE_SKILLS))}[/]"
    )
    console.print()

    skills_config = config.get("skills", {}).get("config", {})
    installed_skills = []

    for skill_name in sorted(available.keys()):
        if skill_name in CORE_SKILLS:
            continue

        already_installed = (user_skills / skill_name).exists()
        default = already_installed

        if Confirm.ask(
            f"    Install [bright_cyan]{skill_name}[/]?",
            default=default,
            console=console,
        ):
            # Check deps
            failed = _check_deps(skill_name)
            if failed:
                for dep in failed:
                    fail(f"{dep['label']} — not found")
                    console.print(f"      [muted]{dep['install_hint']}[/]")
                if not Confirm.ask(
                    f"    Install {skill_name} anyway?",
                    default=False,
                    console=console,
                ):
                    continue

            # Install (or skip if already installed)
            target_skill = user_skills / skill_name
            if not target_skill.exists():
                user_skills.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    available[skill_name],
                    target_skill,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
                )

            installed_skills.append(skill_name)

            # Show dep check results
            if not failed:
                deps = SKILL_DEPS.get(skill_name, [])
                for dep in deps:
                    ok(dep["label"])

            # Skill-specific config prompts
            if skill_name == "kubernetes":
                _configure_kubernetes(config, skills_config)

    config.setdefault("skills", {})["config"] = skills_config

    # Kubernetes KUBECONFIG validation
    k8s_config = skills_config.get("kubernetes", {})
    kubeconfig = k8s_config.get("kubeconfig", "")
    if kubeconfig and "kubernetes" in installed_skills:
        console.print()
        info("Validating Kubernetes clusters...")
        contexts = _discover_k8s_contexts(kubeconfig)
        for ctx_name in contexts:
            validated_ok, msg = _validate_k8s_context(kubeconfig, ctx_name)
            if validated_ok:
                ok(f"{ctx_name}: {msg}")
            else:
                fail(f"{ctx_name}: {msg}")

    # MCP server config
    if any(s in installed_skills for s in ("last9", "github", "exception_fixer")):
        _configure_mcp_servers(config, installed_skills, new_secrets)

    # ─── Step 4: Write Files ─────────────────────────────────────────
    step += 1
    step_header(step, total_steps, "Write Configuration")

    # Ensure config has required sections
    config.setdefault("bot", {
        "name": "mithai",
        "system_prompt": (
            "You are a helpful operations assistant.\n"
            "You have access to skills that let you interact with infrastructure.\n"
            "Be concise and precise. Explain before acting.\n"
        ),
    })
    config.setdefault("state", {
        "backend": "filesystem",
        "filesystem": {"path": str(target / "state")},
    })
    config.setdefault("learning", {
        "enabled": True,
        "memory_dir": str(target / "memory"),
        "reflection": True,
        "approval_auto_promote": 3,
    })

    # Write config
    target.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    ok(f"Config written to [white]{config_path}[/]")

    # Write env file — merge new secrets with existing
    if not use_env_ref and api_key:
        new_secrets["ANTHROPIC_API_KEY"] = api_key

    existing_env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing_env[k.strip()] = v.strip()

    existing_env.update(new_secrets)

    env_content = "# mithai secrets (never commit this file)\n\n"
    for k, v in sorted(existing_env.items()):
        env_content += f"{k}={v}\n"

    env_path.write_text(env_content)
    env_path.chmod(0o600)
    ok(f"Env file written to [white]{env_path}[/] [muted](permissions: 0600)[/]")

    # Create memory and state dirs
    memory_dir = Path(config.get("learning", {}).get("memory_dir", target / "memory"))
    memory_dir.mkdir(parents=True, exist_ok=True)

    state_dir = Path(config.get("state", {}).get("filesystem", {}).get("path", target / "state"))
    state_dir.mkdir(parents=True, exist_ok=True)
    ok(f"Memory dir: [muted]{memory_dir}[/]")
    ok(f"State dir: [muted]{state_dir}[/]")

    # Summary
    all_skills = sorted(CORE_SKILLS | set(installed_skills))
    summary_content = (
        f"[key]Config:[/]   {config_path}\n"
        f"[key]Secrets:[/]  {env_path}\n"
        f"[key]Skills:[/]   {', '.join(all_skills)}\n"
        f"[key]Adapters:[/] {', '.join(adapter_choices)}\n"
        f"\n"
        f"[bright_cyan]mithai run[/]     Start the agent\n"
        f"[bright_cyan]mithai chat[/]    Interactive CLI chat\n"
        f"[bright_cyan]mithai doctor[/]  Run diagnostics\n"
        f"[bright_cyan]mithai ui[/]      Start Control Room"
    )
    console.print()
    summary_panel("Setup Complete", summary_content)
    console.print()


def _configure_kubernetes(config: dict, skills_config: dict):
    """Prompt for Kubernetes-specific configuration."""
    existing = skills_config.get("kubernetes", {})

    console.print()
    console.print("      [header]Kubernetes Configuration[/]")

    kubeconfig = Prompt.ask(
        "      KUBECONFIG paths (colon-separated)",
        default=existing.get("kubeconfig", str(Path.home() / ".kube" / "config")),
        console=console,
    )

    default_ns = Prompt.ask(
        "      Default namespace",
        default=existing.get("default_namespace", "default"),
        console=console,
    )

    skills_config["kubernetes"] = {
        "kubeconfig": kubeconfig,
        "default_namespace": default_ns,
        "context": existing.get("context", ""),
        "alert_channel": existing.get("alert_channel", ""),
        "poll_interval_minutes": existing.get("poll_interval_minutes", 5),
        "cooldown_minutes": existing.get("cooldown_minutes", 30),
        "namespaces": existing.get("namespaces", []),
        "auto_investigate": existing.get("auto_investigate", True),
        "exclude_namespaces": existing.get("exclude_namespaces", [
            "kube-system", "kube-public", "kube-node-lease",
        ]),
    }


def _configure_mcp_servers(config: dict, installed_skills: list, new_secrets: dict):
    """Prompt for MCP server configuration."""
    console.print()
    console.print("    [header]MCP Server Configuration[/]")
    console.print()

    mcp_servers = config.get("mcp_servers", {})

    if "last9" in installed_skills or "exception_fixer" in installed_skills:
        if Confirm.ask(
            "    Configure [bright_cyan]Last9[/] MCP?",
            default=True,
            console=console,
        ):
            existing_last9 = mcp_servers.get("last9", {})
            last9_token = Prompt.ask(
                "    Last9 API Token",
                default="(existing)" if existing_last9 else "",
                password=True,
                console=console,
            )
            mcp_servers["last9"] = {
                "transport": "streamablehttp",
                "url": "https://app.last9.io/api/v4/organizations/last9/mcp",
                "headers": {"X-LAST9-API-TOKEN": "Bearer ${LAST9_API_TOKEN}"},
            }
            if last9_token and last9_token != "(existing)":
                new_secrets["LAST9_API_TOKEN"] = last9_token

    # GitHub uses `gh` CLI directly — no MCP server needed.
    # `gh auth login` handles authentication.

    if mcp_servers:
        config["mcp_servers"] = mcp_servers
