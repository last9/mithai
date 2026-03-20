"""Tests for thread context continuity — observed thread replies stored in session."""

from unittest.mock import MagicMock

from mithai.adapters.base import IncomingMessage
from mithai.core.session import SessionManager
from mithai.state.memory import MemoryStateBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(config=None):
    from mithai.core.engine import Engine
    from mithai.memory.filesystem import FilesystemMemoryBackend
    import tempfile
    from pathlib import Path

    llm = MagicMock()
    resp = MagicMock()
    resp.content = [{"type": "text", "text": "got it"}]
    resp.stop_reason = "end_turn"
    llm.create_message.return_value = resp

    state = MemoryStateBackend()
    memory = FilesystemMemoryBackend(Path(tempfile.mkdtemp()))

    base_config = {
        "bot": {},
        "learning": {"enabled": False},
        "llm": {"provider": "anthropic", "api_key": "test"},
    }
    if config:
        base_config.update(config)

    return Engine(
        config=base_config,
        llm=llm,
        state=state,
        memory=memory,
        skills={},
    )


def _make_message(text="hello", channel_id="C1", user_id="alice", thread_id="111.000"):
    return IncomingMessage(
        text=text,
        channel_id=channel_id,
        user_id=user_id,
        platform="slack",
        thread_id=thread_id,
    )


def _session_key(platform, scope):
    return SessionManager.session_key(platform, scope)


def _seed_session_with_turn(engine, key):
    """Create a session that already has a completed agent turn."""
    engine._sessions.append_turn(key, SessionManager.build_turn(
        user_id="alice",
        user_message="@agent what do you think?",
        tool_calls=[],
        assistant_response="I think X is the right approach.",
    ))


# ---------------------------------------------------------------------------
# observe() — thread session storage
# ---------------------------------------------------------------------------

def test_observe_stores_to_thread_session_when_session_active():
    """Thread reply observed when session exists with turns → stored as pending observation."""
    engine = _make_engine()
    key = _session_key("slack", "111.000")
    _seed_session_with_turn(engine, key)

    msg = _make_message(text="I agree with that", user_id="bob", thread_id="111.000")
    engine.observe(msg)

    session = engine._sessions.get_session(key)
    observations = session.get("pending_observations", [])
    assert len(observations) == 1
    assert observations[0]["user_id"] == "bob"
    assert observations[0]["text"] == "I agree with that"


def test_observe_does_not_store_when_no_session_exists():
    """Thread reply observed with no prior session → no session created."""
    engine = _make_engine()
    key = _session_key("slack", "999.000")

    msg = _make_message(text="random message", thread_id="999.000")
    engine.observe(msg)

    assert engine._sessions.get_session(key) is None


def test_observe_does_not_store_when_session_has_no_turns():
    """Session exists but agent has never responded → not an active thread, don't store."""
    engine = _make_engine()
    key = _session_key("slack", "111.000")
    # Load session to create it (zero turns)
    engine._sessions.load(key)
    # Manually persist the empty session
    engine._sessions._state.set("sessions", key, {
        "session_id": key, "platform": "slack", "channel_id": "111.000",
        "created_at": "", "updated_at": "", "turns": [],
    })

    msg = _make_message(text="anyone here?", thread_id="111.000")
    engine.observe(msg)

    session = engine._sessions.get_session(key)
    assert session.get("pending_observations", []) == []


def test_observe_stores_multiple_observations_in_order():
    """Multiple thread replies all stored, preserving order."""
    engine = _make_engine()
    key = _session_key("slack", "111.000")
    _seed_session_with_turn(engine, key)

    for text in ["first reply", "second reply", "third reply"]:
        engine.observe(_make_message(text=text, user_id="bob", thread_id="111.000"))

    session = engine._sessions.get_session(key)
    observations = session.get("pending_observations", [])
    assert len(observations) == 3
    assert [o["text"] for o in observations] == ["first reply", "second reply", "third reply"]


def test_observe_without_thread_id_does_not_store_in_session():
    """Top-level channel messages (no thread_id) are never stored in session."""
    engine = _make_engine()
    key = _session_key("slack", "C1")
    _seed_session_with_turn(engine, key)

    msg = IncomingMessage(
        text="top level message",
        channel_id="C1",
        user_id="alice",
        platform="slack",
        thread_id=None,
    )
    engine.observe(msg)

    session = engine._sessions.get_session(key)
    assert session.get("pending_observations", []) == []


# ---------------------------------------------------------------------------
# handle() — pending observations injected as context
# ---------------------------------------------------------------------------

