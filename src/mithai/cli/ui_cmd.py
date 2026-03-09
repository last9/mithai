"""mithai ui — start the Control Room web interface."""

import click

from mithai.cli.style import banner_small, console, kv, setup_logging
from mithai.core.config import load_config


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--host", default=None, help="Bind host (overrides config)")
@click.option("--port", default=None, type=int, help="Bind port (overrides config)")
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def ui(config_path, host, port, verbose):
    """Start the Control Room web UI."""
    setup_logging(verbose)

    try:
        import uvicorn
    except ImportError:
        raise click.ClickException(
            "Control Room requires 'starlette' and 'uvicorn'. "
            "Install with: pip install mithai[ui]"
        )

    config = load_config(config_path)
    ui_config = config.get("ui", {})

    auth_token = ui_config.get("auth_token", "")
    has_auth = auth_token and not auth_token.startswith("${")

    # Default to localhost when no auth token — avoid exposing UI on all interfaces
    default_host = "0.0.0.0" if has_auth else "127.0.0.1"
    bind_host = host or ui_config.get("host", default_host)
    bind_port = port or ui_config.get("port", 8420)

    # Import here to avoid ImportError if starlette not installed
    from mithai.ui.app import create_app
    from mithai.cli.style import warn

    app = create_app(config)

    banner_small("Control Room")
    if not has_auth and bind_host != "127.0.0.1":
        warn("No auth_token configured — UI is accessible without authentication")
    kv("URL", f"http://{bind_host}:{bind_port}", indent=4)
    console.print()

    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info" if not verbose else "debug")
