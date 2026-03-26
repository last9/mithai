"""Tests for the scheduling skill (platform API + local crontab)."""

import json
import http.server
import threading
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_backend():
    """Reset backend to a fresh CrontabBackend between tests."""
    from skills.scheduling.tools import CrontabBackend
    import skills.scheduling.tools as mod
    mod._backend = CrontabBackend()
    yield
    mod._backend = CrontabBackend()


@pytest.fixture
def ctx():
    return {"channel_id": "C123", "user_id": "U456"}


def _handle(name, input, ctx):
    from skills.scheduling.tools import handle
    return json.loads(handle(name, input, ctx))


@pytest.fixture
def mock_server():
    """Minimal HTTP server that mimics /v1/schedules."""
    schedules = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/v1/schedules":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(schedules).encode())
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/v1/schedules":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                entry = {"id": "sched-1", "name": body["name"], "cron": body["cron"],
                         "paused": False, "payload": body.get("payload", {})}
                schedules.append(entry)
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(entry).encode())
            else:
                self.send_error(404)

        def do_DELETE(self):
            if self.path.startswith("/v1/schedules/"):
                sid = self.path.split("/")[-1]
                before = len(schedules)
                schedules[:] = [s for s in schedules if s["id"] != sid]
                if len(schedules) < before:
                    self.send_response(204)
                    self.end_headers()
                else:
                    self.send_error(404)
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", schedules
    server.shutdown()


# ---------------------------------------------------------------------------
# Shared validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_input(self):
        from skills.scheduling.tools import _validate_create_input
        assert _validate_create_input({"label": "eod-summary", "cron_expression": "0 17 * * 1-5"}) is None

    def test_invalid_label_spaces(self):
        from skills.scheduling.tools import _validate_create_input
        err = _validate_create_input({"label": "bad label", "cron_expression": "0 17 * * *"})
        assert err is not None
        assert "letters, numbers" in err

    def test_invalid_label_slashes(self):
        from skills.scheduling.tools import _validate_create_input
        err = _validate_create_input({"label": "../etc", "cron_expression": "0 17 * * *"})
        assert err is not None

    def test_invalid_cron_too_few_fields(self):
        from skills.scheduling.tools import _validate_create_input
        err = _validate_create_input({"label": "ok", "cron_expression": "0 17 *"})
        assert err is not None
        assert "5 fields" in err

    def test_invalid_cron_too_many_fields(self):
        from skills.scheduling.tools import _validate_create_input
        err = _validate_create_input({"label": "ok", "cron_expression": "0 17 * * * *"})
        assert err is not None


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------


class TestStartup:
    def test_agent_cloud_platform_backend_when_configured(self):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import startup, AgentCloudPlatformBackend
        startup({"backend": "agent_cloud_platform", "scheduling_backend_url": "http://localhost:8081", "scheduling_backend_token": "tok_123"})
        assert isinstance(mod._backend, AgentCloudPlatformBackend)

    def test_crontab_backend_by_default(self):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import startup, CrontabBackend
        startup({})
        assert isinstance(mod._backend, CrontabBackend)

    def test_crontab_fallback_when_platform_url_missing(self):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import startup, CrontabBackend
        startup({"backend": "agent_cloud_platform", "scheduling_backend_token": "tok_123"})
        assert isinstance(mod._backend, CrontabBackend)

    def test_crontab_fallback_when_platform_token_missing(self):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import startup, CrontabBackend
        startup({"backend": "agent_cloud_platform", "scheduling_backend_url": "http://localhost:8081"})
        assert isinstance(mod._backend, CrontabBackend)

    def test_startup_via_config_wires_backend(self, mock_server, ctx):
        """startup() with agent_cloud_platform config routes handle() to the real HTTP server."""
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import startup, AgentCloudPlatformBackend
        url, _ = mock_server
        startup({"backend": "agent_cloud_platform", "scheduling_backend_url": url, "scheduling_backend_token": "tok"})
        assert isinstance(mod._backend, AgentCloudPlatformBackend)
        assert _handle("list_schedules", {}, ctx)["schedules"] == []


