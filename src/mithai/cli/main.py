"""CLI entry point for mithai."""

import click

from mithai import get_version_string
from mithai.cli.style import console, BANNER_SMALL


class MithaiGroup(click.Group):
    """Custom group that shows a styled help page."""

    def format_help(self, ctx, formatter):
        console.print(f"\n  {BANNER_SMALL} [muted]v{get_version_string()}[/]")
        console.print("  [muted]AI agent framework for organizations[/]\n")
        super().format_help(ctx, formatter)


@click.group(cls=MithaiGroup)
@click.version_option(version=get_version_string(), prog_name="mithai")
def cli():
    """mithai — AI agent framework for organizations."""
    pass


# Import and register subcommands
from mithai.cli.run_cmd import run  # noqa: E402
from mithai.cli.chat_cmd import chat  # noqa: E402
from mithai.cli.init_cmd import init  # noqa: E402
from mithai.cli.skill_cmd import skill  # noqa: E402
from mithai.cli.service_cmd import service  # noqa: E402
from mithai.cli.doctor_cmd import doctor  # noqa: E402
from mithai.cli.ui_cmd import ui  # noqa: E402
from mithai.cli.agent_cmd import agent  # noqa: E402

cli.add_command(run)
cli.add_command(chat)
cli.add_command(init)
cli.add_command(skill)
cli.add_command(service)
cli.add_command(doctor)
cli.add_command(ui)
cli.add_command(agent)
