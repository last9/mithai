"""Tests for the slack skill (slack_get_history and slack_send_message tools)."""

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock


SKILL_DIR = Path(__file__).parent.parent / "skills" / "slack"


def _load_skill_module():
    """Load skills/slack/tools.py fresh (reset module-level _client)."""
    mod_name = "mithai_skill_slack_test"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SKILL_DIR / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_slack_client(messages=None, user_map=None):
    """Build a mock SlackClient."""
    client = MagicMock()
    client.get_history.return_value = (
        messages if messages is not None else ["alice: hello", "bob: deploy going well"],
        user_map if user_map is not None else {"U1": "alice", "U2": "bob"},
    )
    client.post_message.return_value = {"ok": True, "ts": "123.456", "channel": "C99"}
    return client


def _make_adapter_with_client(client):
    """Build a mock adapter that exposes a slack_client property."""
    adapter = MagicMock()
    adapter.slack_client = client
    return adapter


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

def test_tools_exported():
    mod = _load_skill_module()
    assert hasattr(mod, "TOOLS")
    assert len(mod.TOOLS) == 2
    names = [t["name"] for t in mod.TOOLS]
    assert "slack_get_history" in names
    assert "slack_send_message" in names


def test_get_history_has_no_human_field():
    """slack_get_history should auto-execute — no approval gate."""
    mod = _load_skill_module()
    tool = next(t for t in mod.TOOLS if t["name"] == "slack_get_history")
    assert "human" not in tool


def test_send_message_requires_approval():
    """slack_send_message must have human=approve."""
    mod = _load_skill_module()
    tool = next(t for t in mod.TOOLS if t["name"] == "slack_send_message")
    assert tool.get("human") == "approve"


# ---------------------------------------------------------------------------
# bind()
# ---------------------------------------------------------------------------

def test_bind_stores_slack_client():
    mod = _load_skill_module()
    client = _make_slack_client()
    adapter = _make_adapter_with_client(client)
    mod.bind(MagicMock(), adapter)
    assert mod._client is client


def test_bind_ignores_adapter_without_slack_client():
    mod = _load_skill_module()
    assert mod._client is None
    adapter = MagicMock(spec=[])  # No attributes at all
    mod.bind(MagicMock(), adapter)
    assert mod._client is None


def test_bind_ignores_non_slack_adapter():
    mod = _load_skill_module()
    assert mod._client is None
    # MagicMock by default has any attribute, but spec=[] prevents slack_client
    mod.bind(MagicMock(), MagicMock(spec=[]))
    assert mod._client is None


# ---------------------------------------------------------------------------
# handle() — no client
# ---------------------------------------------------------------------------

def test_handle_get_history_returns_error_when_no_client():
    mod = _load_skill_module()
    result = json.loads(mod.handle("slack_get_history", {}, {"channel_id": "C1"}))
    assert "error" in result


def test_handle_send_message_returns_error_when_no_client():
    mod = _load_skill_module()
    result = json.loads(mod.handle("slack_send_message", {"text": "hello"}, {"channel_id": "C1"}))
    assert "error" in result


# ---------------------------------------------------------------------------
# handle() — slack_get_history with client
# ---------------------------------------------------------------------------

def _setup_with_client(messages=None, user_map=None):
    mod = _load_skill_module()
    client = _make_slack_client(messages, user_map)
    adapter = _make_adapter_with_client(client)
    mod.bind(MagicMock(), adapter)
    return mod, client


def test_handle_returns_messages_and_user_map():
    mod, client = _setup_with_client(
        messages=["alice: hi", "bob: hey"],
        user_map={"U1": "alice", "U2": "bob"},
    )
    result = json.loads(mod.handle("slack_get_history", {}, {"channel_id": "C99"}))
    assert result["messages"] == ["alice: hi", "bob: hey"]
    assert result["user_map"] == {"U1": "alice", "U2": "bob"}
    assert result["count"] == 2


def test_handle_uses_ctx_channel_id_when_not_in_input():
    mod, client = _setup_with_client()
    mod.handle("slack_get_history", {}, {"channel_id": "C_CTX"})
    client.get_history.assert_called_once_with("C_CTX", 100)


def test_handle_prefers_input_channel_id_over_ctx():
    mod, client = _setup_with_client()
    mod.handle("slack_get_history", {"channel_id": "C_INPUT"}, {"channel_id": "C_CTX"})
    client.get_history.assert_called_once_with("C_INPUT", 100)


def test_handle_default_limit_is_100():
    mod, client = _setup_with_client()
    mod.handle("slack_get_history", {}, {"channel_id": "C1"})
    _, limit = client.get_history.call_args[0]
    assert limit == 100


def test_handle_custom_limit():
    mod, client = _setup_with_client()
    mod.handle("slack_get_history", {"limit": 50}, {"channel_id": "C1"})
    _, limit = client.get_history.call_args[0]
    assert limit == 50


def test_handle_caps_limit_at_500():
    mod, client = _setup_with_client()
    mod.handle("slack_get_history", {"limit": 9999}, {"channel_id": "C1"})
    _, limit = client.get_history.call_args[0]
    assert limit == 500


def test_handle_returns_error_for_missing_channel_id():
    mod, _ = _setup_with_client()
    result = json.loads(mod.handle("slack_get_history", {}, {}))
    assert "error" in result


def test_handle_unknown_tool():
    mod, _ = _setup_with_client()
    result = json.loads(mod.handle("slack_does_not_exist", {}, {"channel_id": "C1"}))
    assert "error" in result


# ---------------------------------------------------------------------------
# handle() — slack_send_message with client
# ---------------------------------------------------------------------------

def test_handle_get_history_empty_result():
    mod, client = _setup_with_client(messages=[], user_map={})
    result = json.loads(mod.handle("slack_get_history", {}, {"channel_id": "C1"}))
    assert result["messages"] == []
    assert result["user_map"] == {}
    assert result["count"] == 0


def test_send_message_posts_to_channel():
    mod, client = _setup_with_client()
    result = json.loads(mod.handle(
        "slack_send_message",
        {"text": "hello world"},
        {"channel_id": "C99"},
    ))
    client.post_message.assert_called_once_with("C99", "hello world", thread_ts=None)
    assert result["ok"] is True
    assert result["ts"] == "123.456"
    assert result["channel"] == "C99"


def test_send_message_uses_input_channel_id():
    mod, client = _setup_with_client()
    mod.handle("slack_send_message", {"channel_id": "C_INPUT", "text": "hi"}, {"channel_id": "C_CTX"})
    client.post_message.assert_called_once_with("C_INPUT", "hi", thread_ts=None)


def test_send_message_uses_ctx_channel_id_as_fallback():
    mod, client = _setup_with_client()
    mod.handle("slack_send_message", {"text": "hi"}, {"channel_id": "C_CTX"})
    client.post_message.assert_called_once_with("C_CTX", "hi", thread_ts=None)


def test_send_message_passes_thread_ts():
    mod, client = _setup_with_client()
    mod.handle(
        "slack_send_message",
        {"text": "reply", "thread_ts": "111.222"},
        {"channel_id": "C1"},
    )
    client.post_message.assert_called_once_with("C1", "reply", thread_ts="111.222")


def test_send_message_returns_error_for_missing_text():
    mod, _ = _setup_with_client()
    result = json.loads(mod.handle("slack_send_message", {}, {"channel_id": "C1"}))
    assert "error" in result


def test_send_message_returns_error_for_missing_channel_id():
    mod, _ = _setup_with_client()
    result = json.loads(mod.handle("slack_send_message", {"text": "hi"}, {}))
    assert "error" in result
