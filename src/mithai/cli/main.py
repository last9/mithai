"""CLI entry point for mithai."""

import click

from mithai import __version__


@click.group()
@click.version_option(version=__version__, prog_name="mithai")
def cli():
    """mithai — AI agent framework for infrastructure operations."""
    pass


# Import and register subcommands
from mithai.cli.run_cmd import run  # noqa: E402
from mithai.cli.chat_cmd import chat  # noqa: E402
from mithai.cli.init_cmd import init  # noqa: E402
from mithai.cli.skill_cmd import skill  # noqa: E402

cli.add_command(run)
cli.add_command(chat)
cli.add_command(init)
cli.add_command(skill)