# ---------------------------------------------------------------------------
# Agent Cloud Platform backend
# ---------------------------------------------------------------------------


class TestAgentCloudPlatformBackend:
    """Test AgentCloudPlatformBackend with a mock HTTP server."""

    def test_list_empty(self, mock_server, ctx):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import AgentCloudPlatformBackend
        url, _ = mock_server
        mod._backend = AgentCloudPlatformBackend(url, "test-token")
        assert _handle("list_schedules", {}, ctx)["schedules"] == []

    def test_create_and_list(self, mock_server, ctx):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import AgentCloudPlatformBackend
        url, _ = mock_server
        mod._backend = AgentCloudPlatformBackend(url, "test-token")

        result = _handle("create_schedule", {
            "cron_expression": "0 17 * * 1-5",
            "task_text": "EOD summary",
            "label": "eod-summary",
        }, ctx)
        assert result["created"] is True
        assert result["label"] == "eod-summary"

        listed = _handle("list_schedules", {}, ctx)
        assert len(listed["schedules"]) == 1
        assert listed["schedules"][0]["label"] == "eod-summary"

    def test_create_and_delete(self, mock_server, ctx):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import AgentCloudPlatformBackend
        url, _ = mock_server
        mod._backend = AgentCloudPlatformBackend(url, "test-token")

        _handle("create_schedule", {
            "cron_expression": "30 9 * * *",
            "task_text": "health check",
            "label": "health-check",
        }, ctx)

        assert _handle("delete_schedule", {"label": "health-check"}, ctx)["deleted"] is True
        assert _handle("list_schedules", {}, ctx)["schedules"] == []

    def test_delete_nonexistent(self, mock_server, ctx):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import AgentCloudPlatformBackend
        url, _ = mock_server
        mod._backend = AgentCloudPlatformBackend(url, "test-token")
        assert "error" in _handle("delete_schedule", {"label": "nope"}, ctx)

    def test_create_invalid_label(self, mock_server, ctx):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import AgentCloudPlatformBackend
        url, _ = mock_server
        mod._backend = AgentCloudPlatformBackend(url, "test-token")
        assert "error" in _handle("create_schedule", {
            "cron_expression": "0 17 * * *",
            "task_text": "test",
            "label": "bad label!",
        }, ctx)

    def test_api_unreachable(self, ctx):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import AgentCloudPlatformBackend
        mod._backend = AgentCloudPlatformBackend("http://127.0.0.1:1", "tok")
        result = _handle("list_schedules", {}, ctx)
        assert "error" in result
        assert "unreachable" in result["error"].lower() or "refused" in result["error"].lower()


# ---------------------------------------------------------------------------
# Local crontab backend
# ---------------------------------------------------------------------------


