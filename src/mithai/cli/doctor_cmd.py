"""mithai doctor — diagnostics and health checks."""

import os
import subprocess
from pathlib import Path

import click
import yaml

from mithai import get_version_string
from mithai.core.config import load_config, get_skill_paths
from mithai.core.skill_loader import load_skills


MITHAI_HOME = Path.home() / ".mithai"


def _result(ok: bool, msg: str):
    marker = click.style("✓", fg="green") if ok else click.style("✗", fg="red")
    click.echo(f"  {marker} {msg}")


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
            _result(False, f"LLM ({provider} {model}): API key not set")
            return False

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            client.messages.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            _result(True, f"LLM ({provider} {model}): connected")
            return True
        except Exception as e:
            _result(False, f"LLM ({provider} {model}): {e}")
            return False
    else:
        _result(False, f"LLM ({provider}): unsupported provider for health check")
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
                _result(True, f"Slack adapter: connected (workspace: {team})")
            except Exception as e:
                _result(False, f"Slack adapter: {e}")
                failures += 1
        else:
            _result(False, "Slack adapter: token not set")
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
                    _result(True, f"Telegram adapter: connected (@{data['result'].get('username', '?')})")
                else:
                    _result(False, f"Telegram adapter: {data.get('description', 'error')}")
                    failures += 1
            except Exception as e:
                _result(False, f"Telegram adapter: {e}")
                failures += 1
        else:
            _result(False, "Telegram adapter: token not set")
            failures += 1

    if "cli" in types:
        _result(True, "CLI adapter: available")

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
            _result(True, f"kubectl: {version}")
        else:
            _result(False, "kubectl: not working")
            failures += 1
            return failures
    except FileNotFoundError:
        _result(False, "kubectl: not found")
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
                        _result(True, f"Cluster {ctx}: reachable")
                    else:
                        err = cr.stderr.strip()[:80]
                        _result(False, f"Cluster {ctx}: {err}")
                        failures += 1
                except subprocess.TimeoutExpired:
                    _result(False, f"Cluster {ctx}: timeout")
                    failures += 1
        except Exception as e:
            _result(False, f"kubectl contexts: {e}")
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
            _result(True, f"GitHub CLI: {version}")
            return True
    except FileNotFoundError:
        pass
    _result(False, "GitHub CLI (gh): not found")
    return False


def _check_skills(config: dict, config_path: str) -> int:
    """Check loaded skills. Returns number of failures."""
    try:
        skill_paths = get_skill_paths(config)
        loaded = load_skills(skill_paths)
        core = sum(1 for s in loaded.values() if s.name in {"shell", "memory", "sessions", "http_checker"})
        optional = len(loaded) - core
        _result(True, f"Skills loaded: {len(loaded)} ({core} core + {optional} optional)")
        return 0
    except Exception as e:
        _result(False, f"Skills: {e}")
        return 1


def _check_directories() -> int:
    """Check writable directories. Returns number of failures."""
    failures = 0
    for name, path in [
        ("Memory dir", MITHAI_HOME / "memory"),
        ("State dir", MITHAI_HOME / "state"),
    ]:
        if path.exists() and os.access(path, os.W_OK):
            _result(True, f"{name}: {path} (writable)")
        elif path.exists():
            _result(False, f"{name}: {path} (not writable)")
            failures += 1
        else:
            _result(False, f"{name}: {path} (does not exist)")
            failures += 1
    return failures


@click.command()
@click.option("--config", "config_path", default=None,
              help="Path to config.yaml (default: ~/.mithai/config.yaml)")
def doctor(config_path):
    """Run diagnostics — check config, connections, and dependencies."""
    config_file = Path(config_path) if config_path else MITHAI_HOME / "config.yaml"

    click.echo()
    click.echo(click.style("Mithai Doctor", bold=True))
    click.echo("=" * 40)
    click.echo(f"  Version:  {get_version_string()}")
    click.echo(f"  Config:   {config_file}")
    click.echo()

    if not config_file.exists():
        click.echo(click.style("  Config file not found.", fg="red"))
        click.echo("  Run `mithai init` to create one.")
        raise SystemExit(1)

    # Load env file
    env_file = config_file.parent / "env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    config = load_config(str(config_file))

    click.echo("Checks:")
    issues = 0

    # LLM
    if not _check_llm(config):
        issues += 1

    # Adapters
    issues += _check_adapters(config)

    # kubectl
    issues += _check_kubectl(config)

    # gh CLI (check if github skill is used)
    skill_paths = get_skill_paths(config)
    loaded = load_skills(skill_paths)
    if "github" in loaded or "exception_fixer" in loaded:
        if not _check_gh_cli():
            issues += 1

    # Skills
    issues += _check_skills(config, str(config_file))

    # Directories
    issues += _check_directories()

    click.echo()
    if issues == 0:
        click.echo(click.style("All checks passed.", fg="green", bold=True))
    else:
        click.echo(click.style(f"{issues} issue(s) found.", fg="yellow", bold=True))

    raise SystemExit(1 if issues > 0 else 0)
