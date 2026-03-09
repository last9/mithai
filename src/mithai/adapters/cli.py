"""CLI/terminal adapter for local development and testing."""

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from mithai.adapters.base import Adapter, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.adapters.formatters import CLIFormatter
from mithai.human.mcp import HumanRequest

_console = Console()


def _flush_stdin():
    """Discard any buffered stdin input (keystrokes during LLM processing).

    When the LLM takes seconds to respond, users may press Enter or type
    while waiting.  Those keystrokes accumulate in the terminal buffer and
    would be read as empty prompts (you> you> you> ...) or auto-deny
    approval prompts.  Flushing clears that buffer.
    """
    try:
        import termios
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except (ImportError, termios.error, ValueError, OSError):
        # Windows or non-tty — best-effort: drain anything readable
        try:
            import select
            while select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.readline()
        except Exception:
            pass


class CLIAdapter(Adapter):
    """
    Interactive terminal REPL.

    Useful for testing skills and the engine without a chat platform.
    """

    def __init__(self):
        self._running = False
        self._formatter = CLIFormatter()

    def start(self, on_message: MessageHandler) -> None:
        self._running = True
        _console.print(
            "\n  [bright_magenta bold]mithai[/] [muted]ready · type [white]quit[/white] to exit[/]\n"
        )

        while self._running:
            try:
                text = _console.input("[bright_cyan bold]you>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                _console.print()
                break

            if not text:
                continue
            if text.lower() in ("quit", "exit", "q"):
                break

            message = IncomingMessage(
                text=text,
                channel_id="cli",
                user_id="local",
                platform="cli",
            )

            response = on_message(message, self)

            # Flush any keystrokes buffered while the LLM was processing
            _flush_stdin()

            _console.print()
            for chunk in self._formatter.format(response):
                try:
                    md = Markdown(chunk)
                    _console.print(Panel(
                        md,
                        title="[bright_magenta]mithai[/]",
                        border_style="bright_magenta",
                        padding=(1, 2),
                    ))
                except Exception:
                    _console.print(f"[bright_magenta]mithai>[/] {chunk}")
            _console.print()

    def stop(self) -> None:
        self._running = False

    def send(self, message: OutgoingMessage) -> None:
        for chunk in self._formatter.format(message.text):
            try:
                md = Markdown(chunk)
                _console.print(Panel(
                    md,
                    title="[bright_magenta]mithai[/]",
                    border_style="bright_magenta",
                    padding=(1, 2),
                ))
            except Exception:
                _console.print(f"[bright_magenta]mithai>[/] {chunk}")

    def request_human_approval(self, request: HumanRequest, channel_id: str) -> bool:
        # Flush buffered keystrokes so stale Enter presses don't auto-deny
        _flush_stdin()

        _console.print()
        level_color = "yellow" if request.level == "approve" else "red"
        _console.print(Panel(
            request.description,
            title=f"[{level_color} bold]Human Approval Required [{request.level.upper()}][/]",
            border_style=level_color,
            padding=(1, 2),
        ))

        if request.level == "confirm":
            confirm_text = _extract_confirm_token(request)
            _console.print(
                f"  Type [bold]{confirm_text}[/] to confirm, or anything else to deny:"
            )
            try:
                answer = _console.input("  [yellow]>[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                _console.print("  [red]Denied.[/]")
                return False
            approved = answer == confirm_text
        else:
            try:
                answer = _console.input("  [yellow]Approve? [y/N]:[/] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                _console.print("  [red]Denied.[/]")
                return False
            approved = answer in ("y", "yes")

        if approved:
            _console.print("  [green]Approved.[/]\n")
        else:
            _console.print("  [red]Denied.[/]\n")
        return approved


def _extract_confirm_token(request: HumanRequest) -> str:
    """Extract a confirmation token from tool input for the confirm level."""
    for value in request.tool_input.values():
        if isinstance(value, str) and value:
            return value
    return request.tool_name.split("__")[-1]
