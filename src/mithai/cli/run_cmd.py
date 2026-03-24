"""mithai run — start the bot with configured adapters."""

import logging
import threading

import click

from mithai.cli.style import banner_small, console, info, kv, ok, section, setup_logging
from mithai.core.config import (
    get_adapter_config,
    get_adapter_types,
    get_agent_config,
    get_agents,
    get_llm_config,
    get_skill_paths,
    load_config,
)

logger = logging.getLogger(__name__)


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option(
    "--adapter",
    "adapter_override",
    type=click.Choice(["cli", "slack", "slack_http", "telegram"]),
    default=None,
    help="Run only this adapter (overrides config, single-agent mode only)",
)
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def run(config_path, adapter_override, verbose):
    """Start mithai with configured adapters and skills."""
    setup_logging(verbose)

    config = load_config(config_path)

    from mithai.telemetry import setup_telemetry
    setup_telemetry(config)

    try:
        agents_config = get_agents(config)

        if agents_config:
            _run_multi_agent(config, agents_config)
        else:
            _run_single_agent(config, adapter_override)
    except (RuntimeError, ImportError) as exc:
        console.print(f"\n  [red bold]Error:[/] {exc}\n")
        raise SystemExit(1) from None


def _maybe_startup_onboard(engine, adapter, on_join) -> None:
    """Start onboarding daemon thread if the adapter supports it."""
    if on_join and hasattr(adapter, "startup_onboard"):
        threading.Thread(
            target=adapter.startup_onboard,
            args=(engine.is_channel_onboarded, on_join),
            daemon=True,
        ).start()


def _run_single_agent(config: dict, adapter_override: str | None):
    """Single-agent mode — one engine, adapters from global config."""
    engines = _create_engine_single(config)
    engine = engines["default"]

    if adapter_override:
        adapter_types = [adapter_override]
    else:
        adapter_types = get_adapter_types(config)

    adapters = []
    for adapter_type in adapter_types:
        adapter_config = get_adapter_config(config, adapter_type)
        respond = adapter_config.get("respond", "all")
        adapter = _create_adapter(config, adapter_type, respond=respond)
        adapters.append((adapter_type, adapter))

    engine.late_bind(adapters)

    # Give CLI adapter engine ref for slash commands
    for _, adapter in adapters:
        if hasattr(adapter, "set_engine"):
            adapter.set_engine(engine)

    heartbeat = _start_heartbeat(config, engine)

    # Show startup info
    banner_small("run")
    llm_config = get_llm_config(config)
    kv("LLM", f"{llm_config.get('provider', '?')} / {llm_config.get('model', '?')}", indent=4)
    kv("Skills", f"{len(engine._skills)} loaded ({', '.join(sorted(engine._skills.keys()))})", indent=4)

    if engine._mcp_manager:
        servers = list(engine._mcp_manager._configs.keys())
        if servers:
            kv("MCP", ", ".join(servers), indent=4)

    adapter_names = [name for name, _ in adapters]
    kv("Adapters", ", ".join(adapter_names), indent=4)
    console.print()

    if len(adapters) == 1:
        name, adapter = adapters[0]
        ok(f"Starting with [bright_cyan]{name}[/] adapter")
        console.print()
        on_join = engine.handle_channel_join if name in ("slack", "slack_http") else None
        on_observe = engine.observe
        on_bot_reply = engine.log_outgoing if name in ("slack", "slack_http") else None
        _maybe_startup_onboard(engine, adapter, on_join)
        try:
            adapter.start(on_message=engine.handle, on_channel_join=on_join, on_observe=on_observe,
                          on_bot_reply=on_bot_reply)
        except KeyboardInterrupt:
            console.print("\n  [muted]Shutting down...[/]")
        finally:
            adapter.stop()
            if heartbeat:
                heartbeat.stop()
    else:
        ok(f"Starting with adapters: [bright_cyan]{', '.join(adapter_names)}[/]")
        console.print()

        threads = []
        for name, adapter in adapters:
            on_join = engine.handle_channel_join if name in ("slack", "slack_http") else None
            on_observe = engine.observe
            on_bot_reply = engine.log_outgoing if name in ("slack", "slack_http") else None
            t = threading.Thread(
                target=_run_adapter,
                args=(name, adapter, engine.handle, on_join, on_observe, on_bot_reply),
                daemon=True,
            )
            t.start()
            threads.append(t)

        try:
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            console.print("\n  [muted]Shutting down...[/]")
            for _, adapter in adapters:
                adapter.stop()
            if heartbeat:
                heartbeat.stop()