def test_handle_prepends_pending_observations_to_user_message():
    """When the agent is @mentioned and there are pending observations,
    they are prepended to the LLM user message as thread context."""
    engine = _make_engine()
    key = _session_key("slack", "111.000")
    _seed_session_with_turn(engine, key)

    # Store pending observations directly
    engine._sessions.append_observation(key, {"user_id": "bob", "text": "I agree"})
    engine._sessions.append_observation(key, {"user_id": "charlie", "text": "me too"})

    adapter = MagicMock()
    adapter.request_human_approval.return_value = True

    mention = _make_message(text="what's the latest?", user_id="alice", thread_id="111.000")
    engine.handle(mention, adapter)

    call_args = engine._llm.create_message.call_args
    messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][1]

    # Find the last user message — it should contain the thread context
    user_messages = [m for m in messages if m["role"] == "user"]
    last_user_content = user_messages[-1]["content"]
    assert "bob" in last_user_content
    assert "I agree" in last_user_content
    assert "charlie" in last_user_content
    assert "me too" in last_user_content
    assert "what's the latest?" in last_user_content


def test_handle_clears_observations_after_use():
    """Pending observations are cleared from the session after handle() consumes them."""
    engine = _make_engine()
    key = _session_key("slack", "111.000")
    _seed_session_with_turn(engine, key)

    engine._sessions.append_observation(key, {"user_id": "bob", "text": "noted"})

    adapter = MagicMock()
    mention = _make_message(text="ok continue", thread_id="111.000")
    engine.handle(mention, adapter)

    session = engine._sessions.get_session(key)
    assert session.get("pending_observations", []) == []


def test_handle_no_observations_no_context_prefix():
    """When there are no pending observations, the user message is passed through unchanged."""
    engine = _make_engine()
    key = _session_key("slack", "111.000")
    _seed_session_with_turn(engine, key)

    adapter = MagicMock()
    mention = _make_message(text="plain question", thread_id="111.000")
    engine.handle(mention, adapter)

    call_args = engine._llm.create_message.call_args
    messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][1]
    user_messages = [m for m in messages if m["role"] == "user"]
    last_user_content = user_messages[-1]["content"]
    assert last_user_content == "plain question"
    assert "Thread context" not in last_user_content


# ---------------------------------------------------------------------------
# handle() — thread backfill on first @mention in existing thread
# ---------------------------------------------------------------------------

def _make_thread_reply_message(text="what do you think?", thread_ts="111.000"):
    """A message that is a reply in an existing thread (thread_id != message_id)."""
    return IncomingMessage(
        text=text,
        channel_id="C1",
        user_id="alice",
        platform="slack",
        message_id="222.000",   # reply's own ts — different from thread root
        thread_id=thread_ts,    # points to the root message
    )


def _make_toplevel_message(text="hello", ts="111.000"):
    """A top-level @mention (thread_id == message_id — nothing above it)."""
    return IncomingMessage(
        text=text,
        channel_id="C1",
        user_id="alice",
        platform="slack",
        message_id=ts,
        thread_id=ts,   # same as message_id: first message in a thread
    )


def test_handle_backfills_thread_history_on_first_mention():
    """Agent @mentioned in message 30 of a thread with empty session.
    fetch_thread_context() is called and the thread history is prepended to the LLM context."""
    engine = _make_engine()

    adapter = MagicMock()
    adapter.fetch_thread_context.return_value = [
        "alice: anyone know how to fix the deploy?",
        "bob: tried restarting — no luck",
        "charlie: might be a config issue",
    ]

    msg = _make_thread_reply_message(text="@agent can you help?")
    engine.handle(msg, adapter)

    adapter.fetch_thread_context.assert_called_once_with("C1", "111.000")

    call_args = engine._llm.create_message.call_args
    messages = call_args[1].get("messages") or call_args[0][1]
    user_messages = [m for m in messages if m["role"] == "user"]
    content = user_messages[-1]["content"]

    assert "alice: anyone know how to fix the deploy?" in content
    assert "bob: tried restarting" in content
    assert "charlie: might be a config issue" in content
    assert "@agent can you help?" in content


def test_handle_no_backfill_when_session_already_has_turns():
    """Agent already responded in this thread — history is in the session, no backfill."""
    engine = _make_engine()
    key = _session_key("slack", "111.000")
    _seed_session_with_turn(engine, key)

    adapter = MagicMock()
    adapter.fetch_thread_context.return_value = ["bob: some message"]

    msg = _make_thread_reply_message(text="follow-up question")
    engine.handle(msg, adapter)

    adapter.fetch_thread_context.assert_not_called()


def test_handle_no_backfill_for_toplevel_mention():
    """Top-level @mention (thread_id == message_id) — nothing above it, no backfill."""
    engine = _make_engine()

    adapter = MagicMock()
    adapter.fetch_thread_context.return_value = ["bob: some message"]

    msg = _make_toplevel_message(text="hey agent")
    engine.handle(msg, adapter)

    adapter.fetch_thread_context.assert_not_called()


