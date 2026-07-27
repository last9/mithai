"""Tests for Control Room web app routes."""


import pytest
from starlette.testclient import TestClient

from mithai.memory.filesystem import FilesystemMemoryBackend
from mithai.state.filesystem import FilesystemStateBackend
from mithai.ui.app import create_app


@pytest.fixture
def state_dir(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return d


@pytest.fixture
def memory_dir(tmp_path):
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture
def skills_dir(tmp_path):
    sd = tmp_path / "skills" / "parrot"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("You are a parrot.")
    (sd / "tools.py").write_text(
        'TOOLS = [{"name": "echo", "description": "Echo back", '
        '"input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}]\n'
        'def handle(name, input, ctx): return input.get("text", "")\n'
    )
    return tmp_path / "skills"


@pytest.fixture
def config(state_dir, memory_dir, skills_dir):
    return {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic"},
        "skills": {"paths": [str(skills_dir)]},
        "learning": {
            "approval_auto_promote": 3,
            "memory": {"backend": "filesystem", "filesystem": {"path": str(memory_dir)}},
        },
        "state": {"backend": "filesystem", "filesystem": {"path": str(state_dir)}},
    }


@pytest.fixture
def state(state_dir):
    return FilesystemStateBackend(str(state_dir))


@pytest.fixture
def memory(memory_dir):
    return FilesystemMemoryBackend(memory_dir)


@pytest.fixture
def client(config):
    app = create_app(config)
    return TestClient(app)


@pytest.fixture
def seeded_client(config, state, memory):
    """Client with some pre-seeded data."""
    state.set("sessions", "slack:C1", {
        "session_id": "slack:C1",
        "platform": "slack",
        "channel_id": "C1",
        "created_at": "2026-03-01T10:00:00+00:00",
        "updated_at": "2026-03-01T10:05:00+00:00",
        "turns": [
            {
                "timestamp": "2026-03-01T10:00:00+00:00",
                "user_id": "U123",
                "user_message": "check disk usage",
                "tool_calls": [
                    {"tool": "shell__run_command", "input": {"command": "df -h"}, "approved": True, "result_summary": "Filesystem Size Used"},
                ],
                "assistant_response": "Here is the disk usage.",
            },
        ],
    })
    memory.write("MEMORY.md", "# Knowledge\n- DaemonSets use rollout restart")
    memory.write_json("approvals.json", {
        "shell__run_command": {
            "df -h": {"approved": 5, "denied": 0},
            "rm -rf /": {"approved": 0, "denied": 2},
        },
    })
    app = create_app(config)
    return TestClient(app)


class TestDashboard:
    def test_dashboard_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Dashboard" in resp.text

    def test_dashboard_shows_stats(self, seeded_client):
        resp = seeded_client.get("/")
        assert resp.status_code == 200
        assert "slack" in resp.text


class TestSessions:
    def test_sessions_empty(self, client):
        resp = client.get("/sessions")
        assert resp.status_code == 200
        assert "No sessions found" in resp.text

    def test_sessions_with_data(self, seeded_client):
        resp = seeded_client.get("/sessions")
        assert resp.status_code == 200
        assert "slack:C1" in resp.text

    def test_session_detail(self, seeded_client):
        resp = seeded_client.get("/sessions/slack:C1")
        assert resp.status_code == 200
        assert "check disk usage" in resp.text
        assert "shell__run_command" in resp.text
        assert "approved" in resp.text

    def test_session_not_found(self, client):
        resp = client.get("/sessions/nonexistent")
        assert resp.status_code == 404

    def test_session_search(self, seeded_client):
        resp = seeded_client.get("/sessions?q=disk")
        assert resp.status_code == 200
        assert "disk" in resp.text


class TestApprovals:
    def test_approvals_empty(self, client):
        resp = client.get("/approvals")
        assert resp.status_code == 200
        assert "No approval data" in resp.text

    def test_approvals_with_data(self, seeded_client):
        resp = seeded_client.get("/approvals")
        assert resp.status_code == 200
        assert "df -h" in resp.text
        assert "status-dot-green" in resp.text  # auto-promoted indicator
        assert "status-dot-red" in resp.text    # denied indicator


class TestMemory:
    def test_memory_empty(self, client):
        resp = client.get("/memory")
        assert resp.status_code == 200

    def test_memory_with_files(self, seeded_client):
        resp = seeded_client.get("/memory")
        assert resp.status_code == 200
        assert "MEMORY.md" in resp.text

    def test_memory_file_view(self, seeded_client):
        resp = seeded_client.get("/memory/MEMORY.md")
        assert resp.status_code == 200
        assert "DaemonSets" in resp.text

    def test_memory_file_not_found(self, client):
        resp = client.get("/memory/nonexistent.md")
        assert resp.status_code == 404

    def test_memory_search(self, seeded_client):
        resp = seeded_client.get("/memory?q=DaemonSet")
        assert resp.status_code == 200
        assert "DaemonSet" in resp.text


class TestSkills:
    def test_skills_page(self, client):
        resp = client.get("/skills")
        assert resp.status_code == 200
        assert "parrot" in resp.text
        assert "echo" in resp.text


class TestConfig:
    def test_config_page(self, client):
        resp = client.get("/config")
        assert resp.status_code == 200
        assert "config.yaml" in resp.text


class TestJSONAPI:
    def test_api_sessions(self, seeded_client):
        resp = seeded_client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["session_id"] == "slack:C1"

    def test_api_session_detail(self, seeded_client):
        resp = seeded_client.get("/api/sessions/slack:C1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "slack:C1"
        assert len(data["turns"]) == 1

    def test_api_session_not_found(self, client):
        resp = client.get("/api/sessions/nope")
        assert resp.status_code == 404

    def test_api_approvals(self, seeded_client):
        resp = seeded_client.get("/api/approvals")
        assert resp.status_code == 200
        data = resp.json()
        assert "approvals" in data
        assert "stats" in data
        assert data["stats"]["total_approved"] == 5

    def test_api_memory(self, seeded_client):
        resp = seeded_client.get("/api/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert "MEMORY.md" in data["files"]

    def test_api_memory_file(self, seeded_client):
        resp = seeded_client.get("/api/memory/MEMORY.md")
        assert resp.status_code == 200
        data = resp.json()
        assert "DaemonSets" in data["content"]

    def test_api_memory_file_create_and_edit(self, client):
        # PUT creates a new file (the dashboard sends raw text), then edits it.
        resp = client.put("/api/memory/notes/new.md", content="# Hello\nfirst")
        assert resp.status_code == 200, resp.text
        assert client.get("/api/memory/notes/new.md").json()["content"] == "# Hello\nfirst"
        resp = client.put("/api/memory/notes/new.md", content="# Hello\nedited")
        assert resp.status_code == 200
        assert client.get("/api/memory/notes/new.md").json()["content"] == "# Hello\nedited"

    def test_api_memory_file_delete(self, client):
        client.put("/api/memory/scratch.md", content="temp")
        assert client.delete("/api/memory/scratch.md").status_code == 200
        assert client.get("/api/memory/scratch.md").status_code == 404
        # Deleting a missing file → 404.
        assert client.delete("/api/memory/scratch.md").status_code == 404

    def test_api_skills(self, client):
        resp = client.get("/api/skills")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        parrot = [s for s in data if s["name"] == "parrot"]
        assert len(parrot) == 1

    def test_api_config(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200

    def test_api_stats(self, seeded_client):
        resp = seeded_client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "approvals" in data


class TestAuth:
    def test_auth_required_when_configured(self, config):
        config["ui"] = {"auth_token": "my-secret-token"}
        app = create_app(config)
        client = TestClient(app)

        resp = client.get("/")
        assert resp.status_code == 401

    def test_auth_via_query_param_sets_cookie_and_redirects(self, config):
        config["ui"] = {"auth_token": "my-secret-token"}
        app = create_app(config)
        client = TestClient(app, follow_redirects=False)

        resp = client.get("/?token=my-secret-token")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"
        assert "mithai_session" in resp.cookies

    def test_auth_via_query_param_preserves_other_params(self, config):
        config["ui"] = {"auth_token": "my-secret-token"}
        app = create_app(config)
        client = TestClient(app, follow_redirects=False)

        resp = client.get("/sessions?token=my-secret-token&q=hello")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/sessions?q=hello"

    def test_auth_via_cookie(self, config):
        config["ui"] = {"auth_token": "my-secret-token"}
        app = create_app(config)
        client = TestClient(app)
        client.cookies.set("mithai_session", "my-secret-token")

        resp = client.get("/")
        assert resp.status_code == 200

    def test_auth_via_cookie_wrong_value(self, config):
        config["ui"] = {"auth_token": "my-secret-token"}
        app = create_app(config)
        client = TestClient(app)
        client.cookies.set("mithai_session", "wrong")

        resp = client.get("/")
        assert resp.status_code == 401

    def test_auth_via_bearer_header(self, config):
        config["ui"] = {"auth_token": "my-secret-token"}
        app = create_app(config)
        client = TestClient(app)

        resp = client.get("/", headers={"Authorization": "Bearer my-secret-token"})
        assert resp.status_code == 200

    def test_auth_wrong_token(self, config):
        config["ui"] = {"auth_token": "my-secret-token"}
        app = create_app(config)
        client = TestClient(app)

        resp = client.get("/?token=wrong")
        assert resp.status_code == 401

    def test_no_auth_when_unresolved_env_var(self, config):
        config["ui"] = {"auth_token": "${MITHAI_UI_TOKEN}"}
        app = create_app(config)
        client = TestClient(app)

        resp = client.get("/")
        assert resp.status_code == 200