def _run_multi_agent(config: dict, agents_config: dict):
    """Multi-agent mode — each agent gets its own adapter(s), wired to its own engine."""
    engines = _create_engines_multi(config, agents_config)

    # Create per-agent adapters from agents.<id>.adapter config
    all_adapters: list[tuple[str, str, object, object]] = []  # (agent_id, adapter_type, adapter, engine)
    for agent_id, agent_def in agents_config.items():
        engine = engines[agent_id]
        agent_adapter_cfg = agent_def.get("adapter", {})
        for adapter_type, type_cfg in agent_adapter_cfg.items():
            respond = agent_def.get("respond") or type_cfg.get("respond", "all")
            adapter = _create_adapter(config, adapter_type, adapter_config=type_cfg, respond=respond)
            all_adapters.append((agent_id, adapter_type, adapter, engine))

    if not all_adapters:
        raise click.ClickException(
            "Multi-agent mode requires at least one agent with an 'adapter' section"
        )

    # Late-bind each engine with its own adapters
    heartbeats = []
    for agent_id, engine in engines.items():
        agent_adapters = [(t, a) for aid, t, a, _ in all_adapters if aid == agent_id]
        engine.late_bind(agent_adapters)
        hb = _start_heartbeat(get_agent_config(config, agent_id), engine)
        if hb:
            heartbeats.append(hb)

    # Show startup info
    banner_small("multi-agent")
    section("Agents")
    for agent_id, engine in engines.items():
        agent_adapters = [(t, a) for aid, t, a, _ in all_adapters if aid == agent_id]
        adapter_names = [t for t, _ in agent_adapters]
        console.print(
            f"    [bright_cyan]{agent_id}[/]  "
            f"[muted]skills=[/]{len(engine._skills)}  "
            f"[muted]adapters=[/]{', '.join(adapter_names)}"
        )
    console.print()

    # Start all adapters in daemon threads
    threads = []
    for agent_id, adapter_type, adapter, engine in all_adapters:
        label = f"{agent_id}/{adapter_type}"
        info(f"Starting [bright_cyan]{label}[/]")
        on_join = engine.handle_channel_join if adapter_type in ("slack", "slack_http") else None
        on_observe = engine.observe
        on_bot_reply = engine.log_outgoing if adapter_type in ("slack", "slack_http") else None
        _maybe_startup_onboard(engine, adapter, on_join)
        t = threading.Thread(
            target=_run_adapter,
            args=(label, adapter, engine.handle, on_join, on_observe, on_bot_reply),
            daemon=True,
        )
        t.start()
        threads.append(t)

    console.print()

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        console.print("\n  [muted]Shutting down...[/]")
        for _, _, adapter, _ in all_adapters:
            adapter.stop()
        for hb in heartbeats:
            hb.stop()


def _run_adapter(name: str, adapter, on_message, on_channel_join=None, on_observe=None, on_bot_reply=None):
    """Run a single adapter in a thread."""
    logger = logging.getLogger(f"mithai.adapter.{name}")
    try:
        logger.info("Starting %s adapter", name)
        adapter.start(on_message=on_message, on_channel_join=on_channel_join, on_observe=on_observe,
                      on_bot_reply=on_bot_reply)
    except Exception:
        logger.exception("Adapter %s crashed", name)
    finally:
        adapter.stop()


def _create_engine_single(config: dict) -> dict:
    """Create a single engine (no agents: config). Returns {"default": engine}."""
    from mithai.core.engine import Engine

    llm = _create_llm(config)
    state = _create_state(config)
    memory = _create_memory_backend(config)
    engine = Engine(config=config, llm=llm, state=state, memory=memory)
    return {"default": engine}


def _create_engines_multi(config: dict, agents_config: dict) -> dict:
    """Create one Engine per agent with filtered skills and isolated memory."""
    from mithai.core.engine import Engine
    from mithai.core.skill_loader import load_skills, filter_skills

    llm = _create_llm(config)
    state = _create_state(config)

    # Load all skills once, then filter per agent
    skill_paths = get_skill_paths(config)
    all_skills = load_skills(skill_paths)

    engines = {}
    for agent_id, agent_def in agents_config.items():
        # Filter skills by allowlist
        allowed = agent_def.get("skills", {}).get("allowed")
        if allowed:
            agent_skills = filter_skills(all_skills, allowed)
        else:
            agent_skills = dict(all_skills)

        # Agent-specific memory backend
        memory_path = agent_def.get("memory", {}).get("path")
        if memory_path:
            from mithai.memory.filesystem import FilesystemMemoryBackend
            agent_memory = FilesystemMemoryBackend(memory_path)
        else:
            agent_memory = _create_memory_backend(config)

        # Merge agent config on top of global
        agent_config = get_agent_config(config, agent_id)

        engine = Engine(
            config=agent_config,
            llm=llm,
            state=state,
            memory=agent_memory,
            agent_id=agent_id,
            skills=agent_skills,
        )
        engines[agent_id] = engine

    return engines


def _parse_id_list(value) -> list | None:
    """Coerce a config value to a list of strings.

    Handles three forms that can arrive after env-var substitution:
      - None / missing          → None (no allowlist)
      - list                    → returned as-is
      - "C1,C2, C3"             → ["C1", "C2", "C3"]  (comma-separated env var)
      - "${UNRESOLVED}"         → raises ValueError (unset env var — fail fast)
    """
    if value is None:
        return None
    if isinstance(value, list):
        return value
    items = [item.strip() for item in str(value).split(",") if item.strip()]
    unresolved = [item for item in items if item.startswith("${") and item.endswith("}")]
    if unresolved:
        raise ValueError(
            f"Unresolved env var placeholder in allowlist: {unresolved[0]}. "
            "Set the environment variable or remove the placeholder."
        )
    return items


