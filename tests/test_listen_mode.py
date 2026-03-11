"""Tests for respond: mentions / respond: all listen mode."""

import sys
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_app():
    app = MagicMock()
    app.client = MagicMock()
    app.client.auth_test.return_value = {"user_id": "UBOT"}
    app.action = MagicMock(side_effect=lambda name: (lambda fn: fn))
    app.event = MagicMock(side_effect=lambda name: (lambda fn: fn))
    app.message = MagicMock(side_effect=lambda pattern: (lambda fn: fn))
    return app


def _build_adapter(respond="all", allowed_channels=None):
    """Build a SlackAdapterBase subclass (HTTP) with the given respond mode."""
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)
    with patch("slack_bolt.App", mock_app_cls, create=True):
        from mithai.adapters.slack_http import SlackHTTPAdapter
        adapter = SlackHTTPAdapter(
            bot_token="xoxb-test",
            signing_secret="sig",
            allowed_channels=allowed_channels,
            respond=respond,
        )
    return adapter, mock_app


def _capture_message_handler(adapter, mock_app, on_message, on_observe=None):
    """Register handlers and return the captured handle_message function."""
    captured = {}

    def capture_decorator(pattern):
        def decorator(fn):
            captured["fn"] = fn
            return fn
        return decorator

    mock_app.message.side_effect = capture_decorator
    adapter._register_message_handlers(on_message, on_observe=on_observe)
    return captured.get("fn")


# ---------------------------------------------------------------------------
# 1. respond: mentions — non-mention calls on_observe, not on_message
# ---------------------------------------------------------------------------

def test_respond_mentions_calls_on_observe_not_on_message():
    adapter, mock_app = _build_adapter(respond="mentions")
    on_message = MagicMock(return_value="reply")
    on_observe = MagicMock()

    handle_message = _capture_message_handler(adapter, mock_app, on_message, on_observe=on_observe)

    fake_message = {"channel": "C1", "text": "just a regular message", "ts": "1.0", "user": "U1"}
    handle_message(message=fake_message, say=MagicMock())

    on_observe.assert_called_once()
    on_message.assert_not_called()


# ---------------------------------------------------------------------------
# 2. respond: mentions — say() never called for non-mention messages
# ---------------------------------------------------------------------------

def test_respond_mentions_no_say():
    adapter, mock_app = _build_adapter(respond="mentions")
    on_message = MagicMock(return_value="reply")
    handle_message = _capture_message_handler(adapter, mock_app, on_message)

    fake_say = MagicMock()
    fake_message = {"channel": "C1", "text": "no mention here", "ts": "1.0", "user": "U1"}
    handle_message(message=fake_message, say=fake_say)

    fake_say.assert_not_called()
    on_message.assert_not_called()


# ---------------------------------------------------------------------------
# 3. respond: all — on_message called (default behaviour unchanged)
# ---------------------------------------------------------------------------

def test_respond_all_calls_on_message():
    adapter, mock_app = _build_adapter(respond="all")
    on_message = MagicMock(return_value="reply")
    handle_message = _capture_message_handler(adapter, mock_app, on_message)

    fake_say = MagicMock()
    fake_message = {"channel": "C1", "text": "hello bot", "ts": "1.0", "user": "U1"}
    handle_message(message=fake_message, say=fake_say)

    on_message.assert_called_once()


# ---------------------------------------------------------------------------
# 4. app_mention handler always calls on_message regardless of respond setting
# ---------------------------------------------------------------------------

def test_mention_always_calls_on_message():
    adapter, mock_app = _build_adapter(respond="mentions")
    on_message = MagicMock(return_value="reply")

    # Capture app_mention handler
    captured = {}

    def capture_event(name):
        def decorator(fn):
            captured[name] = fn
            return fn
        return decorator

    mock_app.event.side_effect = capture_event
    adapter._register_message_handlers(on_message)

    handle_app_mention = captured.get("app_mention")
    assert handle_app_mention is not None

    fake_say = MagicMock()
    event = {"channel": "C1", "text": "<@UBOT> deploy please", "ts": "2.0", "user": "U2"}
    handle_app_mention(event=event, say=fake_say)

    on_message.assert_called_once()


