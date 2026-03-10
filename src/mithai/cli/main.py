"""CLI entry point for mithai."""

import difflib

import click

from mithai import get_version_string
from mithai.cli.style import console, BANNER_SMALL


class MithaiGroup(click.Group):
    """Custom group with styled help and 'did you mean?' suggestions."""

    def format_help(self, ctx, formatter):
        console.print(f"\n  {BANNER_SMALL} [muted]v{get_version_string()}[/]")
        console.print("  [muted]AI agent framework for organizations[/]\n")
        super().format_help(ctx, formatter)

    def resolve_command(self, ctx, args):
        """Override to suggest similar commands on typos."""
        cmd_name = args[0] if args else None
        cmd = self.get_command(ctx, cmd_name) if cmd_name else None

        if cmd is None and cmd_name:
            matches = difflib.get_close_matches(
                cmd_name, self.list_commands(ctx), n=1, cutoff=0.5
            )
            if matches:
                console.print(
                    f"\n  [red]Unknown command:[/] [white]{cmd_name}[/]"
                )
                console.print(
                    f"  [yellow]Did you mean:[/] [bright_cyan]{matches[0]}[/]\n"
                )
                ctx.exit(2)

        return super().resolve_command(ctx, args)


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
from mithai.cli.logs_cmd import logs  # noqa: E402
from mithai.cli.status_cmd import status  # noqa: E402

cli.add_command(run)
cli.add_command(chat)
cli.add_command(init)
cli.add_command(skill)
cli.add_command(service)
cli.add_command(doctor)
cli.add_command(ui)
cli.add_command(agent)
cli.add_command(logs)
cli.add_command(status)
