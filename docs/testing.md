---
title: "Testing your skill"
description: "Unit test handle() and resolve_human() directly without running the agent."
---


Skills are Python modules. `handle()` and `resolve_human()` are plain functions that take a dict and return a string — you can unit test them directly without starting the agent, connecting to Slack, or touching the LLM.

---

## On this page

- [Why test skills](#why-test-skills)
- [The test setup](#the-test-setup)
- [Testing handle()](#testing-handle)
- [Testing resolve_human()](#testing-resolve_human)
- [Mocking HTTP](#mocking-http)
- [Mocking memory](#mocking-memory)
- [Running tests](#running-tests)
- [Testing approval flow end-to-end](#testing-approval-flow-end-to-end)

---

## Why test skills

The agent framework handles routing, LLM interaction, and approval gates. Your skill's job is simpler: receive a tool name and a dict of inputs, do something, and return a JSON string. Because the interface is this narrow, unit tests are fast and reliable.

Testing `handle()` directly lets you:

- Verify correct JSON is returned for each tool
- Check that config values are read and used properly
- Assert that memory writes happen under the right conditions
- Confirm error paths return well-formed error objects

Testing `resolve_human()` directly lets you verify your approval logic without running the full agent loop — important because approval bugs are silent (a missed `"approve"` means a destructive action runs automatically).

---

## The test setup

Every tool handler receives a `ctx` dict built by the engine. In tests, construct it manually:

```python
ctx = {
    "config": {
        "services": {
            "checkout": {"url": "https://checkout.internal/health"},
            "billing":  {"url": "https://billing.internal/health"},
        }
    },
    "memory": None,
    "state":  None,
    "channel_id": "C123",
    "user_id":    "U456",
    "logger":     None,
}
```

The real `ctx` is built by `build_context()` in `src/mithai/core/context.py`. The fields are:

| Key | Type | What it is |
|---|---|---|
| `config` | `dict` | The `skills.config.<skill_name>` block from `config.yaml` |
| `memory` | `MemoryBackend \| None` | Persistent memory store |
| `state` | `StateBackend \| None` | Persistent key-value store |
| `channel_id` | `str` | Channel the message came from |
| `user_id` | `str` | Who sent the message |
| `logger` | `Logger \| None` | Python logger (safe to pass `None` in tests) |

Your skill should always call `ctx.get("memory")` rather than `ctx["memory"]` and check for `None` before using it. Tests where you pass `None` will surface any missing guard.

---

## Testing handle()

The services skill from the tutorial (`skills/services/tools.py`) exports three tools. Here is how to test each one.

### list_services

```python
from skills.services.tools import handle

def test_list_services_returns_configured_services():
    ctx = {
        "config": {
            "services": {
                "checkout": {"url": "https://checkout.internal/health"},
            }
        },
        "memory": None, "state": None, "channel_id": "C1", "user_id": "U1", "logger": None,
    }
    result = json.loads(handle("list_services", {}, ctx))
    assert "checkout" in result["services"]
    assert result["services"]["checkout"]["url"] == "https://checkout.internal/health"


def test_list_services_empty_config():
    ctx = {
        "config": {},
        "memory": None, "state": None, "channel_id": "C1", "user_id": "U1", "logger": None,
    }
    result = json.loads(handle("list_services", {}, ctx))
    assert "message" in result
```

### check_health

Testing `check_health` requires an HTTP call. Use a mock HTTP server (see [Mocking HTTP](#mocking-http)):

```python
from unittest.mock import patch, MagicMock

def test_check_health_healthy_service():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_response):
        result = json.loads(handle(
            "check_health",
            {"service": "checkout", "url": "https://checkout.internal/health"},
            make_ctx(),
        ))

    assert result["healthy"] is True
    assert result["status"] == 200
    assert result["service"] == "checkout"


def test_check_health_unreachable_service():
    import urllib.error
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        result = json.loads(handle(
            "check_health",
            {"service": "auth", "url": "https://auth.internal/health"},
            make_ctx(),
        ))

    assert result["healthy"] is False
    assert "error" in result
```

### restart_service

```python
def test_restart_service_returns_success():
    ctx = make_ctx()
    result = json.loads(handle(
        "restart_service",
        {"service": "auth", "environment": "staging"},
        ctx,
    ))
    assert result["restarted"] is True
    assert result["service"] == "auth"
    assert result["environment"] == "staging"
```

---

## Testing resolve_human()

`resolve_human` takes the same arguments as `handle` but returns a string (`"approve"`, `"confirm"`) or `None`. Test every branch.

```python
from skills.services.tools import resolve_human

def test_production_restart_requires_approval():
    level = resolve_human(
        "restart_service",
        {"service": "auth", "environment": "production"},
        {},
    )
    assert level == "approve"


def test_staging_restart_is_auto_execute():
    level = resolve_human(
        "restart_service",
        {"service": "auth", "environment": "staging"},
        {},
    )
    assert level is None


def test_non_restart_tools_are_auto_execute():
    for tool in ["list_services", "check_health"]:
        level = resolve_human(tool, {}, {})
        assert level is None, f"Expected None for {tool}, got {level!r}"
```

> **Note:** `resolve_human` is called by the engine only when the tool's `"human"` field is set to `"dynamic"`. If you use a static level like `"approve"`, there is no `resolve_human` to test — the approval gate is declarative.

---

## Mocking HTTP

The services skill uses `urllib.request.urlopen`. Patch it at the call site — the module path where it is *used*, not where it is defined.

```python
from unittest.mock import patch, MagicMock

def make_mock_response(status: int):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_check_health_503():
    with patch("skills.services.tools.urllib.request.urlopen",
               return_value=make_mock_response(503)):
        result = json.loads(handle(
            "check_health",
            {"service": "auth", "url": "https://auth.internal/health"},
            make_ctx(),
        ))
    assert result["healthy"] is False
    assert result["status"] == 503
```

If your skill uses the `responses` library instead of stdlib `urllib`, add it as a dev dependency and use its `@responses.activate` decorator:

```python
import responses as resp_mock

@resp_mock.activate
def test_check_health_with_responses_library():
    resp_mock.add(resp_mock.GET, "https://checkout.internal/health", status=200)
    result = json.loads(handle(
        "check_health",
        {"service": "checkout", "url": "https://checkout.internal/health"},
        make_ctx(),
    ))
    assert result["healthy"] is True
```

> **Tip:** Prefer `unittest.mock.patch` for skills that use `urllib` directly — it has no extra dependencies. Use the `responses` library when the skill imports `requests`.

---

## Mocking memory

Pass a real `FilesystemMemoryBackend` pointed at a `tmp_path`, or write a minimal in-memory stub.

### Option 1: FilesystemMemoryBackend on tmp_path (recommended)

```python
import pytest
from mithai.memory.filesystem import FilesystemMemoryBackend

@pytest.fixture
def mem(tmp_path):
    return FilesystemMemoryBackend(tmp_path / "memory")


def test_restart_writes_to_memory(mem):
    ctx = {**make_ctx(), "memory": mem}
    handle("restart_service", {"service": "auth", "environment": "production"}, ctx)
    content = mem.read("restarts.md")
    assert content is not None
    assert "auth" in content
```

### Option 2: In-memory stub

When you want tests that never touch disk:

```python
class InMemoryMemory:
    def __init__(self):
        self._store = {}

    def read(self, path):
        return self._store.get(path)

    def write(self, path, content, *, append=False):
        if append:
            self._store[path] = self._store.get(path, "") + content
        else:
            self._store[path] = content

    def exists(self, path):
        return path in self._store
```

Use it the same way:

```python
def test_restart_appends_to_log():
    mem = InMemoryMemory()
    ctx = {**make_ctx(), "memory": mem}
    handle("restart_service", {"service": "billing", "environment": "staging"}, ctx)
    assert "billing" in (mem.read("restarts.md") or "")
```

---

## Running tests

Place skill tests under `tests/skills/`:

```
tests/
└── skills/
    └── test_services.py
```

Run the full suite:

```bash
uv run pytest tests/ -v
```

Run only skill tests:

```bash
uv run pytest tests/skills/ -v
```

Run a single test:

```bash
uv run pytest tests/skills/test_services.py::test_check_health_healthy_service -v
```

> **Tip:** If your skill imports are failing with `ModuleNotFoundError`, add the project root to `sys.path` in `conftest.py` or run pytest from the root with `uv run pytest`.

---

## Complete test file

Here is a full test file for the services skill, covering all three tools and both approval branches.

**`tests/skills/test_services.py`**

```python
"""Tests for the services skill."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add skills directory to path so the module can be imported directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "skills"))

from services.tools import handle, resolve_human  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def make_ctx(services=None, memory=None):
    return {
        "config": {
            "services": services or {
                "checkout": {"url": "https://checkout.internal/health"},
                "billing":  {"url": "https://billing.internal/health"},
                "auth":     {"url": "https://auth.internal/health"},
            }
        },
        "memory":     memory,
        "state":      None,
        "channel_id": "C123",
        "user_id":    "U456",
        "logger":     None,
    }


def make_mock_response(status: int):
    resp = MagicMock()
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── list_services ─────────────────────────────────────────────────────────────

def test_list_services_returns_all_configured_services():
    result = json.loads(handle("list_services", {}, make_ctx()))
    assert set(result["services"].keys()) == {"checkout", "billing", "auth"}


def test_list_services_empty_config_returns_message():
    result = json.loads(handle("list_services", {}, make_ctx(services={})))
    assert "message" in result
    assert "services" not in result


# ── check_health ──────────────────────────────────────────────────────────────

def test_check_health_healthy_200():
    with patch("services.tools.urllib.request.urlopen",
               return_value=make_mock_response(200)):
        result = json.loads(handle(
            "check_health",
            {"service": "checkout", "url": "https://checkout.internal/health"},
            make_ctx(),
        ))
    assert result["healthy"] is True
    assert result["status"] == 200
    assert result["service"] == "checkout"
    assert "response_ms" in result


def test_check_health_unhealthy_503():
    with patch("services.tools.urllib.request.urlopen",
               return_value=make_mock_response(503)):
        result = json.loads(handle(
            "check_health",
            {"service": "auth", "url": "https://auth.internal/health"},
            make_ctx(),
        ))
    assert result["healthy"] is False
    assert result["status"] == 503


def test_check_health_url_error():
    import urllib.error
    with patch("services.tools.urllib.request.urlopen",
               side_effect=urllib.error.URLError("connection refused")):
        result = json.loads(handle(
            "check_health",
            {"service": "billing", "url": "https://billing.internal/health"},
            make_ctx(),
        ))
    assert result["healthy"] is False
    assert "error" in result


def test_check_health_looks_up_url_from_config():
    """check_health should use ctx config when no URL is provided."""
    with patch("services.tools.urllib.request.urlopen",
               return_value=make_mock_response(200)):
        result = json.loads(handle(
            "check_health",
            {"service": "checkout"},   # no url field
            make_ctx(),
        ))
    assert result["healthy"] is True


# ── restart_service ───────────────────────────────────────────────────────────

def test_restart_service_returns_success():
    result = json.loads(handle(
        "restart_service",
        {"service": "auth", "environment": "staging"},
        make_ctx(),
    ))
    assert result["restarted"] is True
    assert result["service"] == "auth"
    assert result["environment"] == "staging"


def test_restart_service_writes_to_memory(tmp_path):
    from mithai.memory.filesystem import FilesystemMemoryBackend
    mem = FilesystemMemoryBackend(tmp_path / "memory")
    ctx = {**make_ctx(), "memory": mem}
    handle("restart_service", {"service": "auth", "environment": "production"}, ctx)
    content = mem.read("restarts.md")
    assert content is not None
    assert "auth" in content


# ── resolve_human ─────────────────────────────────────────────────────────────

def test_production_restart_requires_approval():
    level = resolve_human(
        "restart_service", {"service": "auth", "environment": "production"}, {}
    )
    assert level == "approve"


def test_staging_restart_is_auto_execute():
    level = resolve_human(
        "restart_service", {"service": "auth", "environment": "staging"}, {}
    )
    assert level is None


def test_read_only_tools_are_auto_execute():
    for tool in ["list_services", "check_health"]:
        assert resolve_human(tool, {}, {}) is None
```

---

## Testing approval flow end-to-end

Unit tests verify your logic. They cannot test the full approval flow — sending a Slack message with Approve/Deny buttons, waiting for a click, and routing the result back to the engine.

For end-to-end approval testing, use `mithai chat`:

```bash
mithai chat
```

`mithai chat` runs the full engine loop in the terminal. When a tool requires approval, it prints the approval prompt and waits for you to type `approve` or `deny`. This exercises `resolve_human`, the Human MCP gate, `handle`, and the session recording path together.

For automated integration tests of the engine, see how `tests/test_human_mcp.py` constructs a minimal `HumanMCP` instance with a mock adapter — the same pattern applies if you need to test approval routing in CI.

---

← [Configuration](configuration.md) | [Deploy to production](deployment.md) →
