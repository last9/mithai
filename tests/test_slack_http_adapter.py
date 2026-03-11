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
    # Capture the handler registered with @app.message("")
    registered_handler = {}

    def capture_decorator(pattern):
        def decorator(fn):
            registered_handler["fn"] = fn
            return fn
        return decorator

    mock_app.message.side_effect = capture_decorator
    adapter._register_message_handlers(on_message)

    # Simulate a message from a disallowed channel
    fake_message = {"channel": "C_OTHER", "text": "hello", "ts": "1234", "user": "U1"}
    registered_handler["fn"](message=fake_message, say=MagicMock())

    on_message.assert_not_called()


def test_allowed_channels_passes_messages_from_allowed_channel():
    """on_message IS called when the channel is in allowed_channels."""
    adapter, mock_app, _ = _build_http_adapter(allowed_channels=["C_ALLOWED"])

    on_message = MagicMock(return_value="reply")
    registered_handler = {}

    def capture_decorator(pattern):
        def decorator(fn):
            registered_handler["fn"] = fn
            return fn
        return decorator

    mock_app.message.side_effect = capture_decorator
    adapter._register_message_handlers(on_message)

    fake_message = {"channel": "C_ALLOWED", "text": "hello", "ts": "1234", "user": "U1"}
    mock_say = MagicMock()
    registered_handler["fn"](message=fake_message, say=mock_say)

    on_message.assert_called_once()


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

def test_run_cmd_unknown_adapter_raises():
    import click
    try:
        from mithai.cli.run_cmd import _create_adapter
        _create_adapter({}, "ftp", adapter_config={})
        assert False, "should have raised"
    except click.ClickException as e:
        assert "ftp" in str(e)
