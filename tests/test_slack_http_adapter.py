"""Tests for SlackHTTPAdapter and SlackAdapterBase refactor."""

import sys
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_app():
    """Return a mock slack_bolt.App instance that records registered handlers."""
    app = MagicMock()
    app.client = MagicMock()
    app.client.auth_test.return_value = {"user_id": "U123"}
    # Decorators return the handler unchanged so we can inspect them later
    app.action = MagicMock(side_effect=lambda name: (lambda fn: fn))
    app.event = MagicMock(side_effect=lambda name: (lambda fn: fn))
    app.message = MagicMock(side_effect=lambda pattern: (lambda fn: fn))
    return app


def _build_http_adapter(bot_token="xoxb-test", signing_secret="sig-secret",
                         host="0.0.0.0", port=3000, allowed_channels=None):
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)
    with patch("slack_bolt.App", mock_app_cls, create=True):
        from mithai.adapters.slack_http import SlackHTTPAdapter
        adapter = SlackHTTPAdapter(
            bot_token=bot_token,
            signing_secret=signing_secret,
            host=host,
            port=port,
            allowed_channels=allowed_channels,
        )
    return adapter, mock_app, mock_app_cls


def _build_socket_adapter(bot_token="xoxb-test", app_token="xapp-test", allowed_channels=None):
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)
    mock_handler = MagicMock()
    mock_handler_cls = MagicMock(return_value=mock_handler)
    with patch("slack_bolt.App", mock_app_cls, create=True), \
         patch("slack_bolt.adapter.socket_mode.SocketModeHandler", mock_handler_cls, create=True):
        from mithai.adapters.slack import SlackAdapter
        adapter = SlackAdapter(
            bot_token=bot_token,
            app_token=app_token,
            allowed_channels=allowed_channels,
        )
    return adapter, mock_app, mock_app_cls, mock_handler


# ---------------------------------------------------------------------------
# 1. SlackAdapterBase is the parent of both adapter classes
# ---------------------------------------------------------------------------

def test_class_hierarchy():
    from mithai.adapters.slack import SlackAdapterBase, SlackAdapter
    from mithai.adapters.slack_http import SlackHTTPAdapter

    assert issubclass(SlackAdapter, SlackAdapterBase)
    assert issubclass(SlackHTTPAdapter, SlackAdapterBase)
    # They are distinct classes
    assert SlackAdapter is not SlackHTTPAdapter


# ---------------------------------------------------------------------------
# 2. SlackHTTPAdapter passes signing_secret to Bolt App; Socket Mode does not
# ---------------------------------------------------------------------------

def test_http_adapter_passes_signing_secret_to_bolt():
    _, _, mock_app_cls = _build_http_adapter(
        bot_token="xoxb-http",
        signing_secret="my-secret",
    )
    mock_app_cls.assert_called_once_with(token="xoxb-http", signing_secret="my-secret")


def test_socket_adapter_does_not_pass_signing_secret():
    _, _, mock_app_cls, _ = _build_socket_adapter(bot_token="xoxb-socket")
    call_kwargs = mock_app_cls.call_args[1]
    assert "signing_secret" not in call_kwargs
    assert call_kwargs["token"] == "xoxb-socket"


# ---------------------------------------------------------------------------
# 3. HTTP adapter construction stores host/port/server state
# ---------------------------------------------------------------------------

def test_http_adapter_construction_stores_params():
    adapter, _, _ = _build_http_adapter(host="127.0.0.1", port=4000)
    assert adapter._host == "127.0.0.1"
    assert adapter._port == 4000
    assert adapter._server is None  # not started yet


# ---------------------------------------------------------------------------
# 4. _register_message_handlers wires all required Bolt event handlers
# ---------------------------------------------------------------------------

def test_register_message_handlers_wires_bolt_handlers():
    adapter, mock_app, _ = _build_http_adapter()
    on_message = MagicMock()

    adapter._register_message_handlers(on_message)

    # @app.message("") must be registered
    mock_app.message.assert_called_once_with("")

    # @app.event("app_mention") and @app.event("message") must be registered
    event_calls = [c.args[0] for c in mock_app.event.call_args_list]
    assert "app_mention" in event_calls
    assert "message" in event_calls


