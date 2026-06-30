"""mithai doctor — diagnostics and health checks."""

import os
import subprocess
from pathlib import Path

import click

from mithai import get_version_string
from mithai.cli.style import banner_small, console, fail, kv, ok, section
from mithai.core.config import load_config, get_skill_paths, get_memory_dir, get_state_dir
from mithai.core.skill_loader import load_skills


def _check_llm(config: dict) -> bool:
    """Check LLM connectivity."""
    llm = config.get("llm", {})
    provider = llm.get("provider", "anthropic")
    model = llm.get("model", "unknown")

    if provider == "anthropic":
        api_key_ref = llm.get("anthropic", {}).get("api_key", "")
        api_key = api_key_ref
        if api_key_ref.startswith("${") and api_key_ref.endswith("}"):
            env_var = api_key_ref[2:-1]
            api_key = os.environ.get(env_var, "")

        if not api_key:
            fail(f"LLM ({provider} {model}): API key not set")
            return False

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            client.messages.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            ok(f"LLM [bright_cyan]{provider}[/] / [white]{model}[/]: connected")
            return True
        except Exception as e:
            fail(f"LLM ({provider} {model}): {e}")
            return False
    else:
        fail(f"LLM ({provider}): unsupported provider for health check")
        return False


def _check_adapters(config: dict) -> int:
    """Check adapter connectivity. Returns number of failures."""
    adapters = config.get("adapter", {})
    types = adapters.get("types", [])
    failures = 0

    if "slack" in types:
        bot_ref = adapters.get("slack", {}).get("bot_token", "")
        bot_token = bot_ref
        if bot_ref.startswith("${") and bot_ref.endswith("}"):
            bot_token = os.environ.get(bot_ref[2:-1], "")

        if bot_token:
            try:
                from slack_sdk import WebClient
                client = WebClient(token=bot_token)
                result = client.auth_test()
                team = result.get("team", "unknown")
                ok(f"Slack: connected (workspace: [white]{team}[/])")
            except Exception as e:
                fail(f"Slack: {e}")
                failures += 1
        else:
            fail("Slack: token not set")
            failures += 1

    if "telegram" in types:
        tg_ref = adapters.get("telegram", {}).get("bot_token", "")
        tg_token = tg_ref
        if tg_ref.startswith("${") and tg_ref.endswith("}"):
            tg_token = os.environ.get(tg_ref[2:-1], "")

        if tg_token:
            try:
                import requests
                resp = requests.get(
                    f"https://api.telegram.org/bot{tg_token}/getMe", timeout=10
                )
                data = resp.json()
                if data.get("ok"):
                    ok(f"Telegram: connected (@{data['result'].get('username', '?')})")
                else:
                    fail(f"Telegram: {data.get('description', 'error')}")
                    failures += 1
            except Exception as e:
                fail(f"Telegram: {e}")
                failures += 1
        else:
            fail("Telegram: token not set")
            failures += 1

    if "cli" in types:
        ok("CLI adapter: available")

    return failures


