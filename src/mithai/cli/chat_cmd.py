"""mithai chat — shortcut for running with CLI adapter."""

import logging
import click

from mithai.core.config import load_config


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def chat(config_path, verbose):
    """Start an interactive CLI chat session (for development/testing)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = load_config(config_path)

    from mithai.adapters.cli import CLIAdapter
    from mithai.core.engine import Engine
    from mithai.cli.run_cmd import _create_llm, _create_state

    adapter = CLIAdapter()
    llm = _create_llm(config)
    state = _create_state(config)

    engine = Engine(config=config, llm=llm, state=state)

    try:
        adapter.start(on_message=engine.handle)
    except KeyboardInterrupt:
        click.echo("\nBye!")
    finally:
        adapter.stop()