def test_mention_also_calls_on_observe():
    """After responding to an @mention, on_observe is called so it lands in channel_context."""
    adapter, mock_app = _build_adapter(respond="mentions")
    on_message = MagicMock(return_value="reply")
    on_observe = MagicMock()

    captured = {}

    def capture_event(name):
        def decorator(fn):
            captured[name] = fn
            return fn
        return decorator

    mock_app.event.side_effect = capture_event
    adapter._register_message_handlers(on_message, on_observe=on_observe)

    handle_app_mention = captured.get("app_mention")
    event = {"channel": "C1", "text": "<@UBOT> deploy please", "ts": "2.0", "user": "U2"}
    handle_app_mention(event=event, say=MagicMock())

    on_observe.assert_called_once()
    observed_msg = on_observe.call_args[0][0]
    assert observed_msg.text == "deploy please"
    assert observed_msg.channel_id == "C1"


# ---------------------------------------------------------------------------
# 5. engine.observe() appends formatted line to channel_context/{channel_id}.md
# ---------------------------------------------------------------------------

def test_engine_observe_writes_to_memory(tmp_path):
    from mithai.memory.filesystem import FilesystemMemoryBackend
    from mithai.adapters.base import IncomingMessage

    memory = FilesystemMemoryBackend(str(tmp_path))

    # Build a minimal engine with a real memory backend
    mock_llm = MagicMock()
    mock_state = MagicMock()

    with patch("mithai.core.engine.get_skill_paths", return_value=[]), \
         patch("mithai.core.engine.load_skills", return_value={}), \
         patch("mithai.core.engine.get_mcp_config", return_value={}), \
         patch("mithai.core.engine.get_human_config", return_value={}), \
         patch("mithai.core.engine.get_llm_config", return_value={}):
        from mithai.core.engine import Engine
        engine = Engine(config={}, llm=mock_llm, state=mock_state, memory=memory)

    msg = IncomingMessage(text="deploy just went out", channel_id="C999", user_id="U42", platform="slack")
    engine.observe(msg)

    content = memory.read("channel_context/C999.md")
    assert content is not None
    assert "U42" in content
    assert "deploy just went out" in content
    # message_id (Slack ts or UUID hex) should appear, not ISO wall-clock time
    assert msg.message_id in content


# ---------------------------------------------------------------------------
# 6. engine.observe() is a no-op when memory is None
# ---------------------------------------------------------------------------

def test_engine_observe_noop_when_no_memory():
    from mithai.adapters.base import IncomingMessage

    mock_llm = MagicMock()
    mock_state = MagicMock()

    with patch("mithai.core.engine.get_skill_paths", return_value=[]), \
         patch("mithai.core.engine.load_skills", return_value={}), \
         patch("mithai.core.engine.get_mcp_config", return_value={}), \
         patch("mithai.core.engine.get_human_config", return_value={}), \
         patch("mithai.core.engine.get_llm_config", return_value={}):
        from mithai.core.engine import Engine
        engine = Engine(config={}, llm=mock_llm, state=mock_state, memory=None)

    msg = IncomingMessage(text="hello", channel_id="C1", user_id="U1", platform="slack")
    # Must not raise
    engine.observe(msg)


# ---------------------------------------------------------------------------
# 7. _create_adapter forwards respond to SlackAdapter
# ---------------------------------------------------------------------------

def test_run_cmd_passes_respond_to_adapter():
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)
    mock_handler_cls = MagicMock()

    adapter_config = {
        "bot_token": "xoxb-test",
        "app_token": "xapp-test",
        "respond": "mentions",
    }

    with patch("slack_bolt.App", mock_app_cls, create=True), \
         patch("slack_bolt.adapter.socket_mode.SocketModeHandler", mock_handler_cls, create=True):
        from mithai.cli.run_cmd import _create_adapter
        adapter = _create_adapter({}, "slack", adapter_config=adapter_config, respond="mentions")

    assert adapter._respond == "mentions"