class TestCrontabBackend:
    def test_list_empty(self, ctx):
        from skills.scheduling.tools import CrontabBackend
        backend = CrontabBackend()
        with patch.object(backend, "_get_crontab", return_value=""):
            result = json.loads(backend.list(ctx))
            assert result["schedules"] == []

    def test_parse_entries(self):
        from skills.scheduling.tools import CrontabBackend
        backend = CrontabBackend()
        crontab = "0 17 * * 1-5 bash -c 'curl ...' # mithai:eod-summary\n"
        entries = backend._parse_entries(crontab)
        assert len(entries) == 1
        assert entries[0]["label"] == "eod-summary"
        assert entries[0]["cron_expression"] == "0 17 * * 1-5"

    def test_parse_ignores_non_mithai_entries(self):
        from skills.scheduling.tools import CrontabBackend
        backend = CrontabBackend()
        crontab = "0 * * * * /usr/bin/some-other-job\n0 9 * * * bash # mithai:daily\n"
        entries = backend._parse_entries(crontab)
        assert len(entries) == 1
        assert entries[0]["label"] == "daily"

    def test_list_with_entries(self, ctx):
        from skills.scheduling.tools import CrontabBackend
        backend = CrontabBackend()
        crontab = "0 17 * * 1-5 bash -c 'curl ...' # mithai:eod-summary\n"
        with patch.object(backend, "_get_crontab", return_value=crontab):
            result = json.loads(backend.list(ctx))
            assert len(result["schedules"]) == 1
            assert result["schedules"][0]["label"] == "eod-summary"

    def test_create_success(self, monkeypatch, ctx, tmp_path):
        import skills.scheduling.tools as mod
        from skills.scheduling.tools import CrontabBackend
        monkeypatch.setenv("BOT_USER_ID", "U999")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setattr(mod, "_PAYLOAD_DIR", str(tmp_path))
        backend = CrontabBackend()
        written = {}
        with (
            patch.object(backend, "_get_crontab", return_value=""),
            patch.object(backend, "_set_crontab", side_effect=lambda c: written.update({"content": c}) or {"ok": True}),
            patch.object(backend, "_ensure_token_file", return_value=None),
        ):
            result = json.loads(backend.create({
                "cron_expression": "0 17 * * 1-5",
                "task_text": "EOD summary",
                "label": "eod-summary",
            }, ctx))
        assert result["created"] is True
        assert result["label"] == "eod-summary"
        assert "eod-summary" in written["content"]

    def test_create_missing_bot_user_id(self, monkeypatch, ctx):
        from skills.scheduling.tools import CrontabBackend
        monkeypatch.delenv("BOT_USER_ID", raising=False)
        backend = CrontabBackend()
        result = json.loads(backend.create({
            "cron_expression": "0 9 * * *",
            "task_text": "check",
            "label": "daily-check",
        }, ctx))
        assert "error" in result
        assert "BOT_USER_ID" in result["error"]

    def test_create_duplicate_label(self, monkeypatch, ctx):
        from skills.scheduling.tools import CrontabBackend
        monkeypatch.setenv("BOT_USER_ID", "U999")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        backend = CrontabBackend()
        existing = "0 17 * * * bash # mithai:eod-summary\n"
        with (
            patch.object(backend, "_get_crontab", return_value=existing),
            patch.object(backend, "_ensure_token_file", return_value=None),
        ):
            result = json.loads(backend.create({
                "cron_expression": "0 17 * * *",
                "task_text": "EOD",
                "label": "eod-summary",
            }, ctx))
        assert "error" in result
        assert "already exists" in result["error"]

    def test_delete_success(self, ctx):
        from skills.scheduling.tools import CrontabBackend
        backend = CrontabBackend()
        crontab = "0 17 * * * bash # mithai:eod-summary\n"
        written = {}
        with (
            patch.object(backend, "_get_crontab", return_value=crontab),
            patch.object(backend, "_set_crontab", side_effect=lambda c: written.update({"content": c}) or {"ok": True}),
            patch("os.remove"),
        ):
            result = json.loads(backend.delete({"label": "eod-summary"}, ctx))
        assert result["deleted"] is True
        assert "eod-summary" not in written["content"]

    def test_delete_nonexistent(self, ctx):
        from skills.scheduling.tools import CrontabBackend
        backend = CrontabBackend()
        with patch.object(backend, "_get_crontab", return_value=""):
            result = json.loads(backend.delete({"label": "nope"}, ctx))
        assert "error" in result

    def test_set_crontab_failure(self, ctx):
        from skills.scheduling.tools import CrontabBackend
        backend = CrontabBackend()
        crontab = "0 17 * * * bash # mithai:eod-summary\n"
        with (
            patch.object(backend, "_get_crontab", return_value=crontab),
            patch.object(backend, "_set_crontab", return_value={"error": "crontab: permission denied"}),
            patch("os.remove"),
        ):
            result = json.loads(backend.delete({"label": "eod-summary"}, ctx))
        assert "error" in result

    def test_unknown_tool(self, ctx):
        result = _handle("nonexistent", {}, ctx)
        assert "error" in result
        assert "Unknown tool" in result["error"]