def _check_kubectl(config: dict) -> int:
    """Check kubectl and cluster connectivity. Returns number of failures."""
    k8s = config.get("skills", {}).get("config", {}).get("kubernetes", {})
    if not k8s:
        return 0

    failures = 0

    # Check kubectl binary
    try:
        result = subprocess.run(
            ["kubectl", "version", "--client", "-o", "json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            import json
            ver = json.loads(result.stdout)
            version = ver.get("clientVersion", {}).get("gitVersion", "unknown")
            ok(f"kubectl: [white]{version}[/]")
        else:
            fail("kubectl: not working")
            failures += 1
            return failures
    except FileNotFoundError:
        fail("kubectl: not found")
        return 1

    # Check clusters
    kubeconfig = k8s.get("kubeconfig", "")
    if kubeconfig:
        env = dict(os.environ)
        env["KUBECONFIG"] = kubeconfig

        try:
            result = subprocess.run(
                ["kubectl", "config", "get-contexts", "-o", "name"],
                capture_output=True, text=True, timeout=10, env=env,
            )
            contexts = [c.strip() for c in result.stdout.strip().split("\n") if c.strip()]

            for ctx in contexts:
                try:
                    cr = subprocess.run(
                        ["kubectl", "cluster-info", "--context", ctx],
                        capture_output=True, text=True, timeout=15, env=env,
                    )
                    if cr.returncode == 0:
                        ok(f"Cluster [white]{ctx}[/]: reachable")
                    else:
                        err = cr.stderr.strip()[:80]
                        fail(f"Cluster {ctx}: {err}")
                        failures += 1
                except subprocess.TimeoutExpired:
                    fail(f"Cluster {ctx}: timeout")
                    failures += 1
        except Exception as e:
            fail(f"kubectl contexts: {e}")
            failures += 1

    return failures


def _check_gh_cli() -> bool:
    """Check GitHub CLI availability."""
    try:
        result = subprocess.run(
            ["gh", "--version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            version = result.stdout.strip().splitlines()[0]
            ok(f"GitHub CLI: [white]{version}[/]")
            return True
    except FileNotFoundError:
        pass
    fail("GitHub CLI (gh): not found")
    return False


def _check_skills(config: dict) -> int:
    """Check loaded skills. Returns number of failures."""
    try:
        skill_paths = get_skill_paths(config)
        loaded = load_skills(skill_paths)
        core = sum(1 for s in loaded.values() if s.name in {"shell", "memory", "sessions", "http_checker"})
        optional = len(loaded) - core
        ok(f"Skills: [white]{len(loaded)}[/] loaded ({core} core + {optional} optional)")
        return 0
    except Exception as e:
        fail(f"Skills: {e}")
        return 1


def _configured_memory_dir(config: dict) -> Path | None:
    memory_dir = get_memory_dir(config)
    if memory_dir is None:
        backend = config.get("learning", {}).get("memory", {}).get("backend", "filesystem")
        ok(f"Memory backend: [white]{backend}[/] (no local directory)")
    return memory_dir


def _configured_state_dir(config: dict) -> Path | None:
    state_dir = get_state_dir(config)
    if state_dir is None:
        backend = config.get("state", {}).get("backend", "filesystem")
        ok(f"State backend: [white]{backend}[/] (no local directory)")
    return state_dir


def _check_directories(config: dict) -> int:
    """Check writable directories. Returns number of failures."""
    failures = 0
    paths = []
    memory_dir = _configured_memory_dir(config)
    state_dir = _configured_state_dir(config)
    if memory_dir is not None:
        paths.append(("Memory dir", memory_dir))
    if state_dir is not None:
        paths.append(("State dir", state_dir))

    for name, path in paths:
        if path.exists() and os.access(path, os.W_OK):
            ok(f"{name}: [muted]{path}[/]")
        elif path.exists():
            fail(f"{name}: {path} (not writable)")
            failures += 1
        else:
            fail(f"{name}: {path} (does not exist)")
            failures += 1
    return failures


def _check_mcp(config: dict) -> int:
    """Check MCP server configuration."""
    mcp_servers = config.get("mcp_servers", {})
    if not mcp_servers:
        return 0

    failures = 0
    for name, server_cfg in mcp_servers.items():
        transport = server_cfg.get("transport", "stdio")
        url = server_cfg.get("url", "")
        if url:
            ok(f"MCP [white]{name}[/]: configured ({transport} → {url[:50]})")
        else:
            ok(f"MCP [white]{name}[/]: configured ({transport})")
    return failures


@click.command()
@click.option("--config", "config_path", default="config.yaml",
              help="Path to config.yaml")
def doctor(config_path):
    """Run diagnostics — check config, connections, and dependencies."""
    config_file = Path(config_path)

    banner_small("doctor")
    kv("Version", get_version_string(), indent=4)
    kv("Config", str(config_file), indent=4)
    console.print()

    if not config_file.exists():
        fail("Config file not found.")
        console.print("    Run [bright_cyan]mithai init[/] to create one.")
        raise SystemExit(1)

    # Load env file
    env_file = config_file.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    config = load_config(str(config_file))

    issues = 0

    # LLM
    section("LLM")
    if not _check_llm(config):
        issues += 1

    # Adapters
    section("Adapters")
    issues += _check_adapters(config)

    # MCP
    mcp_servers = config.get("mcp_servers", {})
    if mcp_servers:
        section("MCP Servers")
        issues += _check_mcp(config)

    # kubectl
    k8s = config.get("skills", {}).get("config", {}).get("kubernetes", {})
    if k8s:
        section("Kubernetes")
        issues += _check_kubectl(config)

    # gh CLI (check if github skill is used)
    skill_paths = get_skill_paths(config)
    loaded = load_skills(skill_paths)
    if "github" in loaded or "exception_fixer" in loaded:
        section("GitHub")
        if not _check_gh_cli():
            issues += 1

    # Skills
    section("Skills")
    issues += _check_skills(config)

    # Directories
    section("Filesystem")
    issues += _check_directories(config)

    console.print()
    if issues == 0:
        console.print("  [green bold]All checks passed.[/]")
    else:
        console.print(f"  [yellow bold]{issues} issue(s) found.[/]")

    console.print()
    raise SystemExit(1 if issues > 0 else 0)