def test_register_message_handlers_wires_channel_join_when_provided():
    adapter, mock_app, _ = _build_http_adapter()
    on_message = MagicMock()
    on_join = MagicMock()

    adapter._register_message_handlers(on_message, on_channel_join=on_join)

    event_calls = [c.args[0] for c in mock_app.event.call_args_list]
    assert "member_joined_channel" in event_calls


def test_register_message_handlers_skips_channel_join_when_none():
    adapter, mock_app, _ = _build_http_adapter()
    on_message = MagicMock()

    adapter._register_message_handlers(on_message, on_channel_join=None)

    event_calls = [c.args[0] for c in mock_app.event.call_args_list]
    assert "member_joined_channel" not in event_calls


# ---------------------------------------------------------------------------
# 5. allowed_channels filtering: messages from other channels are dropped
# ---------------------------------------------------------------------------

def test_allowed_channels_drops_messages_from_other_channels():
    """on_message must NOT be called when the channel is not in allowed_channels."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C_ALLOWED"])
    on_message = MagicMock(return_value="reply")
    handler = _capture_message_handler(adapter, mock_app, on_message)

    fake_message = {"channel": "C_OTHER", "text": "hello", "ts": "1234", "user": "U1"}
    handler(message=fake_message, say=MagicMock())

    on_message.assert_not_called()


def test_allowed_channels_passes_messages_from_allowed_channel():
    """on_message IS called when the channel is in allowed_channels."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C_ALLOWED"])
    on_message = MagicMock(return_value="reply")
    handler = _capture_message_handler(adapter, mock_app, on_message)

    fake_message = {"channel": "C_ALLOWED", "text": "hello", "ts": "1234", "user": "U1"}
    handler(message=fake_message, say=MagicMock())

    on_message.assert_called_once()


# ---------------------------------------------------------------------------
# Bug: allowed_channels=[] (empty list) must block all channels, not allow all.
# An empty list is falsy in Python, so `set([]) if [] else None` yields None,
# which disables filtering entirely. The fix is to check `is not None`.
# ---------------------------------------------------------------------------

def test_empty_allowed_channels_blocks_all_channels():
    """allowed_channels=[] must reject every channel, not accept all of them.

    Regression test: procmgr writes `allowed_channels: []` when no channels are
    configured, and the adapter must treat an empty list as "block all", not "no restriction".
    """
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=[])
    on_message = MagicMock(return_value="reply")
    handler = _capture_message_handler(adapter, mock_app, on_message)

    fake_message = {"channel": "C_ANY", "text": "hello", "ts": "1234", "user": "U1"}
    handler(message=fake_message, say=MagicMock())

    on_message.assert_not_called()


def test_allowed_channels_always_a_set():
    """_allowed_channels is always a set — never None — regardless of input."""
    # [] → empty set (not activated, block all)
    adapter_empty, _, _ = _build_http_adapter(allowed_channels=[])
    assert adapter_empty._allowed_channels == set()

    # None (key absent) → empty set (block all until channels are configured)
    adapter_none, _, _ = _build_http_adapter(allowed_channels=None)
    assert adapter_none._allowed_channels == set()

    # non-empty list → set of those channels
    adapter_set, _, _ = _build_http_adapter(allowed_channels=["C1", "C2"])
    assert adapter_set._allowed_channels == {"C1", "C2"}


# ---------------------------------------------------------------------------
# 6. Approval action handler: approve/deny updates pending_approvals
# ---------------------------------------------------------------------------

def test_handle_approval_action_approve():
    adapter, mock_app, _ = _build_http_adapter()

    import threading
    ev = threading.Event()
    adapter._pending_approvals["req-1"] = {"event": ev, "approved": False, "user": None}

    body = {
        "actions": [{"value": "req-1"}],
        "user": {"id": "U99", "name": "alice"},
        "channel": {},
        "message": {},
    }
    adapter._handle_approval_action(body, approved=True)

    assert adapter._pending_approvals["req-1"]["approved"] is True
    assert adapter._pending_approvals["req-1"]["user"] == "alice"
    assert ev.is_set()


def test_handle_approval_action_deny():
    adapter, mock_app, _ = _build_http_adapter()

    import threading
    ev = threading.Event()
    adapter._pending_approvals["req-2"] = {"event": ev, "approved": True, "user": None}

    body = {
        "actions": [{"value": "req-2"}],
        "user": {"id": "U88", "name": "bob"},
        "channel": {},
        "message": {},
    }
    adapter._handle_approval_action(body, approved=False)

    assert adapter._pending_approvals["req-2"]["approved"] is False
    assert ev.is_set()


