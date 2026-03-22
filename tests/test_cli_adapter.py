"""Tests for CLIAdapter — piped stdin, interactive mode, and on_bot_reply acceptance."""

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from mithai.adapters.base import IncomingMessage, OutgoingMessage
from mithai.adapters.cli import CLIAdapter


# ---------------------------------------------------------------------------
# Piped stdin mode
# ---------------------------------------------------------------------------


class TestPipedMode:
    def _make_piped_adapter(self):
        """Create a CLIAdapter that thinks stdin is piped."""
        with patch.object(sys, "stdin", new_callable=lambda: lambda: StringIO("hello world\n")):
            # isatty() on StringIO returns False, so _piped=True
            adapter = CLIAdapter()
        assert adapter._piped is True
        return adapter

    def test_piped_adapter_detects_pipe(self):
        with patch.object(sys, "stdin", new=StringIO("test")):
            adapter = CLIAdapter()
        assert adapter._piped is True

    def test_interactive_adapter_detects_tty(self):
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        with patch.object(sys, "stdin", new=mock_stdin), \
             patch("mithai.adapters.cli.PromptSession"):
            adapter = CLIAdapter()
        assert adapter._piped is False

    def test_piped_reads_stdin_and_calls_on_message(self):
        handler = MagicMock(return_value="4")
        with patch.object(sys, "stdin", new=StringIO("what is 2+2?")):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO("what is 2+2?")):
            adapter._start_piped(handler)

        handler.assert_called_once()
        msg = handler.call_args[0][0]
        assert isinstance(msg, IncomingMessage)
        assert msg.text == "what is 2+2?"
        assert msg.channel_id == "cli"
        assert msg.platform == "cli"

    def test_piped_writes_response_to_stdout(self, capsys):
        handler = MagicMock(return_value="The answer is 4.")
        with patch.object(sys, "stdin", new=StringIO("question")):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO("question")):
            adapter._start_piped(handler)
        captured = capsys.readouterr()
        assert captured.out.strip() == "The answer is 4."

    def test_piped_no_ansi_in_output(self, capsys):
        handler = MagicMock(return_value="plain text response")
        with patch.object(sys, "stdin", new=StringIO("input")):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO("input")):
            adapter._start_piped(handler)
        captured = capsys.readouterr()
        assert "\x1b" not in captured.out  # No ANSI escape codes

    def test_piped_empty_stdin_does_not_call_handler(self):
        handler = MagicMock()
        with patch.object(sys, "stdin", new=StringIO("")):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO("")):
            adapter._start_piped(handler)
        handler.assert_not_called()

    def test_piped_whitespace_only_stdin_does_not_call_handler(self):
        handler = MagicMock()
        with patch.object(sys, "stdin", new=StringIO("   \n  \n")):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO("   \n  \n")):
            adapter._start_piped(handler)
        handler.assert_not_called()

    def test_piped_handler_exception_exits_nonzero(self):
        handler = MagicMock(side_effect=RuntimeError("LLM error"))
        with patch.object(sys, "stdin", new=StringIO("test")):
            adapter = CLIAdapter()
        with pytest.raises(SystemExit) as exc_info:
            with patch.object(sys, "stdin", new=StringIO("test")):
                adapter._start_piped(handler)
        assert exc_info.value.code == 1

    def test_piped_handler_error_writes_to_stderr(self, capsys):
        handler = MagicMock(side_effect=RuntimeError("API key invalid"))
        with patch.object(sys, "stdin", new=StringIO("test")):
            adapter = CLIAdapter()
        with pytest.raises(SystemExit):
            with patch.object(sys, "stdin", new=StringIO("test")):
                adapter._start_piped(handler)
        captured = capsys.readouterr()
        assert "API key invalid" in captured.err

    def test_piped_multiline_stdin_reads_all(self, capsys):
        """Piped mode reads entire stdin (until EOF), not just first line."""
        handler = MagicMock(return_value="done")
        multiline = "line one\nline two\nline three"
        with patch.object(sys, "stdin", new=StringIO(multiline)):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO(multiline)):
            adapter._start_piped(handler)
        msg = handler.call_args[0][0]
        assert msg.text == multiline


# ---------------------------------------------------------------------------
# Piped mode suppresses Rich output
# ---------------------------------------------------------------------------