def _create_adapter(config: dict, adapter_type: str, adapter_config: dict | None = None,
                    respond: str = "all"):
    """Create an adapter instance.

    If adapter_config is provided (per-agent mode), use it directly.
    Otherwise fall back to global adapter config.
    """
    if adapter_config is None:
        adapter_config = get_adapter_config(config, adapter_type)

    if adapter_type == "cli":
        from mithai.adapters.cli import CLIAdapter
        return CLIAdapter()

    elif adapter_type == "slack":
        from mithai.adapters.slack import SlackAdapter
        return SlackAdapter(
            bot_token=adapter_config["bot_token"],
            app_token=adapter_config["app_token"],
            allowed_channels=_parse_id_list(adapter_config.get("allowed_channels")),
            approval_timeout=adapter_config.get("approval_timeout", 300),
            respond=respond,
        )

    elif adapter_type == "telegram":
        from mithai.adapters.telegram import TelegramAdapter
        return TelegramAdapter(
            bot_token=adapter_config["bot_token"],
            allowed_chat_ids=_parse_id_list(adapter_config.get("allowed_chat_ids")),
        )

    elif adapter_type == "slack_http":
        from mithai.adapters.slack_http import SlackHTTPAdapter
        return SlackHTTPAdapter(
            bot_token=adapter_config["bot_token"],
            signing_secret=adapter_config["signing_secret"],
            host=adapter_config.get("host", "0.0.0.0"),
            port=adapter_config.get("port", 3000),
            allowed_channels=_parse_id_list(adapter_config.get("allowed_channels")),
            approval_timeout=adapter_config.get("approval_timeout", 300),
            respond=respond,
        )

    else:
        raise click.ClickException(f"Unknown adapter type: {adapter_type}")


def _create_llm(config: dict):
    llm_config = get_llm_config(config)
    provider = llm_config["provider"]

    if provider == "anthropic":
        from mithai.llm.anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=llm_config["api_key"],
            model=llm_config.get("model", "claude-sonnet-4-6"),
        )

    else:
        raise click.ClickException(f"Unknown LLM provider: {provider}")


def _create_state(config: dict):
    state_config = config.get("state", {})
    backend = state_config.get("backend", "filesystem")

    if backend == "filesystem":
        from mithai.state.filesystem import FilesystemStateBackend
        path = state_config.get("filesystem", {}).get("path", "./.mithai/state")
        return FilesystemStateBackend(path)

    elif backend == "memory":
        from mithai.state.memory import MemoryStateBackend
        return MemoryStateBackend()

    else:
        raise click.ClickException(f"Unknown state backend: {backend}")


def _create_memory_backend(config: dict):
    learning = config.get("learning", {})

    # Backward compat: legacy memory_dir key
    memory_config = learning.get("memory", {})
    if not memory_config and "memory_dir" in learning:
        memory_config = {"backend": "filesystem", "filesystem": {"path": learning["memory_dir"]}}

    backend = memory_config.get("backend", "filesystem")

    if backend == "filesystem":
        from mithai.memory.filesystem import FilesystemMemoryBackend
        fs_config = memory_config.get("filesystem", {})
        path = fs_config.get("path", "./memory")
        return FilesystemMemoryBackend(path)

    elif backend == "redis":
        try:
            from mithai.memory.redis import RedisMemoryBackend
        except ImportError:
            raise click.ClickException(
                "Redis memory backend requires 'redis' package. "
                "Install with: pip install mithai[redis]"
            )
        redis_config = memory_config.get("redis", {})
        return RedisMemoryBackend(
            url=redis_config.get("url", "redis://localhost:6379"),
            prefix=redis_config.get("prefix", "mithai:memory"),
        )

    elif backend == "s3":
        try:
            from mithai.memory.s3 import S3MemoryBackend
        except ImportError:
            raise click.ClickException(
                "S3 memory backend requires 'boto3' package. "
                "Install with: pip install mithai[s3]"
            )
        s3_config = memory_config.get("s3", {})
        if "bucket" not in s3_config:
            raise click.ClickException("S3 memory backend requires 'bucket' in config")
        return S3MemoryBackend(
            bucket=s3_config["bucket"],
            prefix=s3_config.get("prefix", "memory"),
            region=s3_config.get("region"),
            profile=s3_config.get("profile"),
        )

    else:
        raise click.ClickException(f"Unknown memory backend: {backend}")


def _start_heartbeat(config: dict, engine):
    """Start a HeartbeatScheduler if enabled in config. Returns the scheduler or None."""
    hb_config = config.get("heartbeat", {})
    if not hb_config.get("enabled", False):
        return None
    if engine._memory is None:
        return None

    from mithai.core.heartbeat import HeartbeatScheduler, _DEFAULT_INTERVAL
    interval = int(hb_config.get("interval", _DEFAULT_INTERVAL))
    auto_approve = hb_config.get("auto_approve")  # None → HeartbeatScheduler applies its default
    scheduler = HeartbeatScheduler(engine, engine._memory, interval=interval, auto_approve=auto_approve)
    scheduler.start()
    return scheduler
