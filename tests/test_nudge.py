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
  6. max_tokens truncation mid-tool_use → orphan tool_use blocks are closed with
     synthetic error results before any further LLM call  [onboarding 400 fix]
"""

import json
from unittest.mock import MagicMock

from mithai.adapters.base import IncomingMessage
from mithai.core.engine import Engine
from mithai.core.skill_loader import ToolDefinition
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

    def test_tool_result_callback_receives_raw_result(self, tmp_skill_dir, tmp_path):
        """Adapters can enforce response policy from tool outputs."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response("test_skill__echo", {"message": "notify"}),
            _end_turn("done"),
        ]
        engine = _make_engine(tmp_skill_dir, llm)
        adapter = MagicMock()

        engine.handle(_msg(), adapter)

        adapter.on_tool_result.assert_called_once_with(
            "test_skill__echo",
            {"message": "notify"},
            '{"echoed": "notify"}',
        )

    def test_adapter_can_block_followup_tool_before_route(self, tmp_skill_dir, tmp_path):
        """Adapters can prevent side-effect tools after a prior tool activates policy."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response("policy__mark_suppressed", {}),
            _tool_use_response(
                "slack__slack_send_message",
                {"channel_id": "C1", "message": "customer leak"},
            ),
            _end_turn("done"),
        ]
        engine = _make_engine(tmp_skill_dir, llm)

        engine._router.get_definition = MagicMock(return_value=ToolDefinition(
            name="tool",
            description="test tool",
            input_schema={"type": "object"},
        ))
        engine._router.route = MagicMock(return_value=json.dumps({
            "response_policy": {"suppress_final_response": True}
        }))
        engine._router.is_mcp_tool = MagicMock(return_value=False)

        class BlockingAdapter:
            def __init__(self):
                self.suppressed = False
                self.results = []

            def fetch_thread_context(self, channel_id, thread_ts):
                return None

            def on_thinking_start(self): pass
            def on_thinking_end(self, elapsed_s): pass
            def on_tool_start(self, tool_name, tool_input): pass
            def on_tool_end(self, tool_name, elapsed_s, approved): pass
            def on_synthesizing(self): pass

            def before_tool_call(self, tool_name, tool_input):
                if (
                    self.suppressed
                    and tool_name == "slack__slack_send_message"
                    and tool_input.get("channel_id") == "C1"
                ):
                    return json.dumps({"ok": True, "suppressed": True})
                return None

            def on_tool_result(self, tool_name, tool_input, result):
                self.results.append((tool_name, result))
                parsed = json.loads(result)
                if parsed.get("response_policy", {}).get("suppress_final_response") is True:
                    self.suppressed = True

        adapter = BlockingAdapter()
        engine.handle(_msg(), adapter)

        engine._router.route.assert_called_once()
        assert engine._router.route.call_args.args[0] == "policy__mark_suppressed"
        assert adapter.results[-1] == (
            "slack__slack_send_message",
            '{"ok": true, "suppressed": true}',
        )

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


# ---------------------------------------------------------------------------
# max_tokens truncation mid-tool_use
# ---------------------------------------------------------------------------

def _truncated_tool_use(tool_id="toolu_truncated"):
    """stop_reason=max_tokens with a tool_use block — generation was cut off
    mid-call, so the engine's tool loop never executes or answers it."""
    return LLMResponse(
        content=[{
            "type": "tool_use",
            "id": tool_id,
            "name": "test_skill__echo",
            "input": {"message": "huge payload that hit the output cap"},
        }],
        stop_reason="max_tokens",
        model="claude-test",
        usage={"input_tokens": 20, "output_tokens": 4096},
    )


def _assert_tool_pairing_valid(messages):
    """Mirror the Anthropic API validation: every tool_use id in an assistant
    message must have a matching tool_result in the immediately following message."""
    for i, msg in enumerate(messages):
        content = msg["content"]
        if msg["role"] != "assistant" or not isinstance(content, list):
            continue
        tool_ids = {b["id"] for b in content if b.get("type") == "tool_use"}
        if not tool_ids:
            continue
        assert i + 1 < len(messages), f"messages.{i}: tool_use ids {tool_ids} with no following message"
        next_content = messages[i + 1]["content"]
        result_ids = {
            b["tool_use_id"]
            for b in (next_content if isinstance(next_content, list) else [])
            if isinstance(b, dict) and b.get("type") == "tool_result"
        }
        assert tool_ids <= result_ids, (
            f"messages.{i}: tool_use ids without tool_result immediately after: {tool_ids - result_ids}"
        )


class TestMaxTokensTruncatedToolUse:
    def test_orphan_tool_use_closed_before_any_further_llm_call(self, tmp_skill_dir):
        """A max_tokens response carrying tool_use blocks must never leave the
        message array invalid for the next API call (the onboarding 400 bug)."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),    # initial: echo runs normally
            _truncated_tool_use(),   # synthesis: cut off mid-tool_use
            _end_turn("recovered"),  # recovery: model replies with text
        ]
        engine = _make_engine(tmp_skill_dir, llm)

        result = engine.handle(_msg(), MagicMock())

        assert result == "recovered"
        for call in llm.create_message.call_args_list:
            _assert_tool_pairing_valid(call[1]["messages"])

    def test_truncated_block_gets_synthetic_error_result(self, tmp_skill_dir):
        """The recovery call must contain a tool_result for the truncated id
        explaining the call did not execute."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),
            _truncated_tool_use("toolu_cut"),
            _end_turn("ok"),
        ]
        engine = _make_engine(tmp_skill_dir, llm)
        engine.handle(_msg(), MagicMock())

        recovery_messages = llm.create_message.call_args_list[2][1]["messages"]
        results = [
            b
            for m in recovery_messages
            if isinstance(m["content"], list)
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result" and b["tool_use_id"] == "toolu_cut"
        ]
        assert len(results) == 1
        assert "not execute" in results[0]["content"]
        assert "stop_reason=max_tokens" in results[0]["content"]

    def test_truncation_on_initial_response_recovers(self, tmp_skill_dir):
        """max_tokens truncation on the very first response (no prior tool
        rounds) must also be closed and recovered, not crash."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _truncated_tool_use("toolu_first"),  # initial call cut off mid-tool_use
            _end_turn("recovered from start"),   # recovery call
        ]
        engine = _make_engine(tmp_skill_dir, llm)

        result = engine.handle(_msg(), MagicMock())

        assert result == "recovered from start"
        assert llm.create_message.call_count == 2
        for call in llm.create_message.call_args_list:
            _assert_tool_pairing_valid(call[1]["messages"])

    def test_repeated_truncation_bails_out_with_valid_messages(self, tmp_skill_dir):
        """If recovery itself keeps truncating, the engine must stop retrying
        and still leave the message array valid (no infinite loop, no 400)."""
        llm = MagicMock()
        llm.create_message.side_effect = [
            _tool_use_response(),
            _truncated_tool_use("toolu_a"),
            _truncated_tool_use("toolu_b"),
            _truncated_tool_use("toolu_c"),
            _end_turn("late reply"),  # nudge or final call, if any
        ]
        engine = _make_engine(tmp_skill_dir, llm)

        engine.handle(_msg(), MagicMock())

        assert llm.create_message.call_count <= 5
        for call in llm.create_message.call_args_list:
            _assert_tool_pairing_valid(call[1]["messages"])
