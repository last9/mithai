"""Load and validate mithai configuration from config.yaml + .env."""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError


# Slack Socket Mode adapter (adapter.slack)
# Ref: https://api.slack.com/apis/socket-mode
class SlackAdapterConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    bot_token: str | None = None     # xoxb-... OAuth bot token
    app_token: str | None = None     # xapp-... Socket Mode app-level token
    allowed_channels: str | list[str] | None = None  # channel IDs whitelist; str for comma-separated env var
    approval_timeout: int | None = None        # seconds; default 300
    respond: str | None = None                 # "all" or "mentions"


# Slack HTTP adapter (adapter.slack_http)
# Ref: https://api.slack.com/apis/http
class SlackHTTPAdapterConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    bot_token: str | None = None
    signing_secret: str | None = None  # HMAC signing secret for request verification
    host: str | None = None
    port: int | None = None
    allowed_channels: str | list[str] | None = None  # str for comma-separated env var
    approval_timeout: int | None = None
    respond: str | None = None


# Telegram Bot adapter (adapter.telegram)
# Ref: https://core.telegram.org/bots/api — chat_id is a signed 64-bit integer
class TelegramAdapterConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    bot_token: str | None = None
    allowed_chat_ids: str | list[str | int] | None = None  # str for comma-separated; list allows unresolved ${} placeholders


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str | None = None
    types: list[str] | None = None
    slack: SlackAdapterConfig | None = None
    slack_http: SlackHTTPAdapterConfig | None = None
    telegram: TelegramAdapterConfig | None = None


class LLMAnthropicConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    api_key: str | None = None


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    anthropic: LLMAnthropicConfig | None = None


class FilesystemMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str | None = None


# Ref: https://redis-py.readthedocs.io/en/stable/connections.html
class RedisMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str | None = None    # redis://host:port
    prefix: str | None = None


# Ref: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html
class S3MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    bucket: str | None = None
    prefix: str | None = None
    region: str | None = None
    profile: str | None = None  # AWS CLI named profile


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    backend: str | None = None
    filesystem: FilesystemMemoryConfig | None = None
    redis: RedisMemoryConfig | None = None
    s3: S3MemoryConfig | None = None
    memory_dir: str | None = None  # legacy flat path field


class LearningConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool | None = None
    reflection: bool | None = None
    approval_auto_promote: int | None = None  # threshold count, not a flag
    memory: MemoryConfig | None = None


class HeartbeatConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool | None = None
    interval: int | None = None                        # seconds
    auto_approve: list[str] | str | None = None        # tool name prefixes to skip approval


class StateFilesystemConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str | None = None


class StateConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    backend: str | None = None
    filesystem: StateFilesystemConfig | None = None


class UIConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    host: str | None = None
    port: int | None = None
    auth_token: str | None = None


class AgentSkillsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    allowed: list[str] | None = None


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None
    system_prompt: str | None = None
    skills: AgentSkillsConfig | None = None
    memory: MemoryConfig | None = None
    adapter: AdapterConfig | None = None
    learning: LearningConfig | None = None


class BotConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None
    system_prompt: str | None = None


class SkillsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    paths: list[str] | None = None
    config: dict[str, dict] | None = None


class SessionsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    max_stored: int | None = None   # max sessions retained on disk
    max_history: int | None = None  # max turns sent to LLM context


class OnboardingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool | None = None


class HumanConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    timeout_seconds: int | None = None
    overrides: dict[str, str | None] | None = None  # tool prefix → "approve"|"confirm"|None


class OTLPConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    endpoint: str | None = None              # e.g. http://localhost:4318
    headers: dict[str, str] | None = None    # e.g. {"Authorization": "Bearer ..."}


class TelemetrySamplingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    ratio: float | None = None   # 0.0–1.0; default 1.0 (sample all)


class TelemetryLogsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool | None = None
    level: str | None = None   # e.g. "WARNING", "ERROR" — min Python log level to bridge


class TelemetryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool | None = None
    service_name: str | None = None
    exporter: str | None = None   # "otlp" | "stdout" | "none"
    otlp: OTLPConfig | None = None
    sampling: TelemetrySamplingConfig | None = None
    logs: TelemetryLogsConfig | None = None


class MithaiConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    bot: BotConfig | None = None
    adapter: AdapterConfig | None = None
    llm: LLMConfig | None = None
    mcp_servers: dict | None = None
    skills: SkillsConfig | None = None
    learning: LearningConfig | None = None
    heartbeat: HeartbeatConfig | None = None
    agents: dict[str, AgentConfig] | None = None
    default_agent: str | None = None
    state: StateConfig | None = None
    ui: UIConfig | None = None
    sessions: SessionsConfig | None = None
    onboarding: OnboardingConfig | None = None
    human: HumanConfig | None = None
    telemetry: TelemetryConfig | None = None


def _validate_config_schema(config: dict) -> None:
    try:
        MithaiConfig.model_validate(config)
    except ValidationError as e:
        lines = ["Config validation failed:"]
        for err in e.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            lines.append(f"  [{loc}] {err['msg']}")
        raise ValueError("\n".join(lines)) from None


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([^}]+)\}")
        def replacer(match):
            expr = match.group(1)
            if ":-" in expr:
                var_name, default = expr.split(":-", 1)
            else:
                var_name, default = expr, None
            env_val = os.environ.get(var_name)
            if env_val is None:
                return default if default is not None else match.group(0)
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
    _validate_config_schema(config)
    return config


def _validate_config(config: dict) -> None:
    """Validate required config sections exist.

    Multi-agent configs define adapters under `agents.<id>.adapter` and may
    omit the top-level `adapter` / `llm` sections, so those checks are skipped
    when an `agents` section is present.
    """
    is_multi_agent = bool(config.get("agents"))

    if not is_multi_agent:
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
    """Get list of directories to scan for skills.

    Resolution order (later overrides earlier):
    1. Bundled skills inside the PyInstaller binary (lowest priority)
    2. Config-specified paths (e.g., ./skills)
    3. User-installed skills at ~/.mithai/skills/ (highest priority)
    """
    from mithai import get_bundled_path

    result = []

    # 1. Bundled skills (inside PyInstaller binary or repo root)
    bundled = get_bundled_path() / "skills"
    if bundled.exists():
        result.append(bundled)

    # 2. Config-specified paths
    config_paths = config.get("skills", {}).get("paths", ["./skills"])
    for p in config_paths:
        path = Path(p)
        if path.resolve() != bundled.resolve():
            result.append(path)

    # 3. User-installed skills (~/.mithai/skills/)
    user_skills = Path.home() / ".mithai" / "skills"
    if user_skills.exists():
        result.append(user_skills)

    return result


def get_mcp_config(config: dict) -> dict:
    """Get MCP server configurations. Returns empty dict if none configured."""
    return config.get("mcp_servers", {})


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

    merged = dict(config)

    # Agent-level name/system_prompt override global bot fields
    bot_overrides = {}
    if "name" in agent:
        bot_overrides["name"] = agent["name"]
    if "system_prompt" in agent:
        bot_overrides["system_prompt"] = agent["system_prompt"]
    if bot_overrides:
        merged = {**merged, "bot": {**merged.get("bot", {}), **bot_overrides}}

    # Deep-merge agent-level top-level section overrides
    for key in ("onboarding", "learning", "sessions"):
        if key in agent:
            merged[key] = {**merged.get(key, {}), **agent[key]}

    return merged
