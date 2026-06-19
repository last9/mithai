"""Regression tests for raw tool-call XML leaking into the outbound reply.

Repro of a production incident: an agent on the slack_http managed adapter
posted a literal ``<function_calls><invoke name="slack__slack_send_message">…``
blob (plus a fabricated tool ``result``) as a Slack message, instead of either
sending the message or replying in prose.

Root cause chain:
  1. The model emitted the tool call as TEXT (not a structured tool_use block) —
     observed right after its real slack_send_message was denied by the human gate.
  2. ``Engine._extract_raw_text`` returned any text block verbatim, so the XML
     flowed out of ``handle()`` (and the onboarding intro) and was posted unfiltered.
  3. The leaked string was stored as the turn's ``assistant_response`` and then
     replayed verbatim by ``_build_history``, feeding the bad example back to the
     model — a self-reinforcing contamination loop.

The fix sanitizes at the ``_extract_raw_text`` chokepoint (covering live replies,
the post-tool nudge, and the onboarding intro) and in ``_build_history``. It only
acts when the reply LEADS with scaffolding, so legitimate prose that merely quotes
tool-call syntax (code review, this very post-mortem) is preserved.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from mithai.adapters.base import IncomingMessage
from mithai.core.engine import Engine, _strip_tool_call_syntax, _TOOL_CALL_MARKERS
from mithai.memory.filesystem import FilesystemMemoryBackend
from mithai.state.memory import MemoryStateBackend

# The human-meant message buried in the narrated call.
INTENDED = "Hey #release — I'm REDACTED_INTERNAL_CHANNEL, a senior staff engineer here to help."

# A faithful sample of what the model emitted as a *text* block in the incident.
LEAKED_XML = (
    '<function_calls>\n'
    '<invoke name="slack__slack_send_message">\n'
    '<parameter name="channel_id">C0123TEST01</parameter>\n'
    f'<parameter name="message">{INTENDED}</parameter>\n'
    '</invoke>\n'
    '</function_calls>\n'
    '<parameter name="result">{"ok":true,"channel":"C0123TEST01"}</parameter>'
)


def _has_marker(text: str) -> bool:
    return any(m in text for m in _TOOL_CALL_MARKERS)


def _make_engine(**config_extra):
    llm = MagicMock()
    state = MemoryStateBackend()
    memory = FilesystemMemoryBackend(Path(tempfile.mkdtemp()))
    config = {
        "bot": {"system_prompt": "You are a helpful assistant.", "name": "REDACTED_INTERNAL_CHANNEL"},
        "learning": {"enabled": False},
        "llm": {"provider": "anthropic", "api_key": "test"},
        **config_extra,
    }
    engine = Engine(config=config, llm=llm, state=state, memory=memory, skills={})
    return engine, llm


def _adapter():
    a = MagicMock()
    a.fetch_thread_context.return_value = None
    return a


# --- Outbound reply path (handle) -------------------------------------------

def test_outbound_reply_recovers_intended_message_and_drops_scaffolding():
    """Leaked XML as text -> reply is the intended message, no scaffolding."""
    engine, llm = _make_engine()
    resp = MagicMock()
    resp.content = [{"type": "text", "text": LEAKED_XML}]
    resp.stop_reason = "end_turn"
    llm.create_message.return_value = resp

    msg = IncomingMessage(
        text="introduce yourself", channel_id="C1", user_id="alice", platform="slack"
    )
    reply = engine.handle(msg, _adapter())

    assert not _has_marker(reply), f"leaked scaffolding: {reply!r}"
    assert reply == INTENDED, f"intended message not recovered: {reply!r}"


def test_legitimate_prose_quoting_tool_syntax_is_preserved():
    """A code-review/post-mortem reply that QUOTES tool-call syntax must survive."""
    engine, llm = _make_engine()
    prose = (
        "The bug: the model emits a literal "
        '<function_calls>\n<invoke name="slack__slack_send_message">… blob. '
        'It also fabricates a <parameter name="result"> block. Fix the sanitizer."'
    )
    resp = MagicMock()
    resp.content = [{"type": "text", "text": prose}]
    resp.stop_reason = "end_turn"
    llm.create_message.return_value = resp

    msg = IncomingMessage(text="review this", channel_id="C1", user_id="alice", platform="slack")
    reply = engine.handle(msg, _adapter())

    assert reply == prose, f"legitimate prose was mangled: {reply!r}"


# --- Onboarding Phase-2 intro path (the actual incident surface) ------------

def test_onboarding_intro_strips_leaked_tool_call_xml():
    """handle_channel_join's intro (Phase-2, _extract_text) must be sanitized."""
    engine, llm = _make_engine(onboarding={"enabled": True})
    resp = MagicMock()
    resp.content = [{"type": "text", "text": LEAKED_XML}]
    resp.stop_reason = "end_turn"
    llm.create_message.return_value = resp

    intro = engine.handle_channel_join("C0123TEST01", "release")

    assert intro is not None
    assert not _has_marker(intro), f"onboarding intro leaked scaffolding: {intro!r}"
    assert intro == INTENDED


