"""
Tests for the silent-response nudge in Engine._handle_inner().

When the LLM ends a turn with no text content after running tools, the engine
injects "Please reply to the user now." and re-calls the LLM — with tools still
available so the model can take corrective action (e.g. memory_write after
noticing a gap in memory_read).

Scenarios covered:
  1. Silent after tool chain → nudge fires, nudge response returned to user
  2. No tool calls, empty text → nudge does NOT fire (returns "(no response)")
  3. Nudge call receives the same tool list as the main loop (not tools=None)
  4. Nudge model calls a second tool (memory_write) → that result becomes the
     final response  [the turn-9 "promised but didn't deliver" fix]
  5. Whitespace-only text after tool chain → treated as silent, nudge fires
"""

from unittest.mock import MagicMock, call

import pytest

from mithai.adapters.base import IncomingMessage
from mithai.core.engine import Engine
from mithai.llm.base import LLMResponse
from mithai.state.memory import MemoryStateBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_skill_dir, llm):
    from mithai.core.skill_loader import load_skills

    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic", "model": "claude-test", "anthropic": {"api_key": "x"}},
        "skills": {"paths": [str(tmp_skill_dir)]},
    }
    skills = load_skills([tmp_skill_dir])
    return Engine(config, llm, MemoryStateBackend(), skills=skills)


def _msg(text="hi"):
    return IncomingMessage(text=text, channel_id="C1", user_id="U1", platform="slack")


def _end_turn(text="done"):
    return LLMResponse(
        content=[{"type": "text", "text": text}],
        stop_reason="end_turn",
        model="claude-test",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _silent_end_turn():
    """end_turn with no text content — triggers the nudge."""
    return LLMResponse(
        content=[],
        stop_reason="end_turn",
        model="claude-test",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _tool_use_response(tool_name="test_skill__echo", tool_input=None):
    return LLMResponse(
        content=[{
            "type": "tool_use",
            "id": "tu_1",
            "name": tool_name,
            "input": tool_input or {"message": "hi"},
        }],
        stop_reason="tool_use",
        model="claude-test",
        usage={"input_tokens": 20, "output_tokens": 10},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNudge:
    def test_nudge_fires_when_silent_after_tool(self, tmp_skill_dir, tmp_path):
        """Model runs a tool then returns empty content — nudge response is used."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),   # initial: calls tool
            _silent_end_turn(),     # synthesis: no text → nudge triggered
            _end_turn("here you go"),  # nudge: produces the reply
        ]
        engine = _make_engine(tmp_skill_dir, llm)

        result = engine.handle(_msg(), MagicMock())

        assert result == "here you go"
        assert llm.create_message.call_count == 3

    def test_nudge_not_fired_without_tool_calls(self, tmp_skill_dir, tmp_path):
        """No tools called → empty text falls through as '(no response)', no nudge."""
        llm = MagicMock()
        llm.create_message.return_value = _silent_end_turn()
        engine = _make_engine(tmp_skill_dir, llm)

        result = engine.handle(_msg(), MagicMock())

        assert result == "(no response)"
        assert llm.create_message.call_count == 1  # no nudge call

    def test_nudge_call_receives_tools(self, tmp_skill_dir, tmp_path):
        """The nudge LLM call must include the tool list so the model can act."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),
            _silent_end_turn(),
            _end_turn("fixed"),
        ]
        engine = _make_engine(tmp_skill_dir, llm)
        engine.handle(_msg(), MagicMock())

        # Third call is the nudge — it must have tools (not None)
        nudge_call_kwargs = llm.create_message.call_args_list[2][1]
        assert nudge_call_kwargs["tools"] is not None
        assert len(nudge_call_kwargs["tools"]) > 0

    def test_nudge_model_replies_after_tool_gap(self, tmp_skill_dir, tmp_path):
        """
        Turn-8/9 scenario: model reads memory, goes silent, nudge fires with tools
        available. The "Please reply" directive causes the model to produce text in
        the nudge call rather than calling another tool. Having tools=tools available
        prevents the "I'll fix that" hallucination seen when tools=None.
        """
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response("test_skill__echo", {"message": "read"}),  # initial: memory_read
            _silent_end_turn(),                                            # synthesis: silent
            _end_turn("I see the gap — #platform-team was missing from my channel list"),  # nudge: text
        ]
        engine = _make_engine(tmp_skill_dir, llm)

        result = engine.handle(_msg("how did you forget?"), MagicMock())

        assert "platform-team" in result
        # Nudge call must have tools available so the model can act if needed
        nudge_kwargs = llm.create_message.call_args_list[2][1]
        assert nudge_kwargs["tools"] is not None

    def test_whitespace_text_after_tool_triggers_nudge(self, tmp_skill_dir, tmp_path):
        """Text block containing only whitespace is treated as silent — nudge fires."""
        whitespace_response = LLMResponse(
            content=[{"type": "text", "text": "   \n  "}],
            stop_reason="end_turn",
            model="claude-test",
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),
            whitespace_response,
            _end_turn("actually here's my answer"),
        ]
        engine = _make_engine(tmp_skill_dir, llm)

        result = engine.handle(_msg(), MagicMock())

        assert result == "actually here's my answer"
        assert llm.create_message.call_count == 3

    def test_nudge_message_injected_into_history(self, tmp_skill_dir, tmp_path):
        """The nudge user message appears in the messages passed to the nudge call."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),
            _silent_end_turn(),
            _end_turn("reply"),
        ]
        engine = _make_engine(tmp_skill_dir, llm)
        engine.handle(_msg(), MagicMock())

        nudge_messages = llm.create_message.call_args_list[2][1]["messages"]
        last_user_message = next(
            m for m in reversed(nudge_messages) if m["role"] == "user"
        )
        assert last_user_message["content"] == "Please reply to the user now."

    def test_nudge_call_type_is_synthesis(self, tmp_skill_dir, tmp_path):
        """Nudge call is tagged call_type='synthesis' for tracing."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),
            _silent_end_turn(),
            _end_turn("reply"),
        ]
        engine = _make_engine(tmp_skill_dir, llm)
        engine.handle(_msg(), MagicMock())

        nudge_kwargs = llm.create_message.call_args_list[2][1]
        assert nudge_kwargs["call_type"] == "synthesis"