# ---------------------------------------------------------------------------
# 8. adapter.start() receives on_observe=engine.observe when respond: mentions
# ---------------------------------------------------------------------------

def test_run_cmd_on_observe_passed_when_mentions_mode():
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)
    mock_handler_cls = MagicMock()

    with patch("slack_bolt.App", mock_app_cls, create=True), \
         patch("slack_bolt.adapter.socket_mode.SocketModeHandler", mock_handler_cls, create=True):
        from mithai.adapters.slack import SlackAdapter
        adapter = SlackAdapter(
            bot_token="xoxb-test",
            app_token="xapp-test",
            respond="mentions",
        )

    on_observe_fn = MagicMock()
    start_calls = {}

    def capture_register(on_message, on_channel_join=None, on_observe=None, on_bot_reply=None):
        start_calls["on_observe"] = on_observe

    adapter._register_message_handlers = capture_register

    with patch.object(adapter._handler, "start"):
        adapter.start(on_message=MagicMock(), on_observe=on_observe_fn)

    assert start_calls["on_observe"] is on_observe_fn


# ---------------------------------------------------------------------------
# 9. on_observe is None when respond: all
# ---------------------------------------------------------------------------

def test_run_cmd_on_observe_always_passed_in_all_mode():
    """start() must forward on_observe to _register_message_handlers — verified
    by calling the real start() method, not a fake replacement."""
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)

    mock_uvicorn = MagicMock()
    mock_server = MagicMock()
    mock_uvicorn.Server.return_value = mock_server

    class FakeRoute:
        def __init__(self, path, endpoint, methods=None):
            self.path = path

    mock_starlette_apps = MagicMock()
    mock_starlette_routing = MagicMock()
    mock_starlette_routing.Route = FakeRoute
    mock_starlette_apps.Starlette.return_value = MagicMock()

    fake_modules = {
        "uvicorn": mock_uvicorn,
        "starlette": MagicMock(),
        "starlette.applications": mock_starlette_apps,
        "starlette.routing": mock_starlette_routing,
        "starlette.requests": MagicMock(),
        "slack_bolt.adapter.starlette": MagicMock(),
    }

    captured = {}

    with patch("slack_bolt.App", mock_app_cls, create=True), \
         patch.dict(sys.modules, fake_modules):
        from mithai.adapters.slack_http import SlackHTTPAdapter
        adapter = SlackHTTPAdapter(bot_token="xoxb-test", signing_secret="sig", respond="all")

        def capture_register(on_message, on_channel_join=None, on_observe=None, on_bot_reply=None):
            captured["on_observe"] = on_observe

        adapter._register_message_handlers = capture_register
        on_observe_fn = MagicMock()
        adapter.start(on_message=MagicMock(), on_observe=on_observe_fn)

    assert captured["on_observe"] is on_observe_fn


# ---------------------------------------------------------------------------
# 10. on_bot_reply is called with (channel, bot_user_id, response, ts)
# ---------------------------------------------------------------------------

def test_on_bot_reply_called_after_handle_message():
    """on_bot_reply must be called with (channel, bot_user_id, response, ts)
    after a message is handled — this logs the reply to channel_context."""
    adapter, mock_app = _build_adapter(respond="all")
    on_bot_reply = MagicMock()

    captured = {}

    def capture_decorator(pattern):
        def decorator(fn):
            captured["fn"] = fn
            return fn
        return decorator

    mock_app.message.side_effect = capture_decorator
    adapter._register_message_handlers(MagicMock(return_value="hello there"), on_bot_reply=on_bot_reply)
    handle_message = captured["fn"]

    fake_say = MagicMock()
    fake_message = {"channel": "C1", "text": "hi", "ts": "1.0", "user": "U1"}
    handle_message(message=fake_message, say=fake_say)

    on_bot_reply.assert_called_once_with("C1", "UBOT", "hello there", "1.0")


