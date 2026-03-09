"""Shared CLI styling — colors, tables, panels, and helpers."""

import logging

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

# Brand palette
THEME = Theme({
    "brand": "bold bright_magenta",
    "success": "bold green",
    "error": "bold red",
    "warning": "bold yellow",
    "info": "bold cyan",
    "muted": "dim",
    "header": "bold bright_white",
    "key": "bright_cyan",
    "value": "white",
    "step": "bold bright_magenta",
})

console = Console(theme=THEME)

BANNER = r"""[bright_magenta]
 ███╗   ███╗██╗████████╗██╗  ██╗ █████╗ ██╗
 ████╗ ████║██║╚══██╔══╝██║  ██║██╔══██╗██║
 ██╔████╔██║██║   ██║   ███████║███████║██║
 ██║╚██╔╝██║██║   ██║   ██╔══██║██╔══██║██║
 ██║ ╚═╝ ██║██║   ██║   ██║  ██║██║  ██║██║
 ╚═╝     ╚═╝╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝[/bright_magenta]"""

BANNER_SMALL = "[bright_magenta bold]mithai[/]"

# Indian sweets — a random one with ASCII art shown on each startup
_SWEETS = [
    ("gulab jamun",  r"  (@@)  "),
    ("rasgulla",     r"  {~~}  "),
    ("jalebi",       r"  ~@~   "),
    ("barfi",        r"  [**]  "),
    ("ladoo",        r"  (##)  "),
    ("kaju katli",   r"  <◇>   "),
    ("rasmalai",     r" (~@@~) "),
    ("modak",        r"  /@@\  "),
    ("kulfi",        r"  |▼|   "),
    ("kheer",        r"  {:.}  "),
    ("peda",         r"  (bg)  "),
    ("sandesh",      r"  [~~]  "),
    ("gajar halwa",  r"  {##}  "),
    ("mysore pak",   r"  [##]  "),
    ("kaju katli",   r"  <##>  "),
]


def _random_sweet() -> tuple[str, str]:
    """Return a random Indian sweet (name, ascii art)."""
    import random
    return random.choice(_SWEETS)


def banner(version: str = ""):
    """Print the mithai banner with a random sweet."""
    console.print(BANNER)
    name, art = _random_sweet()
    if version:
        console.print(f"  [muted]AI agent framework for organizations — v{version}[/]")
    console.print(f"  [muted]today's mithai:[/] [bright_yellow]{art}[/] [muted]{name}[/]")
    console.print()


def banner_small(subtitle: str = ""):
    """Print a compact banner for subcommands."""
    line = "[bright_magenta bold]mithai[/]"
    if subtitle:
        line += f" [muted]·[/] [bright_white]{subtitle}[/]"
    console.print(f"\n  {line}")
    console.print(f"  [muted]{'─' * 40}[/]")


def ok(msg: str):
    """Print a success result line."""
    console.print(f"  [green]✓[/] {msg}")


def fail(msg: str):
    """Print a failure result line."""
    console.print(f"  [red]✗[/] {msg}")


def warn(msg: str):
    """Print a warning result line."""
    console.print(f"  [yellow]![/] {msg}")


def info(msg: str):
    """Print an info line."""
    console.print(f"  [cyan]→[/] {msg}")


def step_header(step: int, total: int, title: str):
    """Print a wizard step header."""
    console.print()
    console.print(f"  [step]Step {step}/{total}[/] [header]{title}[/]")
    console.print(f"  [muted]{'─' * 36}[/]")


def section(title: str):
    """Print a section header."""
    console.print(f"\n  [header]{title}[/]")
    console.print(f"  [muted]{'─' * 36}[/]")


def kv(key: str, value: str, indent: int = 2):
    """Print a key-value pair."""
    pad = " " * indent
    console.print(f"{pad}[key]{key}:[/] [value]{value}[/]")


def skill_table(skills: list[dict]) -> Table:
    """Create a table for skill listing."""
    table = Table(show_header=True, header_style="bold bright_white", border_style="dim", padding=(0, 1))
    table.add_column("Skill", style="bright_cyan")
    table.add_column("Tools", style="white", justify="right")
    table.add_column("Human", style="yellow")
    table.add_column("Source", style="dim")

    for s in skills:
        table.add_row(
            s.get("name", "?"),
            str(s.get("tool_count", "?")),
            s.get("human_summary", ""),
            s.get("source", ""),
        )
    return table


def agent_table(agents: list[dict]) -> Table:
    """Create a table for multi-agent listing."""
    table = Table(show_header=True, header_style="bold bright_white", border_style="dim", padding=(0, 1))
    table.add_column("Agent", style="bright_cyan")
    table.add_column("Skills", style="white")
    table.add_column("Adapter", style="green")

    for a in agents:
        table.add_row(
            a.get("id", "?"),
            str(a.get("skill_count", "?")),
            a.get("adapter", "?"),
        )
    return table


def check_table(checks: list[dict]) -> Table:
    """Create a table for doctor checks."""
    table = Table(show_header=False, border_style="dim", padding=(0, 1), show_edge=False)
    table.add_column("Status", width=3)
    table.add_column("Check")

    for c in checks:
        status_icon = "[green]✓[/]" if c["ok"] else "[red]✗[/]"
        table.add_row(status_icon, c["msg"])
    return table


def summary_panel(title: str, content: str):
    """Print a summary panel."""
    console.print(Panel(
        content,
        title=f"[bright_magenta]{title}[/]",
        border_style="bright_magenta",
        padding=(1, 2),
    ))


def setup_logging(verbose: bool = False) -> None:
    """Configure colorized logging with rich.

    Replaces the default logging.basicConfig with a RichHandler
    that colorizes log levels and timestamps.
    """
    level = logging.DEBUG if verbose else logging.INFO

    handler = RichHandler(
        console=Console(stderr=True, theme=THEME),
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        markup=True,
        log_time_format="[%H:%M:%S]",
    )
    handler.setLevel(level)

    logging.basicConfig(
        level=level,
        handlers=[handler],
        format="%(message)s",
        datefmt="[%X]",
    )

    # Silence noisy third-party loggers
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("mcp").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