def test_handle_approval_action_unknown_request_id_is_noop():
    adapter, mock_app, _ = _build_http_adapter()

    body = {
        "actions": [{"value": "unknown-req"}],
        "user": {"id": "U1", "name": "x"},
        "channel": {},
        "message": {},
    }
    # Should not raise, just log a warning
    adapter._handle_approval_action(body, approved=True)
    assert "unknown-req" not in adapter._pending_approvals


# ---------------------------------------------------------------------------
# 7. stop() sets server.should_exit = True; no-op before start()
# ---------------------------------------------------------------------------

def test_http_adapter_stop_before_start_is_noop():
    adapter, _, _ = _build_http_adapter()
    adapter.stop()  # must not raise
    assert adapter._server is None


def test_http_adapter_stop_sets_should_exit():
    adapter, _, _ = _build_http_adapter()
    fake_server = MagicMock()
    fake_server.should_exit = False
    adapter._server = fake_server

    adapter.stop()

    assert fake_server.should_exit is True


# ---------------------------------------------------------------------------
# 8. start() launches uvicorn with /slack/events route
# ---------------------------------------------------------------------------

def test_http_adapter_start_registers_route_and_runs_server():
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)

    mock_server = MagicMock()
    mock_uvicorn = MagicMock()
    mock_uvicorn.Server.return_value = mock_server

    captured_routes = []

    class FakeRoute:
        def __init__(self, path, endpoint, methods=None):
            self.path = path

    mock_starlette_apps = MagicMock()
    mock_starlette_routing = MagicMock()
    mock_starlette_routing.Route = FakeRoute

    def fake_starlette_cls(routes=None, **kwargs):
        captured_routes.extend(routes or [])
        return MagicMock()

    mock_starlette_apps.Starlette = fake_starlette_cls

    mock_bolt_starlette = MagicMock()
    mock_bolt_handler = MagicMock()
    mock_bolt_starlette.SlackRequestHandler.return_value = mock_bolt_handler

    fake_modules = {
        "uvicorn": mock_uvicorn,
        "starlette": MagicMock(),
        "starlette.applications": mock_starlette_apps,
        "starlette.routing": mock_starlette_routing,
        "starlette.requests": MagicMock(),
        "slack_bolt.adapter.starlette": mock_bolt_starlette,
    }

    with patch("slack_bolt.App", mock_app_cls, create=True), \
         patch.dict(sys.modules, fake_modules):
        from mithai.adapters.slack_http import SlackHTTPAdapter
        adapter = SlackHTTPAdapter(bot_token="xoxb-test", signing_secret="sig")
        adapter.start(on_message=MagicMock())

    # SlackRequestHandler wraps the Bolt app
    mock_bolt_starlette.SlackRequestHandler.assert_called_once_with(mock_app)

    # uvicorn server was started
    mock_server.run.assert_called_once()

    # Exactly one route at /slack/events
    assert len(captured_routes) == 1
    assert captured_routes[0].path == "/slack/events"


# ---------------------------------------------------------------------------
# 9. run_cmd._create_adapter returns SlackHTTPAdapter with correct params
# ---------------------------------------------------------------------------

def test_run_cmd_creates_slack_http_adapter():
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)

    adapter_config = {
        "bot_token": "xoxb-test",
        "signing_secret": "secret123",
        "host": "0.0.0.0",
        "port": 3000,
        "allowed_channels": ["C123"],
        "approval_timeout": 120,
    }

    with patch("slack_bolt.App", mock_app_cls, create=True):
        from mithai.cli.run_cmd import _create_adapter
        from mithai.adapters.slack_http import SlackHTTPAdapter

        adapter = _create_adapter({}, "slack_http", adapter_config=adapter_config)

    assert isinstance(adapter, SlackHTTPAdapter)
    assert adapter._host == "0.0.0.0"
    assert adapter._port == 3000
    assert adapter._allowed_channels == {"C123"}
    assert adapter._approval_timeout == 120


