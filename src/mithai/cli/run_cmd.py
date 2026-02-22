"""mithai run — start the bot with configured adapter."""

import logging
import click

from mithai.core.config import get_adapter_config, get_llm_config, load_config


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option(
    "--adapter",
    "adapter_override",
    type=click.Choice(["cli", "slack", "telegram"]),
    default=None,
    help="Override adapter type from config",
)
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def run(config_path, adapter_override, verbose):
    """Start mithai with the configured adapter and skills."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(config_path)

    if adapter_override:
        config["adapter"]["type"] = adapter_override

    adapter = _create_adapter(config)
    llm = _create_llm(config)
    state = _create_state(config)

    from mithai.core.engine import Engine

    engine = Engine(config=config, adapter=adapter, llm=llm, state=state)

    click.echo(f"Starting mithai with {config['adapter']['type']} adapter...")

    try:
        adapter.start(on_message=engine.handle)
    except KeyboardInterrupt:
        click.echo("\nShutting down...")
    finally:
        adapter.stop()


def _create_adapter(config: dict):
    adapter_type = config["adapter"]["type"]
    adapter_config = get_adapter_config(config)

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
            model=llm_config.get("model", "claude-sonnet-4-5-20241022"),
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