def test_on_bot_reply_called_after_app_mention():
    """on_bot_reply must be called after an app_mention is handled."""
    adapter, mock_app = _build_adapter(respond="all")
    on_bot_reply = MagicMock()

    captured = {}

    def capture_event(name):
        def decorator(fn):
            captured[name] = fn
            return fn
        return decorator

    mock_app.event.side_effect = capture_event
    adapter._register_message_handlers(MagicMock(return_value="pong"), on_bot_reply=on_bot_reply)
    handle_app_mention = captured.get("app_mention")

    fake_say = MagicMock()
    fake_event = {"channel": "C2", "text": "<@UBOT> ping", "ts": "2.0", "user": "U2"}
    handle_app_mention(event=fake_event, say=fake_say)

    on_bot_reply.assert_called_once_with("C2", "UBOT", "pong", "2.0")


def test_on_bot_reply_not_called_when_none():
    """If on_bot_reply is not provided, no AttributeError — just skipped."""
    adapter, mock_app = _build_adapter(respond="all")

    captured = {}

    def capture_decorator(pattern):
        def decorator(fn):
            captured["fn"] = fn
            return fn
        return decorator

    mock_app.message.side_effect = capture_decorator
    adapter._register_message_handlers(MagicMock(return_value="hi"), on_bot_reply=None)
    handle_message = captured["fn"]

    # Should not raise
    handle_message(message={"channel": "C1", "text": "test", "ts": "1.0", "user": "U1"},
                   say=MagicMock())


# ---------------------------------------------------------------------------
# 11. Thread session continuity — thread_id must equal ts on first message
#     so replies (thread_ts = that ts) land in the same session.
# ---------------------------------------------------------------------------

def _capture_app_mention_handler(adapter, mock_app, on_message):
    """Register handlers and return the captured app_mention handler."""
    captured = {}

    def capture_event(name):
        def decorator(fn):
            captured[name] = fn
            return fn
        return decorator

    mock_app.event.side_effect = capture_event
    adapter._register_message_handlers(on_message)
    return captured.get("app_mention")


def test_handle_message_thread_id_equals_ts_when_no_thread_ts():
    """First message in a channel has no thread_ts.
    thread_id must be set to ts so that the reply (thread_ts=ts) hits the same session."""
    adapter, mock_app = _build_adapter(respond="all")
    captured_incoming = {}

    def on_message(incoming, adapter):
        captured_incoming["msg"] = incoming
        return "ok"

    handle_message = _capture_message_handler(adapter, mock_app, on_message)

    fake_message = {"channel": "C1", "text": "hello", "ts": "111.000", "user": "U1"}
    handle_message(message=fake_message, say=MagicMock())

    msg = captured_incoming["msg"]
    assert msg.thread_id == "111.000", (
        f"thread_id should equal ts ('111.000') so replies land in the same session, got {msg.thread_id!r}"
    )


def test_handle_message_thread_id_equals_thread_ts_for_reply():
    """A reply carries thread_ts; thread_id must equal thread_ts."""
    adapter, mock_app = _build_adapter(respond="all")
    captured_incoming = {}

    def on_message(incoming, adapter):
        captured_incoming["msg"] = incoming
        return "ok"

    handle_message = _capture_message_handler(adapter, mock_app, on_message)

    reply = {"channel": "C1", "text": "reply", "ts": "222.000", "thread_ts": "111.000", "user": "U1"}
    handle_message(message=reply, say=MagicMock())

    msg = captured_incoming["msg"]
    assert msg.thread_id == "111.000"


def test_handle_app_mention_thread_id_equals_ts_when_no_thread_ts():
    """First @mention in a channel has no thread_ts.
    thread_id must be set to ts so replies hit the same session."""
    adapter, mock_app = _build_adapter(respond="all")
    captured_incoming = {}

    def on_message(incoming, adapter):
        captured_incoming["msg"] = incoming
        return "ok"

    handle_app_mention = _capture_app_mention_handler(adapter, mock_app, on_message)

    fake_event = {"channel": "C1", "text": "<@UBOT> hello", "ts": "111.000", "user": "U1"}
    handle_app_mention(event=fake_event, say=MagicMock())

    msg = captured_incoming["msg"]
    assert msg.thread_id == "111.000", (
        f"thread_id should equal ts ('111.000') so replies land in the same session, got {msg.thread_id!r}"
    )