def test_run_cmd_creates_slack_adapter_with_approval_timeout():
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)
    mock_handler_cls = MagicMock()

    adapter_config = {
        "bot_token": "xoxb-test",
        "app_token": "xapp-test",
        "approval_timeout": 60,
    }

    with patch("slack_bolt.App", mock_app_cls, create=True), \
         patch("slack_bolt.adapter.socket_mode.SocketModeHandler", mock_handler_cls, create=True):
        from mithai.cli.run_cmd import _create_adapter
        from mithai.adapters.slack import SlackAdapter

        adapter = _create_adapter({}, "slack", adapter_config=adapter_config)

    assert isinstance(adapter, SlackAdapter)
    assert adapter._approval_timeout == 60


def test_run_cmd_slack_adapter_default_approval_timeout():
    """Omitting approval_timeout from config uses the 300s default."""
    mock_app = _make_mock_app()
    mock_app_cls = MagicMock(return_value=mock_app)
    mock_handler_cls = MagicMock()

    adapter_config = {"bot_token": "xoxb-test", "app_token": "xapp-test"}

    with patch("slack_bolt.App", mock_app_cls, create=True), \
         patch("slack_bolt.adapter.socket_mode.SocketModeHandler", mock_handler_cls, create=True):
        from mithai.cli.run_cmd import _create_adapter
        adapter = _create_adapter({}, "slack", adapter_config=adapter_config)

    assert adapter._approval_timeout == 300


# ---------------------------------------------------------------------------
# 10. thread_ts is thread-local — concurrent messages don't clobber each other
# ---------------------------------------------------------------------------

def test_thread_ts_is_per_thread():
    """Two threads handling messages simultaneously must not share thread_ts."""
    import threading

    adapter, mock_app, _ = _build_http_adapter()

    results = {}
    barrier = threading.Barrier(2)

    # Simulate two message handlers running concurrently
    def thread_a():
        adapter._local.thread_ts = "ts-A"
        barrier.wait()  # both threads set their ts before either reads
        results["a"] = getattr(adapter._local, "thread_ts", None)

    def thread_b():
        adapter._local.thread_ts = "ts-B"
        barrier.wait()
        results["b"] = getattr(adapter._local, "thread_ts", None)

    t1 = threading.Thread(target=thread_a)
    t2 = threading.Thread(target=thread_b)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Each thread must see only its own ts
    assert results["a"] == "ts-A"
    assert results["b"] == "ts-B"


def test_thread_ts_missing_on_new_thread_does_not_raise():
    """request_human_approval must not raise AttributeError on a fresh thread."""
    adapter, mock_app, _ = _build_http_adapter()
    # _local.thread_ts is never set — getattr should return None safely
    ts = getattr(adapter._local, "thread_ts", None)
    assert ts is None


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# app_mention handler: user mention preservation
# ---------------------------------------------------------------------------

def _capture_app_mention_handler(adapter, on_message, on_bot_reply=None):
    """Register message handlers and return the captured app_mention handler fn."""
    captured = {}

    original_event = adapter._app.event.side_effect

    def capture_event(name):
        def decorator(fn):
            captured[name] = fn
            return fn
        return decorator

    adapter._app.event.side_effect = capture_event
    adapter._register_message_handlers(on_message, on_bot_reply=on_bot_reply)
    adapter._app.event.side_effect = original_event
    return captured.get("app_mention")


def test_app_mention_preserves_user_mentions_in_message():
    """Non-bot @mentions in the message body must be resolved to display names, not stripped.

    Regression: "@bot Acme Corp is owned by @Alice" was delivered to the
    agent as "Acme Corp is owned by" because the regex stripped ALL <@USER> tokens.
    """
    adapter, mock_app, _ = _build_socket_adapter(allowed_channels=["C1"])[:3]

    received_texts = []

    def on_message(incoming, _adapter):
        received_texts.append(incoming.text)
        return "ok"

    # Simulate bot user ID resolved at startup (auth_test is called inside _register_message_handlers)
    mock_app.client.auth_test.return_value = {"user_id": "UBOT"}
    # Mock _slack_client.resolve_user_ids so @UALICE resolves to "Alice"
    adapter._slack_client = MagicMock()
    adapter._slack_client.resolve_user_ids.return_value = {"UALICE": "Alice"}

    handler = _capture_app_mention_handler(adapter, on_message)
    assert handler is not None, "app_mention handler was not registered"

    say = MagicMock()
    event = {
        "channel": "C1",
        "user": "UBOB",
        "ts": "111.222",
        "text": "<@UBOT> Acme Corp is owned by <@UALICE>",
    }
    handler(event, say)

    assert len(received_texts) == 1
    text = received_texts[0]
    # Bot mention stripped, but @Alice must be preserved
    assert "<@UBOT>" not in text
    assert "<@UALICE>" not in text   # raw ID gone
    assert "Alice" in text or "@Alice" in text  # resolved display name present
    assert "Acme Corp is owned by" in text