def test_handle_no_backfill_when_adapter_returns_none():
    """Non-Slack adapter returns None — no crash, no context prefix."""
    engine = _make_engine()

    adapter = MagicMock()
    adapter.fetch_thread_context.return_value = None

    msg = _make_thread_reply_message(text="question")
    engine.handle(msg, adapter)

    call_args = engine._llm.create_message.call_args
    messages = call_args[1].get("messages") or call_args[0][1]
    user_messages = [m for m in messages if m["role"] == "user"]
    content = user_messages[-1]["content"]
    assert content == "question"


def test_handle_no_backfill_when_adapter_returns_empty():
    """fetch_thread_context returns [] — no prefix added."""
    engine = _make_engine()

    adapter = MagicMock()
    adapter.fetch_thread_context.return_value = []

    msg = _make_thread_reply_message(text="question")
    engine.handle(msg, adapter)

    call_args = engine._llm.create_message.call_args
    messages = call_args[1].get("messages") or call_args[0][1]
    user_messages = [m for m in messages if m["role"] == "user"]
    assert user_messages[-1]["content"] == "question"


# ---------------------------------------------------------------------------
# SlackAdapterBase.fetch_thread_context — delegates to SlackClient
# ---------------------------------------------------------------------------

def test_slack_adapter_fetch_thread_context_delegates_to_slack_client():
    from unittest.mock import MagicMock, patch
    with patch("slack_bolt.App"), patch("slack_bolt.adapter.socket_mode.SocketModeHandler"):
        from mithai.adapters.slack import SlackAdapter
        adapter = SlackAdapter(bot_token="xoxb-test", app_token="xapp-test")

    mock_client = MagicMock()
    mock_client.get_thread_replies.return_value = ["alice: hello", "bob: world"]
    adapter._slack_client = mock_client

    result = adapter.fetch_thread_context("C1", "111.000")

    mock_client.get_thread_replies.assert_called_once_with("C1", "111.000")
    assert result == ["alice: hello", "bob: world"]


# ---------------------------------------------------------------------------
# SlackClient.get_thread_replies
# ---------------------------------------------------------------------------

def test_slack_client_get_thread_replies_returns_formatted_messages():
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()

    client._client.conversations_replies.return_value = {
        "ok": True,
        "messages": [
            {"user": "U1", "text": "root message"},
            {"user": "U2", "text": "first reply"},
            {"user": "U1", "text": "second reply"},
        ],
    }
    client._client.users_info.side_effect = lambda user: {
        "user": {"profile": {"display_name": user.lower()}, "name": user.lower()}
    }

    lines = client.get_thread_replies("C1", "111.000")

    client._client.conversations_replies.assert_called_once_with(
        channel="C1", ts="111.000", limit=100
    )
    assert len(lines) == 3
    assert lines[0] == "u1: root message"
    assert lines[1] == "u2: first reply"


def test_slack_client_get_thread_replies_returns_empty_on_error():
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()
    client._client.conversations_replies.side_effect = Exception("api error")

    assert client.get_thread_replies("C1", "111.000") == []


def test_slack_client_get_thread_replies_returns_empty_when_not_ok():
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()
    client._client.conversations_replies.return_value = {"ok": False, "error": "not_in_channel"}

    assert client.get_thread_replies("C1", "111.000") == []


def test_slack_client_get_thread_replies_uses_block_text_for_bot_messages():
    """Bot messages use Block Kit; msg['text'] is a truncated notification fallback.
    get_thread_replies must extract full text from blocks instead."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()

    full_text = "Here is the full deployment summary:\n- Step 1: done\n- Step 2: done\n- All checks passed."
    truncated_text = "Here is the full deployment summary:\n- Step 1: done..."

    client._client.conversations_replies.return_value = {
        "ok": True,
        "messages": [
            # Human message — no blocks, text is authoritative
            {"user": "U1", "text": "deploy the app"},
            # Bot reply — text is truncated, full content is in blocks
            {
                "bot_id": "B123",
                "text": truncated_text,
                "blocks": [
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": full_text},
                    }
                ],
            },
        ],
    }
    client._client.users_info.side_effect = lambda user: {
        "user": {"profile": {"display_name": user.lower()}, "name": user.lower()}
    }

    lines = client.get_thread_replies("C1", "111.000")

    assert len(lines) == 2
    assert lines[0] == "u1: deploy the app"
    # Must use block text, not the truncated msg['text']
    assert full_text in lines[1]
    assert truncated_text not in lines[1]


def test_slack_client_get_thread_replies_falls_back_to_text_when_no_blocks():
    """When a bot message has no blocks, fall back to msg['text'] as before."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()

    client._client.conversations_replies.return_value = {
        "ok": True,
        "messages": [
            {"bot_id": "B123", "user": "UBOT1", "text": "simple bot reply without blocks"},
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "mybot"}, "name": "mybot"}
    }

    lines = client.get_thread_replies("C1", "111.000")
    assert lines == ["mybot: simple bot reply without blocks"]


