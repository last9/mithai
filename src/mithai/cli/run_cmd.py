"""mithai run — start the bot with configured adapters."""

import logging
import threading

import click

from mithai.core.config import get_adapter_config, get_adapter_types, get_llm_config, load_config


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option(
    "--adapter",
    "adapter_override",
    type=click.Choice(["cli", "slack", "telegram"]),
    default=None,
    help="Run only this adapter (overrides config)",
)
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def run(config_path, adapter_override, verbose):
    """Start mithai with configured adapters and skills."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(config_path)
    llm = _create_llm(config)
    state = _create_state(config)

    from mithai.core.engine import Engine
    engine = Engine(config=config, llm=llm, state=state)

    if adapter_override:
        adapter_types = [adapter_override]
    else:
        adapter_types = get_adapter_types(config)

    adapters = []
    for adapter_type in adapter_types:
        adapter = _create_adapter(config, adapter_type)
        adapters.append((adapter_type, adapter))

    # Give skills access to engine + adapter before starting
    engine.late_bind(adapters)

    if len(adapters) == 1:
        # Single adapter — run in main thread
        name, adapter = adapters[0]
        click.echo(f"Starting mithai with {name} adapter...")
        try:
            adapter.start(on_message=engine.handle)
        except KeyboardInterrupt:
            click.echo("\nShutting down...")
        finally:
            adapter.stop()
    else:
        # Multiple adapters — each in its own thread
        names = [name for name, _ in adapters]
        click.echo(f"Starting mithai with adapters: {', '.join(names)}")

        threads = []
        for name, adapter in adapters:
            t = threading.Thread(
                target=_run_adapter,
                args=(name, adapter, engine),
                daemon=True,
            )
            t.start()
            threads.append(t)

        try:
            # Wait for any thread to finish (or Ctrl+C)
            for t in threads:
                t.join()
        except KeyboardInterrupt:
            click.echo("\nShutting down...")
            for _, adapter in adapters:
                adapter.stop()


def _run_adapter(name: str, adapter, engine):
    """Run a single adapter in a thread."""
    logger = logging.getLogger(f"mithai.adapter.{name}")
    try:
        logger.info("Starting %s adapter", name)
        adapter.start(on_message=engine.handle)
    except Exception:
        logger.exception("Adapter %s crashed", name)
    finally:
        adapter.stop()


def _create_adapter(config: dict, adapter_type: str):
    adapter_config = get_adapter_config(config, adapter_type)

    if adapter_type == "cli":
        from mithai.adapters.cli import CLIAdapter
        return CLIAdapter()

    elif adapter_type == "slack":
        from mithai.adapters.slack import SlackAdapter
        return SlackAdapter(
            bot_token=adapter_config["bot_token"],
            app_token=adapter_config["app_token"],
            allowed_channels=adapter_config.get("allowed_channels"),
        )

    elif adapter_type == "telegram":
        from mithai.adapters.telegram import TelegramAdapter
        return TelegramAdapter(
            bot_token=adapter_config["bot_token"],
            allowed_chat_ids=adapter_config.get("allowed_chat_ids"),
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