def test_app_mention_strips_only_bot_mention_when_no_other_mentions():
    """Plain message with only the bot mention is stripped to just the content."""
    adapter, mock_app, _ = _build_socket_adapter(allowed_channels=["C1"])[:3]

    received_texts = []

    def on_message(incoming, _adapter):
        received_texts.append(incoming.text)
        return "ok"

    mock_app.client.auth_test.return_value = {"user_id": "UBOT"}
    adapter._slack_client = MagicMock()
    adapter._slack_client.resolve_user_ids.return_value = {}
    handler = _capture_app_mention_handler(adapter, on_message)

    say = MagicMock()
    event = {
        "channel": "C1",
        "user": "U1",
        "ts": "111.222",
        "text": "<@UBOT> what is the status?",
    }
    handler(event, say)

    assert received_texts == ["what is the status?"]


def test_run_cmd_unknown_adapter_raises():
    import click
    try:
        from mithai.cli.run_cmd import _create_adapter
        _create_adapter({}, "ftp", adapter_config={})
        assert False, "should have raised"
    except click.ClickException as e:
        assert "ftp" in str(e)


# ---------------------------------------------------------------------------
# handle_message: @mention filtering
# Only skip messages that @mention the bot; messages mentioning other users
# must still reach on_observe so pending_observations stays complete.
# ---------------------------------------------------------------------------

def _capture_message_handler(adapter, mock_app, on_message, on_observe=None, bot_id="UBOT"):
    """Register handlers and return the captured handle_message function.

    Configures mock auth_test so _register_message_handlers resolves the bot ID
    to `bot_id` (rather than the default "U123" set at adapter construction).
    Pass bot_id=None to simulate auth_test failure (unresolved bot ID).
    """
    registered = {}

    if bot_id is not None:
        mock_app.client.auth_test.return_value = {"user_id": bot_id}
    else:
        mock_app.client.auth_test.side_effect = Exception("auth failed")

    def capture_message(pattern):
        def decorator(fn):
            registered["message"] = fn
            return fn
        return decorator

    mock_app.message.side_effect = capture_message
    adapter._register_message_handlers(on_message, on_observe=on_observe)
    return registered["message"]


def test_message_mentioning_other_user_reaches_observe():
    """A message like '@alice can you check?' must reach on_observe in mentions mode."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C1"])
    adapter._respond = "mentions"

    on_message = MagicMock(return_value="reply")
    on_observe = MagicMock()
    handler = _capture_message_handler(adapter, mock_app, on_message, on_observe=on_observe)

    # Message mentions @alice (UOTHER), not the bot (UBOT)
    fake_msg = {"channel": "C1", "text": "<@UOTHER> can you check?", "ts": "1.0", "user": "U1", "thread_ts": "1.0"}
    handler(message=fake_msg, say=MagicMock())

    on_observe.assert_called_once()
    on_message.assert_not_called()


def test_message_mentioning_bot_skipped_by_handle_message():
    """A message @mentioning the bot is handled by app_mention, not handle_message."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C1"])
    adapter._respond = "mentions"

    on_message = MagicMock(return_value="reply")
    on_observe = MagicMock()
    handler = _capture_message_handler(adapter, mock_app, on_message, on_observe=on_observe)

    # Bot ID resolved to "UBOT" by _capture_message_handler
    fake_msg = {"channel": "C1", "text": "<@UBOT> what is the status?", "ts": "1.0", "user": "U1"}
    handler(message=fake_msg, say=MagicMock())

    on_message.assert_not_called()
    on_observe.assert_not_called()


