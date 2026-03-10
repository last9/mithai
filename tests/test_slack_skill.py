"""Tests for the slack skill (slack_get_history tool)."""

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


SKILL_DIR = Path(__file__).parent.parent / "skills" / "slack"


def _load_skill_module():
    """Load skills/slack/tools.py fresh (reset module-level _adapter)."""
    mod_name = "mithai_skill_slack_test"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SKILL_DIR / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_slack_adapter(messages=None, user_map=None):
    """Build a mock SlackAdapterBase."""
    adapter = MagicMock()
    adapter._fetch_channel_history.return_value = (
        messages or ["alice: hello", "bob: deploy going well"],
        user_map or {"U1": "alice", "U2": "bob"},
    )
    return adapter


# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

def test_tools_exported():
    mod = _load_skill_module()
    assert hasattr(mod, "TOOLS")
    assert len(mod.TOOLS) == 1
    assert mod.TOOLS[0]["name"] == "slack_get_history"


def test_tool_has_no_human_field():
    """slack_get_history should auto-execute — no approval gate."""
    mod = _load_skill_module()
    assert "human" not in mod.TOOLS[0]


# ---------------------------------------------------------------------------
# bind()
# ---------------------------------------------------------------------------

def test_bind_stores_slack_adapter():
    mod = _load_skill_module()
    mock_adapter = MagicMock()

    class FakeSlackBase:
        pass

    with patch("mithai.adapters.slack.SlackAdapterBase", FakeSlackBase):
        mock_adapter.__class__ = FakeSlackBase
        mod.bind(MagicMock(), mock_adapter)

    assert mod._adapter is mock_adapter


def test_bind_ignores_non_slack_adapter():
    mod = _load_skill_module()
    assert mod._adapter is None
    mod.bind(MagicMock(), MagicMock())  # non-Slack adapter
    assert mod._adapter is None


# ---------------------------------------------------------------------------
# handle() — no adapter
# ---------------------------------------------------------------------------

def test_handle_returns_error_when_no_adapter():
    mod = _load_skill_module()
    # _adapter is None (not bound)
    result = json.loads(mod.handle("slack_get_history", {}, {"channel_id": "C1"}))
    assert "error" in result


# ---------------------------------------------------------------------------
# handle() — with adapter
# ---------------------------------------------------------------------------

def _setup_with_adapter(messages=None, user_map=None):
    mod = _load_skill_module()
    adapter = _make_slack_adapter(messages, user_map)

    class FakeSlackBase:
        pass

    with patch("mithai.adapters.slack.SlackAdapterBase", FakeSlackBase):
        adapter.__class__ = FakeSlackBase
        mod.bind(MagicMock(), adapter)
    return mod, adapter


def test_handle_returns_messages_and_user_map():
    mod, adapter = _setup_with_adapter(
        messages=["alice: hi", "bob: hey"],
        user_map={"U1": "alice", "U2": "bob"},
    )
    result = json.loads(mod.handle("slack_get_history", {}, {"channel_id": "C99"}))
    assert result["messages"] == ["alice: hi", "bob: hey"]
    assert result["user_map"] == {"U1": "alice", "U2": "bob"}
    assert result["count"] == 2


def test_handle_uses_ctx_channel_id_when_not_in_input():
    mod, adapter = _setup_with_adapter()
    mod.handle("slack_get_history", {}, {"channel_id": "C_CTX"})
    adapter._fetch_channel_history.assert_called_once_with("C_CTX", 100)


def test_handle_prefers_input_channel_id_over_ctx():
    mod, adapter = _setup_with_adapter()
    mod.handle("slack_get_history", {"channel_id": "C_INPUT"}, {"channel_id": "C_CTX"})
    adapter._fetch_channel_history.assert_called_once_with("C_INPUT", 100)


def test_handle_default_limit_is_100():
    mod, adapter = _setup_with_adapter()
    mod.handle("slack_get_history", {}, {"channel_id": "C1"})
    _, limit = adapter._fetch_channel_history.call_args[0]
    assert limit == 100


def test_handle_custom_limit():
    mod, adapter = _setup_with_adapter()
    mod.handle("slack_get_history", {"limit": 50}, {"channel_id": "C1"})
    _, limit = adapter._fetch_channel_history.call_args[0]
    assert limit == 50


def test_handle_caps_limit_at_500():
    mod, adapter = _setup_with_adapter()
    mod.handle("slack_get_history", {"limit": 9999}, {"channel_id": "C1"})
    _, limit = adapter._fetch_channel_history.call_args[0]
    assert limit == 500


def test_handle_returns_error_for_missing_channel_id():
    mod, _ = _setup_with_adapter()
    result = json.loads(mod.handle("slack_get_history", {}, {}))
    assert "error" in result


def test_handle_unknown_tool():
    mod, _ = _setup_with_adapter()
    result = json.loads(mod.handle("slack_does_not_exist", {}, {"channel_id": "C1"}))
    assert "error" in result
