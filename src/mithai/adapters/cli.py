"""CLI/terminal adapter for local development and testing."""

import sys
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from mithai.adapters.base import Adapter, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.adapters.formatters import CLIFormatter
from mithai.human.mcp import HumanRequest

_console = Console()

# Slash commands with descriptions for autocomplete
_SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/skills": "List loaded skills and tool counts",
    "/sessions": "List recent conversation sessions",
    "/memory": "Show memory files",
    "/clear": "Clear the terminal screen",
    "/status": "Show engine status",
}


class _SlashCompleter(Completer):
    """Tab-complete slash commands when input starts with '/'."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for cmd, desc in _SLASH_COMMANDS.items():
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display_meta=desc)


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
    Interactive terminal REPL, with piped-stdin support.

    When stdin is a TTY the adapter runs an interactive REPL.
    When stdin is piped (e.g. ``echo "msg" | mithai run``) it reads one
    message, runs the full conversation loop, writes the final plain-text
    response to stdout, and exits.
    """

    _SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self._running = False
        self._engine = None
        self._formatter = CLIFormatter()
        self._thinking_start: float | None = None
        self._live: Live | None = None
        self._piped = not sys.stdin.isatty()
        if not self._piped:
            self._prompt_session = PromptSession(
                completer=_SlashCompleter(),
                complete_while_typing=True,
            )

    def set_engine(self, engine) -> None:
        """Give the adapter a reference to the engine for slash commands."""
        self._engine = engine

    def start(self, on_message: MessageHandler, on_channel_join=None, on_observe=None,
              on_bot_reply=None) -> None:
        if self._piped:
            self._start_piped(on_message)
        else:
            self._start_interactive(on_message)

    def _start_piped(self, on_message: MessageHandler) -> None:
        """Read one message from stdin, process through the full conversation loop,
        write the final plain-text response to stdout, and exit."""
        text = sys.stdin.read().strip()
        if not text:
            return

        message = IncomingMessage(
            text=text,
            channel_id="cli",
            user_id="local",
            platform="cli",
        )

        try:
            response = on_message(message, self)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

        if response:
            print(response, flush=True)

    def _start_interactive(self, on_message: MessageHandler) -> None:
        """Run the interactive REPL (original behavior)."""
        self._running = True
        _console.print(
            "\n  [bright_magenta bold]mithai[/] [muted]ready · type [white]quit[/white] to exit · [white]/help[/white] for commands[/]\n"
        )

        # Build ANSI prompt using Rich's rendering
        prompt_ansi = ANSI("\x1b[1;96myou>\x1b[0m ")

        while self._running:
            try:
                text = self._prompt_session.prompt(prompt_ansi).strip()
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
        if self._piped:
            # In piped mode, write plain text to stdout (no Rich formatting)
            print(message.text, flush=True)
            return
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
        if self._piped:
            print(
                f"Error: tool '{request.tool_name}' requires human approval "
                "but stdin is not a terminal — auto-denying.",
                file=sys.stderr,
            )
            return False
        # Stop any active spinner before showing approval dialog
        self._stop_live()
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


    # ── Status callbacks for progress feedback ──

    def on_thinking_start(self) -> None:
        if self._piped:
            return
        self._thinking_start = time.monotonic()
        self._live = Live(
            Text("  ● thinking...", style="bright_magenta"),
            console=_console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()
        self._spin_thread_running = True
        import threading
        self._spin_thread = threading.Thread(target=self._spin_loop, daemon=True)
        self._spin_thread.start()

    def _spin_loop(self) -> None:
        """Update the spinner with elapsed time."""
        idx = 0
        while self._spin_thread_running and self._live:
            elapsed = time.monotonic() - (self._thinking_start or time.monotonic())
            frame = self._SPINNER_FRAMES[idx % len(self._SPINNER_FRAMES)]
            try:
                self._live.update(
                    Text(f"  {frame} thinking... {elapsed:.0f}s", style="bright_magenta")
                )
            except Exception:
                break
            idx += 1
            time.sleep(0.15)

    def _stop_live(self) -> None:
        """Stop the live spinner."""
        self._spin_thread_running = False
        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def on_thinking_end(self, elapsed_s: float) -> None:
        self._stop_live()

    def on_tool_start(self, tool_name: str, tool_input: dict) -> None:
        if self._piped:
            return
        self._stop_live()
        # Show a compact preview of what's being called
        preview = ""
        if "command" in tool_input:
            preview = f" `{tool_input['command'][:60]}`"
        elif "repo" in tool_input:
            preview = f" {tool_input['repo']}"
        elif "namespace" in tool_input:
            preview = f" ns={tool_input['namespace']}"
        _console.print(f"  [cyan]▸[/] [dim]tool:[/] {tool_name}{preview}")
        self._thinking_start = time.monotonic()

    def on_tool_end(self, tool_name: str, elapsed_s: float, approved: bool) -> None:
        if self._piped:
            return
        if approved:
            _console.print(f"  [green]✓[/] [dim]{tool_name}[/] [muted]({elapsed_s:.1f}s)[/]")
        else:
            _console.print(f"  [red]✗[/] [dim]{tool_name} denied[/]")

    def on_synthesizing(self) -> None:
        if self._piped:
            return
        self._thinking_start = time.monotonic()
        self._live = Live(
            Text("  ● synthesizing response...", style="bright_magenta"),
            console=_console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()
        self._spin_thread_running = True
        import threading
        self._spin_thread = threading.Thread(target=self._synth_spin_loop, daemon=True)
        self._spin_thread.start()

    def _synth_spin_loop(self) -> None:
        idx = 0
        while self._spin_thread_running and self._live:
            elapsed = time.monotonic() - (self._thinking_start or time.monotonic())
            frame = self._SPINNER_FRAMES[idx % len(self._SPINNER_FRAMES)]
            try:
                self._live.update(
                    Text(f"  {frame} synthesizing response... {elapsed:.0f}s", style="bright_magenta")
                )
            except Exception:
                break
            idx += 1
            time.sleep(0.15)


def _extract_confirm_token(request: HumanRequest) -> str:
    """Extract a confirmation token from tool input for the confirm level."""
    for value in request.tool_input.values():
        if isinstance(value, str) and value:
            return value
    return request.tool_name.split("__")[-1]