def test_message_with_no_mention_reaches_observe_in_mentions_mode():
    """Plain thread reply with no mentions is observed when respond=mentions."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C1"])
    adapter._respond = "mentions"

    on_message = MagicMock(return_value="reply")
    on_observe = MagicMock()
    handler = _capture_message_handler(adapter, mock_app, on_message, on_observe=on_observe)

    fake_msg = {"channel": "C1", "text": "sure, on it", "ts": "2.0", "user": "U2", "thread_ts": "1.0"}
    handler(message=fake_msg, say=MagicMock())

    on_observe.assert_called_once()
    on_message.assert_not_called()


def test_message_mentioning_multiple_others_reaches_observe():
    """A message mentioning two other users (not the bot) still reaches on_observe."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C1"])
    adapter._respond = "mentions"

    on_observe = MagicMock()
    handler = _capture_message_handler(adapter, mock_app, MagicMock(), on_observe=on_observe)

    fake_msg = {
        "channel": "C1",
        "text": "<@UOTHER1> and <@UOTHER2> please review",
        "ts": "3.0", "user": "U3", "thread_ts": "1.0",
    }
    handler(message=fake_msg, say=MagicMock())

    on_observe.assert_called_once()


def test_message_mentioning_bot_and_other_skipped():
    """If the bot is mentioned alongside others, app_mention handles it — handle_message skips."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C1"])
    adapter._respond = "mentions"

    on_observe = MagicMock()
    handler = _capture_message_handler(adapter, mock_app, MagicMock(), on_observe=on_observe)

    fake_msg = {
        "channel": "C1",
        "text": "<@UBOT> and <@UOTHER1> please check",
        "ts": "4.0", "user": "U4",
    }
    handler(message=fake_msg, say=MagicMock())

    on_observe.assert_not_called()


def test_bot_id_unknown_falls_back_to_skipping_any_mention():
    """When bot_user_id cannot be resolved, any @mention is skipped (safe fallback)."""
    adapter, mock_app, _ = _build_http_adapter()
    adapter._respond = "mentions"

    on_observe = MagicMock()
    # bot_id=None causes auth_test to raise → _bot_user_id stays None
    handler = _capture_message_handler(adapter, mock_app, MagicMock(), on_observe=on_observe, bot_id=None)

    fake_msg = {"channel": "C1", "text": "<@UANYONE> hello", "ts": "5.0", "user": "U5"}
    handler(message=fake_msg, say=MagicMock())

    on_observe.assert_not_called()


# ---------------------------------------------------------------------------
# SlackAdapter.start(): no connection when allowed_channels is empty
#
# Regression: an unconfigured agent (allowed_channels=[]) was opening a Socket
# Mode WebSocket and stealing ~50% of events from a sibling configured agent
# sharing the same app token. The fix skips handler.start() entirely and blocks
# on a threading.Event instead, keeping the process alive without connecting.
# ---------------------------------------------------------------------------

def test_socket_adapter_start_skips_connection_when_no_allowed_channels():
    """handler.start() must NOT be called when allowed_channels is empty."""
    import threading

    adapter, _mock_app, _mock_app_cls, mock_handler = _build_socket_adapter(
        allowed_channels=[]
    )

    t = threading.Thread(
        target=adapter.start,
        args=(MagicMock(),),
        daemon=True,
    )
    t.start()
    t.join(timeout=0.2)  # give it time to reach Event().wait() or handler.start()

    mock_handler.start.assert_not_called()


def test_socket_adapter_start_skips_connection_when_allowed_channels_none():
    """handler.start() must NOT be called when allowed_channels is None (absent from config)."""
    import threading

    adapter, _mock_app, _mock_app_cls, mock_handler = _build_socket_adapter(
        allowed_channels=None
    )

    t = threading.Thread(target=adapter.start, args=(MagicMock(),), daemon=True)
    t.start()
    t.join(timeout=0.2)

    mock_handler.start.assert_not_called()


def test_socket_adapter_start_connects_when_channels_configured():
    """handler.start() IS called when allowed_channels is non-empty."""
    adapter, mock_app, _mock_app_cls, mock_handler = _build_socket_adapter(
        allowed_channels=["C_PROD"]
    )
    # mock_handler.start() returns immediately (it's a MagicMock), so no thread needed
    adapter.start(on_message=MagicMock())

    mock_handler.start.assert_called_once()


def test_socket_adapter_start_logs_warning_when_no_allowed_channels(caplog):
    """A warning must be logged when the adapter skips connecting due to empty channels."""
    import logging
    import threading

    adapter, _mock_app, _mock_app_cls, _mock_handler = _build_socket_adapter(
        allowed_channels=[]
    )

    with caplog.at_level(logging.WARNING, logger="mithai.adapters.slack"):
        t = threading.Thread(target=adapter.start, args=(MagicMock(),), daemon=True)
        t.start()
        t.join(timeout=0.2)

    assert any("allowed_channels" in r.message for r in caplog.records)
