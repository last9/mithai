"""mithai ui — start the Control Room web interface."""

import logging

import click

from mithai.core.config import load_config


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--host", default=None, help="Bind host (overrides config)")
@click.option("--port", default=None, type=int, help="Bind port (overrides config)")
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def ui(config_path, host, port, verbose):
    """Start the Control Room web UI."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    try:
        import uvicorn
    except ImportError:
        raise click.ClickException(
            "Control Room requires 'starlette' and 'uvicorn'. "
            "Install with: pip install mithai[ui]"
        )

    config = load_config(config_path)
    ui_config = config.get("ui", {})

    bind_host = host or ui_config.get("host", "0.0.0.0")
    bind_port = port or ui_config.get("port", 8420)

    # Import here to avoid ImportError if starlette not installed
    from mithai.ui.app import create_app

    app = create_app(config)

    click.echo(f"Starting Control Room on http://{bind_host}:{bind_port}")
    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info" if not verbose else "debug")