def test_slack_client_get_thread_replies_concatenates_multiple_section_blocks():
    """A bot message may have multiple section blocks; all text should be included."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()

    client._client.conversations_replies.return_value = {
        "ok": True,
        "messages": [
            {
                "bot_id": "B123",
                "text": "truncated...",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "Part one."}},
                    {"type": "divider"},  # no text — must be skipped
                    {"type": "section", "text": {"type": "mrkdwn", "text": "Part two."}},
                ],
            }
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": ""}, "name": "bot"}
    }

    lines = client.get_thread_replies("C1", "111.000")
    assert len(lines) == 1
    assert "Part one." in lines[0]
    assert "Part two." in lines[0]


def test_slack_client_get_thread_replies_regular_message_with_rich_text_blocks():
    """Modern Slack stores every user message with rich_text blocks (not section blocks).
    rich_text blocks use 'elements', not 'text', so _extract_message_text must fall back
    to msg['text'] and return the full message unchanged."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()

    client._client.conversations_replies.return_value = {
        "ok": True,
        "messages": [
            {
                "user": "U1",
                "text": "can you check disk usage?",
                "blocks": [
                    {
                        "type": "rich_text",
                        "elements": [
                            {
                                "type": "rich_text_section",
                                "elements": [{"type": "text", "text": "can you check disk usage?"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    client._client.users_info.side_effect = lambda user: {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    lines = client.get_thread_replies("C1", "111.000")
    assert lines == ["alice: can you check disk usage?"]


def test_slack_client_get_thread_replies_extracts_section_fields():
    """Section blocks may use 'fields' instead of 'text'. Both must be included in output."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()

    client._client.conversations_replies.return_value = {
        "ok": True,
        "messages": [
            {
                "bot_id": "B123",
                "user": "UBOT1",
                "text": "Approval request...",  # truncated fallback
                "blocks": [
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": "*Status:*\nPending"},
                            {"type": "mrkdwn", "text": "*Reviewer:*\nalice"},
                        ],
                    }
                ],
            }
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "mybot"}, "name": "mybot"}
    }

    lines = client.get_thread_replies("C1", "111.000")
    assert len(lines) == 1
    assert "*Status:*" in lines[0]
    assert "*Reviewer:*" in lines[0]


def test_slack_client_get_thread_replies_mixed_header_and_rich_text_uses_msg_text():
    """A message with header + rich_text blocks must NOT return just the header.
    rich_text presence signals the full content is in msg['text'] (or its elements),
    so we fall back to msg['text'] rather than returning partial block text."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()

    body_text = "Full deployment summary with all details intact."
    client._client.conversations_replies.return_value = {
        "ok": True,
        "messages": [
            {
                "bot_id": "B123",
                "user": "UBOT1",
                "text": body_text,
                "blocks": [
                    {"type": "header", "text": {"type": "plain_text", "text": "Deployment"}},
                    {
                        "type": "rich_text",
                        "elements": [
                            {"type": "rich_text_section", "elements": [{"type": "text", "text": body_text}]}
                        ],
                    },
                ],
            }
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "mybot"}, "name": "mybot"}
    }

    lines = client.get_thread_replies("C1", "111.000")
    assert len(lines) == 1
    # Must not return just "Deployment" (the header); full body must be present
    assert body_text in lines[0]


# ---------------------------------------------------------------------------
# SessionManager — append_observation / pop_observations
# ---------------------------------------------------------------------------

def test_session_manager_append_and_pop_observations():
    state = MemoryStateBackend()
    sessions = SessionManager(state)
    key = "slack:thread:111"

    # Create session with a turn so it exists
    sessions.append_turn(key, SessionManager.build_turn("u", "msg", [], "resp"))

    sessions.append_observation(key, {"user_id": "bob", "text": "hello"})
    sessions.append_observation(key, {"user_id": "charlie", "text": "world"})

    observations = sessions.pop_observations(key)
    assert len(observations) == 2
    assert observations[0]["text"] == "hello"
    assert observations[1]["text"] == "world"

    # Second pop returns empty — cleared
    assert sessions.pop_observations(key) == []


def test_session_manager_pop_observations_on_missing_session():
    state = MemoryStateBackend()
    sessions = SessionManager(state)
    assert sessions.pop_observations("slack:nonexistent") == []
