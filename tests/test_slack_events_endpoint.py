"""Tests for the embedded API server's POST /slack/events endpoint.

A distributed Slack app routes every workspace's events to a single control-plane
URL. The control plane verifies the signature, routes by team_id, and forwards the
raw Slack request (body + X-Slack-Signature/-Request-Timestamp headers) to the
right agent engine's embedded API server at POST /slack/events. This endpoint
delegates to the managed Slack adapter's Bolt request handler so the existing
pipeline (verify → dedup → channel filter → engine.handle → reply via the
workspace bot token) runs unchanged.
"""

from unittest.mock import MagicMock

from starlette.responses import JSONResponse
from starlette.testclient import TestClient


def _make_app(engine=None, adapter=None, auth_token=""):
    from mithai.ui.app import create_app
    config = {
        "ui": {"auth_token": auth_token},
        "bot": {"name": "test"},
        "llm": {"provider": "anthropic", "model": "test", "max_tokens": 64,
                "anthropic": {"api_key": "test"}},
    }
    return create_app(config, engine=engine, adapter=adapter)


def _slack_adapter_stub(response: JSONResponse | None = None):
    """A stub adapter exposing an async handle_event, like the managed SlackHTTPAdapter."""
    adapter = MagicMock()
    resp = response if response is not None else JSONResponse({"ok": True})

    async def _handle_event(request):
        adapter._last_request = request
        return resp

    adapter.handle_event = MagicMock(side_effect=_handle_event)
    return adapter


class TestSlackEventsEndpoint:
    def test_delegates_to_adapter_handle_event(self):
        adapter = _slack_adapter_stub(JSONResponse({"challenge": "abc"}))
        app = _make_app(engine=MagicMock(), adapter=adapter)
        client = TestClient(app)

        resp = client.post("/slack/events", json={"type": "event_callback"})

        assert resp.status_code == 200
        assert resp.json() == {"challenge": "abc"}
        adapter.handle_event.assert_called_once()

    def test_returns_503_when_adapter_is_not_a_slack_adapter(self):
        # A non-Slack adapter (e.g. CLI/API) has no handle_event method.
        adapter = MagicMock(spec=[])  # no attributes
        app = _make_app(engine=MagicMock(), adapter=adapter)
        client = TestClient(app)

        resp = client.post("/slack/events", json={"type": "event_callback"})

        assert resp.status_code == 503

    def test_returns_503_when_no_adapter(self):
        app = _make_app(engine=MagicMock(), adapter=None)
        client = TestClient(app)

        resp = client.post("/slack/events", json={"type": "event_callback"})

        assert resp.status_code == 503

    def test_requires_auth_when_token_configured(self):
        adapter = _slack_adapter_stub()
        app = _make_app(engine=MagicMock(), adapter=adapter, auth_token="secret-token")
        client = TestClient(app)

        resp = client.post("/slack/events", json={"type": "event_callback"})

        assert resp.status_code == 401
        adapter.handle_event.assert_not_called()

    def test_passes_with_bearer_token(self):
        adapter = _slack_adapter_stub()
        app = _make_app(engine=MagicMock(), adapter=adapter, auth_token="secret-token")
        client = TestClient(app)

        resp = client.post(
            "/slack/events",
            json={"type": "event_callback"},
            headers={"Authorization": "Bearer secret-token"},
        )

        assert resp.status_code == 200
        adapter.handle_event.assert_called_once()

    def test_get_not_allowed(self):
        adapter = _slack_adapter_stub()
        app = _make_app(engine=MagicMock(), adapter=adapter)
        client = TestClient(app)

        resp = client.get("/slack/events")

        assert resp.status_code == 405


def test_embedded_api_refuses_when_managed_and_token_unset(monkeypatch):
    """Managed adapter + empty/unresolved MITHAI_UI_TOKEN must NOT start the
    embedded API (it would be unauthenticated). Mirrors the signing-secret guard."""
    import mithai.cli.run_cmd as rc

    created = {"n": 0}
    monkeypatch.setattr("mithai.ui.app.create_app",
                        lambda *a, **k: created.__setitem__("n", created["n"] + 1) or object())
    monkeypatch.setenv("MITHAI_UI_PORT", "8421")

    class _Managed:
        _managed = True

    for tok in ("", "${MITHAI_UI_TOKEN}"):
        monkeypatch.setenv("MITHAI_UI_TOKEN", tok)
        rc._maybe_start_embedded_api({"ui": {}}, object(), _Managed())
    assert created["n"] == 0, "embedded API must not start without a real token in managed mode"