class TestPipedHumanApproval:
    def test_piped_mode_auto_denies_approval(self, capsys):
        """In piped mode, human approval requests are auto-denied with stderr message."""
        from mithai.human.mcp import HumanRequest
        with patch.object(sys, "stdin", new=StringIO("x")):
            adapter = CLIAdapter()
        request = HumanRequest(
            tool_name="dangerous_tool",
            tool_input={"command": "rm -rf /"},
            description="Delete everything",
            level="approve",
        )
        result = adapter.request_human_approval(request, "cli")
        assert result is False
        captured = capsys.readouterr()
        assert "dangerous_tool" in captured.err
        assert "auto-denying" in captured.err
        assert "\x1b" not in captured.err  # No ANSI codes


class TestPipedSuppressesRichOutput:
    def test_send_in_piped_mode_outputs_plain_text(self, capsys):
        with patch.object(sys, "stdin", new=StringIO("x")):
            adapter = CLIAdapter()
        adapter.send(OutgoingMessage(text="hello", channel_id="cli"))
        captured = capsys.readouterr()
        assert captured.out.strip() == "hello"
        assert "\x1b" not in captured.out

    def test_status_callbacks_are_noop_in_piped_mode(self):
        with patch.object(sys, "stdin", new=StringIO("x")):
            adapter = CLIAdapter()
        # These should not raise or produce any output
        adapter.on_thinking_start()
        adapter.on_thinking_end(1.0)
        adapter.on_tool_start("shell", {"command": "ls"})
        adapter.on_tool_end("shell", 0.5, True)
        adapter.on_synthesizing()


# ---------------------------------------------------------------------------
# on_bot_reply acceptance
# ---------------------------------------------------------------------------


class TestOnBotReplyAcceptance:
    def test_start_accepts_on_bot_reply_kwarg(self):
        """CLIAdapter.start() must accept on_bot_reply without raising TypeError."""
        handler = MagicMock(return_value="ok")
        bot_reply_cb = MagicMock()
        with patch.object(sys, "stdin", new=StringIO("test")):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO("test")):
            # This is the exact call that was failing before the fix
            adapter.start(
                on_message=handler,
                on_channel_join=None,
                on_observe=None,
                on_bot_reply=bot_reply_cb,
            )
        # Should not raise TypeError

    def test_start_works_without_on_bot_reply(self):
        """Backward compat: start() without on_bot_reply still works."""
        handler = MagicMock(return_value="ok")
        with patch.object(sys, "stdin", new=StringIO("test")):
            adapter = CLIAdapter()
        with patch.object(sys, "stdin", new=StringIO("test")):
            adapter.start(on_message=handler)


# ---------------------------------------------------------------------------
# Interactive mode basics (non-piped)
# ---------------------------------------------------------------------------


class TestInteractiveMode:
    def _make_interactive_adapter(self):
        """Create a CLIAdapter in interactive mode, bypassing PromptSession init."""
        with patch.object(sys, "stdin", new=StringIO("x")):
            # StringIO.isatty() returns False, so this creates piped mode adapter.
            # We manually flip it to interactive and attach a mock prompt session.
            adapter = CLIAdapter()
        adapter._piped = False
        adapter._prompt_session = MagicMock()
        return adapter

    def test_interactive_start_reads_from_prompt_session(self):
        """In interactive mode, start() uses prompt_toolkit session."""
        adapter = self._make_interactive_adapter()
        adapter._prompt_session.prompt.return_value = "quit"
        handler = MagicMock()
        with patch("mithai.adapters.cli._console"):
            adapter._start_interactive(handler)
        handler.assert_not_called()  # "quit" exits without calling handler

    def test_interactive_start_calls_handler_then_quit(self):
        """Interactive mode calls handler for messages, stops on 'quit'."""
        adapter = self._make_interactive_adapter()
        adapter._prompt_session.prompt.side_effect = ["hello", "quit"]
        handler = MagicMock(return_value="hi there")

        with patch("mithai.adapters.cli._console"):
            with patch("mithai.adapters.cli._flush_stdin"):
                adapter._start_interactive(handler)

        handler.assert_called_once()
        msg = handler.call_args[0][0]
        assert msg.text == "hello"
