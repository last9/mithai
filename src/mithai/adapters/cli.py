"""CLI/terminal adapter for local development and testing."""

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from mithai.adapters.base import Adapter, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.adapters.formatters import CLIFormatter
from mithai.human.mcp import HumanRequest

_console = Console()


# ── In-chat slash commands ──

def _handle_slash_command(text: str, engine=None) -> str | None:
    """Handle in-chat slash commands. Returns response text or None if not a command."""
    cmd = text.strip().lower()
    parts = cmd.split(None, 1)
    command = parts[0]

    if command == "/help":
        return (
            "**Chat commands:**\n"
            "| Command | Description |\n"
            "|---------|-------------|\n"
            "| `/help` | Show this help |\n"
            "| `/skills` | List loaded skills and tool counts |\n"
            "| `/sessions` | List recent conversation sessions |\n"
            "| `/memory` | Show memory files |\n"
            "| `/clear` | Clear the screen |\n"
            "| `/status` | Show engine status |\n"
            "| `quit` | Exit the chat |"
        )

    if command == "/skills" and engine:
        lines = ["**Loaded skills:**\n"]
        for name, skill in sorted(engine._skills.items()):
            tool_count = len(skill.tools)
            mcp_count = sum(
                len(e.get("tools", [])) if isinstance(e.get("tools"), list) else 0
                for e in skill.mcp_tools
            )
            has_wildcard = any(e.get("tools") == "*" for e in skill.mcp_tools)
            label = f"{tool_count} tools"
            if mcp_count:
                label += f" + {mcp_count} MCP"
            if has_wildcard:
                label += " + MCP(*)"
            lines.append(f"- **{name}** — {label}")
        return "\n".join(lines)

    if command == "/sessions" and engine:
        sessions = engine._sessions.list_sessions(limit=10)
        if not sessions:
            return "*No sessions yet.*"
        lines = ["**Recent sessions:**\n"]
        for s in sessions:
            ts = s["updated_at"][:16].replace("T", " ") if s.get("updated_at") else "?"
            preview = s.get("last_message", "")[:60]
            lines.append(f"- `{s['session_id']}` ({s['turn_count']} turns, {ts}) — {preview}")
        return "\n".join(lines)

    if command == "/memory" and engine and engine._memory:
        files = engine._memory.list("**/*")
        if not files:
            return "*No memory files.*"
        lines = ["**Memory files:**\n"]
        for f in sorted(files):
            lines.append(f"- `{f}`")
        return "\n".join(lines)

    if command == "/clear":
        _console.clear()
        return None  # No output, just clear

    if command == "/status" and engine:
        parts_list = []
        parts_list.append(f"**Agent:** {engine._agent_id or 'default'}")
        parts_list.append(f"**Skills:** {len(engine._skills)} loaded")
        if engine._mcp_manager:
            servers = list(engine._mcp_manager._configs.keys())
            connected = [
                name for name, entry in engine._mcp_manager._sessions.items()
            ]
            parts_list.append(f"**MCP servers:** {', '.join(servers) if servers else 'none'}")
            if connected:
                parts_list.append(f"**MCP connected:** {', '.join(connected)}")
        if engine._memory:
            parts_list.append("**Memory:** active")
        llm_model = engine._llm_config.get("model", "?")
        parts_list.append(f"**LLM:** {engine._llm_config.get('provider', '?')} / {llm_model}")
        return "\n".join(parts_list)

    return None  # Not a recognized command


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
        self._engine = None
        self._formatter = CLIFormatter()

    def set_engine(self, engine) -> None:
        """Give the adapter a reference to the engine for slash commands."""
        self._engine = engine

    def start(self, on_message: MessageHandler, on_channel_join=None) -> None:
        self._running = True
        _console.print(
            "\n  [bright_magenta bold]mithai[/] [muted]ready · type [white]quit[/white] to exit · [white]/help[/white] for commands[/]\n"
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

            # Handle in-chat slash commands
            if text.startswith("/"):
                result = _handle_slash_command(text, engine=self._engine)
                if result:
                    _console.print()
                    try:
                        _console.print(Panel(
                            Markdown(result),
                            title="[bright_magenta]mithai[/]",
                            border_style="bright_magenta",
                            padding=(1, 2),
                        ))
                    except Exception:
                        _console.print(result)
                    _console.print()
                continue

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