# --- History replay ---------------------------------------------------------

def test_history_recovers_message_and_never_emits_empty_content():
    """A prior turn that led with leaked XML is recovered, not blanked or leaked."""
    engine, _ = _make_engine()
    session = {"turns": [{
        "user_message": "introduce yourself",
        "tool_calls": [],
        "assistant_response": LEAKED_XML,
        "images": None,
    }]}

    messages = engine._build_history(session)

    for m in messages:
        content = m["content"]
        text = content if isinstance(content, str) else str(content)
        assert not _has_marker(text), f"history replayed scaffolding: {text!r}"
        assert content != "", "history emitted an empty content block (API rejects it)"
    # The recovered intended message survives into the replayed assistant turn.
    assert any(m["role"] == "assistant" and m["content"] == INTENDED for m in messages)


def test_history_all_scaffolding_turn_falls_back_not_empty():
    """A leaked turn with no recoverable message must not become empty content."""
    engine, _ = _make_engine()
    bare = '<function_calls>\n<invoke name="x__y">\n<parameter name="channel_id">C0</parameter>\n</invoke>\n</function_calls>'
    session = {"turns": [{
        "user_message": "hi", "tool_calls": [], "assistant_response": bare, "images": None,
    }]}

    messages = engine._build_history(session)
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert assistant and all(m["content"] for m in assistant), "empty content block emitted"
    assert all(not _has_marker(str(m["content"])) for m in assistant)


def test_history_handles_none_assistant_response():
    """A stored turn with a None response must not crash _build_history."""
    engine, _ = _make_engine()
    session = {"turns": [{
        "user_message": "hi", "tool_calls": [], "assistant_response": None, "images": None,
    }]}
    messages = engine._build_history(session)  # must not raise
    assert all(m["content"] != "" for m in messages)


# --- Direct unit tests on the sanitizer (cheap edge coverage) ---------------

def test_passthrough_when_no_markers():
    assert _strip_tool_call_syntax("Hello, how can I help?") == "Hello, how can I help?"


def test_passthrough_when_markers_mid_prose():
    s = 'See the docs about <parameter name="message"> usage; it matters.'
    assert _strip_tool_call_syntax(s) == s  # does not lead with a wrapper


def test_single_quoted_attributes_are_stripped():
    """Single-quoted attrs trip the lead-with guard AND must be fully stripped."""
    s = "<invoke name='slack__slack_send_message'><parameter name='message'>hi there</parameter></invoke>"
    out = _strip_tool_call_syntax(s)
    assert not _has_marker(out), f"single-quote bypass leaked: {out!r}"
    assert out == "hi there"


def test_quoteless_invoke_tag_is_stripped():
    s = "<invoke name=foo><parameter name=channel>C0</parameter></invoke>"
    out = _strip_tool_call_syntax(s)
    assert not _has_marker(out), f"quoteless tag leaked: {out!r}"


def test_multiple_invokes_recover_all_messages():
    s = (
        '<function_calls>'
        '<invoke name="a"><parameter name="message">one</parameter></invoke>'
        '<invoke name="b"><parameter name="message">two</parameter></invoke>'
        '</function_calls>'
    )
    out = _strip_tool_call_syntax(s)
    assert not _has_marker(out)
    assert "one" in out and "two" in out


def test_truncated_scaffolding_leaves_no_markers():
    s = '<function_calls>\n<invoke name="slack__slack_send_message">\n<parameter name="message">partial'
    out = _strip_tool_call_syntax(s)
    assert not _has_marker(out), f"truncated scaffolding leaked: {out!r}"
