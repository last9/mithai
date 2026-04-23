"""Tests for APIAdapter and the /api/trigger endpoint."""

import threading
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from mithai.adapters.api import APIAdapter
from mithai.adapters.base import OutgoingMessage
from mithai.human.mcp import HumanRequest


# ---------------------------------------------------------------------------
# APIAdapter
# ---------------------------------------------------------------------------


class TestAPIAdapter:
    def test_stop_unblocks_start(self):
        adapter = APIAdapter()
        started = threading.Event()

        def _run():
            started.set()
            adapter.start(on_message=MagicMock())

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        started.wait(timeout=1)
        adapter.stop()
        t.join(timeout=2)
        assert not t.is_alive()

    def test_send_logs_full_message(self, caplog):
        import logging
        adapter = APIAdapter()
        msg = OutgoingMessage(text="hello", channel_id="webhook")
        with caplog.at_level(logging.INFO, logger="mithai.adapters.api"):
            adapter.send(msg)
        assert "hello" in caplog.text
        assert "…" not in caplog.text

    def test_send_truncates_long_message_with_ellipsis(self, caplog):
        import logging
        adapter = APIAdapter()
        long_text = "x" * 300
        msg = OutgoingMessage(text=long_text, channel_id="webhook")
        with caplog.at_level(logging.INFO, logger="mithai.adapters.api"):
            adapter.send(msg)
        assert "…" in caplog.text
        assert "x" * 200 in caplog.text
        assert "x" * 201 not in caplog.text

    def test_request_human_approval_auto_denies(self, caplog):
        import logging
        adapter = APIAdapter()
        req = HumanRequest(
            tool_name="shell__run",
            tool_input={"command": "rm -rf /"},
            description="Delete everything",
            level="approve",
        )
        with caplog.at_level(logging.WARNING, logger="mithai.adapters.api"):
            result = adapter.request_human_approval(req, channel_id="webhook")
        assert result is False
        assert "auto-denying" in caplog.text


# ---------------------------------------------------------------------------
# /api/trigger endpoint
# ---------------------------------------------------------------------------


def _make_app(engine=None, adapter=None, auth_token=""):
    from mithai.ui.app import create_app
    config = {
        "ui": {"auth_token": auth_token},
        "bot": {"name": "test"},
        "llm": {"provider": "anthropic", "model": "test", "max_tokens": 64,
                "anthropic": {"api_key": "test"}},
    }
    return create_app(config, engine=engine, adapter=adapter)


class TestApiTriggerEndpoint:
    def test_returns_202_accepted(self):
        engine = MagicMock()
        engine.handle.return_value = "pong"
        adapter = MagicMock()
        app = _make_app(engine=engine, adapter=adapter)
        client = TestClient(app, raise_server_exceptions=True)

        resp = client.post("/api/trigger", json={"message": "ping", "channel_id": "webhook"})

        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"
        assert resp.json()["channel_id"] == "webhook"

    def test_channel_id_defaults_to_trigger(self):
        engine = MagicMock()
        engine.handle.return_value = "ok"
        app = _make_app(engine=engine, adapter=MagicMock())
        client = TestClient(app)

        resp = client.post("/api/trigger", json={"message": "hi"})

        assert resp.status_code == 202
        assert resp.json()["channel_id"] == "trigger"

    def test_missing_message_returns_400(self):
        app = _make_app(engine=MagicMock(), adapter=MagicMock())
        client = TestClient(app)

        resp = client.post("/api/trigger", json={"channel_id": "webhook"})

        assert resp.status_code == 400
        assert "message" in resp.json()["error"]

    def test_invalid_json_returns_400(self):
        app = _make_app(engine=MagicMock(), adapter=MagicMock())
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/api/trigger", content=b"not-json",
                           headers={"Content-Type": "application/json"})

        assert resp.status_code == 400

    def test_no_engine_returns_503(self):
        app = _make_app(engine=None, adapter=None)
        client = TestClient(app)

        resp = client.post("/api/trigger", json={"message": "ping"})

        assert resp.status_code == 503

    def test_auth_required_when_configured(self):
        app = _make_app(engine=MagicMock(), adapter=MagicMock(), auth_token="secret")
        client = TestClient(app)

        resp = client.post("/api/trigger", json={"message": "ping"})

        assert resp.status_code == 401

    def test_auth_bearer_token_accepted(self):
        engine = MagicMock()
        engine.handle.return_value = "ok"
        app = _make_app(engine=engine, adapter=MagicMock(), auth_token="secret")
        client = TestClient(app)

        resp = client.post(
            "/api/trigger",
            json={"message": "ping"},
            headers={"Authorization": "Bearer secret"},
        )

        assert resp.status_code == 202

    def test_engine_handle_called_with_correct_message(self):
        from mithai.adapters.base import IncomingMessage

        engine = MagicMock()
        engine.handle.return_value = "ok"
        adapter = MagicMock()
        app = _make_app(engine=engine, adapter=adapter)
        client = TestClient(app, raise_server_exceptions=True)

        client.post("/api/trigger", json={
            "message": "deploy app",
            "channel_id": "webhook",
            "user_id": "webhook:github",
        })

        # BackgroundTask runs synchronously in TestClient
        engine.handle.assert_called_once()
        msg_arg = engine.handle.call_args[0][0]
        assert isinstance(msg_arg, IncomingMessage)
        assert msg_arg.text == "deploy app"
        assert msg_arg.channel_id == "webhook"
        assert msg_arg.user_id == "webhook:github"
        assert msg_arg.platform == "trigger"
