"""mithai logs — browse and search conversation history."""

import click
from rich.table import Table

from mithai.cli.style import banner_small, console, info, kv
from mithai.core.config import load_config


@click.group()
def logs():
    """Browse conversation history and session logs."""
    pass


@logs.command("list")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--limit", default=20, help="Number of sessions to show")
def list_sessions(config_path, limit):
    """List recent conversation sessions."""
    config = load_config(config_path)
    state = _get_state(config)

    from mithai.core.session import SessionManager

    mgr = SessionManager(state)
    sessions = mgr.list_sessions(limit=limit)

    if not sessions:
        banner_small("logs")
        info("No sessions yet.")
        console.print()
        return

    banner_small("logs")
    console.print()

    table = Table(
        show_header=True, header_style="bold bright_white",
        border_style="dim", padding=(0, 1), show_edge=False,
    )
    table.add_column("Session", style="bright_cyan")
    table.add_column("Platform", style="green")
    table.add_column("Turns", style="white", justify="right")
    table.add_column("Updated", style="dim")
    table.add_column("Last message", style="white", max_width=50)

    for s in sessions:
        ts = s.get("updated_at", "")[:16].replace("T", " ")
        table.add_row(
            s["session_id"],
            s.get("platform", "?"),
            str(s.get("turn_count", 0)),
            ts,
            s.get("last_message", ""),
        )

    console.print(table)
    console.print(f"\n  [muted]{len(sessions)} session(s)[/]\n")


@logs.command("show")
@click.argument("session_id")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--last", "last_n", default=None, type=int, help="Show only last N turns")
def show_session(session_id, config_path, last_n):
    """Show the full conversation for a session."""
    from rich.markdown import Markdown
    from rich.panel import Panel

    config = load_config(config_path)
    state = _get_state(config)

    from mithai.core.session import SessionManager

    mgr = SessionManager(state)
    session = mgr.get_session(session_id)

    if session is None:
        from mithai.cli.style import fail
        fail(f"Session [bright_cyan]{session_id}[/] not found.")
        return

    banner_small(f"session · {session_id}")
    kv("Platform", session.get("platform", "?"), indent=4)
    kv("Created", session.get("created_at", "?")[:19].replace("T", " "), indent=4)
    kv("Turns", str(len(session.get("turns", []))), indent=4)
    console.print()

    turns = session.get("turns", [])
    if last_n:
        turns = turns[-last_n:]

    for i, turn in enumerate(turns, 1):
        ts = turn.get("timestamp", "")[:19].replace("T", " ")

        # User message
        console.print(f"  [bright_cyan bold]you>[/] [dim]{ts}[/]")
        console.print(f"  {turn.get('user_message', '')}")
        console.print()

        # Tool calls
        for tc in turn.get("tool_calls", []):
            approved = tc.get("approved", True)
            icon = "[green]✓[/]" if approved else "[red]✗[/]"
            console.print(f"  {icon} [dim]tool:[/] {tc.get('tool', '?')}")

        # Assistant response
        response = turn.get("assistant_response", "")
        if response:
            try:
                console.print(Panel(
                    Markdown(response),
                    title="[bright_magenta]mithai[/]",
                    border_style="bright_magenta",
                    padding=(0, 2),
                    width=min(console.width - 4, 100),
                ))
            except Exception:
                console.print(f"  [bright_magenta]mithai>[/] {response}")

        if i < len(turns):
            console.print(f"  [dim]{'─' * 36}[/]")

    console.print()


@logs.command("search")
@click.argument("query")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--limit", default=10, help="Max results")
def search_sessions(query, config_path, limit):
    """Search across all sessions for a keyword."""
    config = load_config(config_path)
    state = _get_state(config)

    from mithai.core.session import SessionManager

    mgr = SessionManager(state)
    results = mgr.search(query, limit=limit)

    if not results:
        info(f"No results for [bright_cyan]{query}[/]")
        return

    banner_small(f"search · {query}")
    console.print()

    for r in results:
        ts = r.get("timestamp", "")[:16].replace("T", " ")
        console.print(f"  [bright_cyan]{r['session_id']}[/] [dim]{ts}[/]")
        console.print(f"    [dim]Q:[/] {r.get('user_message', '')[:80]}")
        console.print(f"    [dim]A:[/] {r.get('assistant_response', '')[:80]}")
        console.print()

    console.print(f"  [muted]{len(results)} result(s)[/]\n")


def _get_state(config: dict):
    """Create state backend from config."""
    from mithai.cli.run_cmd import _create_state
    return _create_state(config)
