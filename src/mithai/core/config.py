"""Load and validate mithai configuration from config.yaml + .env."""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([^}]+)\}")
        def replacer(match):
            var_name = match.group(1)
            env_val = os.environ.get(var_name)
            if env_val is None:
                return match.group(0)  # Leave unresolved
            return env_val
        return pattern.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(config_path: str | Path | None = None, env_path: str | Path | None = None) -> dict:
    """
    Load mithai config from YAML file with env var resolution.

    Looks for config.yaml in the current directory if not specified.
    Loads .env from the same directory as config.yaml.
    """
    if config_path is None:
        config_path = Path.cwd() / "config.yaml"
    config_path = Path(config_path)

    # Load .env from config directory or explicit path
    if env_path:
        load_dotenv(Path(env_path))
    else:
        env_file = config_path.parent / ".env"
        if env_file.exists():
            load_dotenv(env_file)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    config = _resolve_env_vars(raw)
    _validate_config(config)
    return config


def _validate_config(config: dict) -> None:
    """Validate required config sections exist."""
    if "adapter" not in config:
        raise ValueError("Config must have an 'adapter' section")
    adapter = config["adapter"]
    has_type = "type" in adapter
    has_types = "types" in adapter
    if not has_type and not has_types:
        raise ValueError("Config adapter must have 'type' or 'types' field")
    if "llm" not in config:
        raise ValueError("Config must have an 'llm' section")


def get_adapter_types(config: dict) -> list[str]:
    """Get list of adapter types to run. Supports both 'type' and 'types'."""
    adapter = config["adapter"]
    if "types" in adapter:
        return adapter["types"]
    return [adapter["type"]]


def get_adapter_config(config: dict, adapter_type: str | None = None) -> dict:
    """Extract adapter-specific config for a given adapter type."""
    adapter = config["adapter"]
    if adapter_type is None:
        adapter_type = adapter.get("type", "cli")
    return adapter.get(adapter_type, {})


def get_llm_config(config: dict) -> dict:
    """Extract LLM provider-specific config."""
    llm = config["llm"]
    provider = llm.get("provider", "anthropic")
    provider_config = llm.get(provider, {})
    return {
        "provider": provider,
        "model": llm.get("model", "claude-sonnet-4-6"),
        "max_tokens": llm.get("max_tokens", 1024),
        **provider_config,
    }


def get_skill_config(config: dict, skill_name: str) -> dict:
    """Get config for a specific skill."""
    return config.get("skills", {}).get("config", {}).get(skill_name, {})


def get_skill_paths(config: dict) -> list[Path]:
    """Get list of directories to scan for skills."""
    paths = config.get("skills", {}).get("paths", ["./skills"])
    return [Path(p) for p in paths]


def get_human_config(config: dict) -> dict:
    """Get Human MCP configuration."""
    return config.get("human", {})


def get_agents(config: dict) -> dict[str, dict] | None:
    """Get the agents section from config, or None for single-agent mode."""
    agents = config.get("agents")
    if not agents:
        return None
    # Strip the default_agent meta-key — callers use get_default_agent_id()
    return {k: v for k, v in agents.items() if k != "default_agent"}


def get_default_agent_id(config: dict) -> str | None:
    """Get the default agent ID, or None for single-agent mode."""
    agents = config.get("agents")
    if not agents:
        return None
    return agents.get("default_agent")


def get_agent_config(config: dict, agent_id: str) -> dict:
    """
    Get config for a specific agent.

    Merges agent-level overrides on top of global config so agents
    inherit llm, skills.paths, state, learning, etc. by default.
    """
    agents = get_agents(config)
    if not agents or agent_id not in agents:
        return config

    agent = agents[agent_id]

    # Agent-level system_prompt overrides global bot.system_prompt
    merged = dict(config)
    if "system_prompt" in agent:
        merged = {**merged, "bot": {**merged.get("bot", {}), "system_prompt": agent["system_prompt"]}}

    return merged
