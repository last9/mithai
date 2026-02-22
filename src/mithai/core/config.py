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
    if "type" not in config.get("adapter", {}):
        raise ValueError("Config adapter must have a 'type' field")
    if "llm" not in config:
        raise ValueError("Config must have an 'llm' section")


def get_adapter_config(config: dict) -> dict:
    """Extract adapter-specific config based on adapter type."""
    adapter = config["adapter"]
    adapter_type = adapter["type"]
    return adapter.get(adapter_type, {})


def get_llm_config(config: dict) -> dict:
    """Extract LLM provider-specific config."""
    llm = config["llm"]
    provider = llm.get("provider", "anthropic")
    provider_config = llm.get(provider, {})
    return {
        "provider": provider,
        "model": llm.get("model", "claude-sonnet-4-5-20241022"),
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
