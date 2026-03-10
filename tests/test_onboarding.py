"""Tests for Slack channel onboarding."""

import re
from unittest.mock import MagicMock, patch

import pytest

from mithai.adapters.base import IncomingMessage
from mithai.core.config import get_agent_config
from mithai.memory.filesystem import FilesystemMemoryBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(config: dict, memory_backend=None, llm=None):
    """Create an Engine with minimal dependencies (no real LLM)."""
    from mithai.core.engine import Engine
    from mithai.state.memory import MemoryStateBackend

    if llm is None:
        llm = MagicMock()
        # Default: return a plain text response with no tool calls
        resp = MagicMock()
        resp.content = [{"type": "text", "text": "Hello, I am engineer9!"}]
        resp.stop_reason = "end_turn"
        llm.create_message.return_value = resp

    state = MemoryStateBackend()
    if memory_backend is None:
        import tempfile
        from pathlib import Path
        memory_backend = FilesystemMemoryBackend(Path(tempfile.mkdtemp()))

    return Engine(
        config=config,
        llm=llm,
        state=state,
        memory=memory_backend,
        skills={},  # no skills needed for onboarding tests
    )


def _base_config(onboarding: dict | None = None) -> dict:
    return {
        "adapter": {"type": "slack"},
        "llm": {"provider": "anthropic", "anthropic": {"api_key": "test"}},
        "bot": {"system_prompt": "You are a test bot."},
        "onboarding": onboarding or {},
        "learning": {"enabled": False},
    }


# ---------------------------------------------------------------------------
# Engine.handle_channel_join
# ---------------------------------------------------------------------------

class TestHandleChannelJoin:
    def test_returns_none_when_disabled(self):
        config = _base_config(onboarding={"enabled": False})
        engine = _make_engine(config)
        result = engine.handle_channel_join("C123", "general")
        assert result is None

    def test_returns_none_when_onboarding_key_absent(self):
        config = {
            "adapter": {"type": "slack"},
            "llm": {"provider": "anthropic", "anthropic": {"api_key": "test"}},
            "learning": {"enabled": False},
        }
        engine = _make_engine(config)
        result = engine.handle_channel_join("C123", "general")
        assert result is None

    def test_returns_intro_when_enabled(self):
        config = _base_config(onboarding={"enabled": True, "history_scan": False})
        engine = _make_engine(config)
        result = engine.handle_channel_join("C123", "general")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_synthetic_prompt_contains_channel_name(self):
        """Verify the prompt sent to LLM references the channel name."""
        config = _base_config(onboarding={"enabled": True, "history_scan": False})

        captured = {}
        llm = MagicMock()

        def _capture_call(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi there!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture_call

        engine = _make_engine(config, llm=llm)
        engine.handle_channel_join("C999", "ops-team")

        assert captured.get("messages"), "LLM was not called"
        first_user = next(
            m for m in captured["messages"] if m.get("role") == "user"
        )
        content = first_user["content"]
        text = content if isinstance(content, str) else content[0].get("text", "")
        assert "ops-team" in text
        assert "C999" in text

    def test_synthetic_prompt_includes_history_section(self):
        """History lines injected by fetch_fn appear in the LLM prompt."""
        config = _base_config(
            onboarding={"enabled": True, "history_scan": True, "history_messages": 10}
        )

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Intro text."}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture

        engine = _make_engine(config, llm=llm)
        engine.set_fetch_channel_history_fn(
            lambda channel_id, limit: (
                ["alice: hello world", "bob: how's the deploy going?"],
                {"U001": "alice", "U002": "bob"},
            )
        )

        engine.handle_channel_join("C100", "deploys")

        first_user = next(
            m for m in captured["messages"] if m.get("role") == "user"
        )
        content = first_user["content"]
        text = content if isinstance(content, str) else content[0].get("text", "")
        assert "alice: hello world" in text
        assert "bob: how's the deploy going?" in text

    def test_synthetic_prompt_includes_user_map(self):
        """User map appears in the LLM prompt."""
        config = _base_config(
            onboarding={"enabled": True, "history_scan": True, "history_messages": 5}
        )

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Done."}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture

        engine = _make_engine(config, llm=llm)
        engine.set_fetch_channel_history_fn(
            lambda cid, lim: ([], {"U42": "carol"})
        )

        engine.handle_channel_join("C200", "team")

        first_user = next(
            m for m in captured["messages"] if m.get("role") == "user"
        )
        content = first_user["content"]
        text = content if isinstance(content, str) else content[0].get("text", "")
        assert "carol" in text
        assert "U42" in text

    def test_isolated_session_key(self):
        """Onboarding uses onboard:<channel_id> session — not the channel session."""
        config = _base_config(onboarding={"enabled": True, "history_scan": False})
        from mithai.state.memory import MemoryStateBackend

        state = MemoryStateBackend()
        llm = MagicMock()
        resp = MagicMock()
        resp.content = [{"type": "text", "text": "Hi!"}]
        resp.stop_reason = "end_turn"
        llm.create_message.return_value = resp

        from mithai.core.engine import Engine
        engine = Engine(
            config=config, llm=llm, state=state, memory=None, skills={}
        )

        engine.handle_channel_join("CXYZ", "random")

        # Sessions are stored under the "sessions" namespace
        session_keys = state.list_keys("sessions")
        assert any("onboard:CXYZ" in k for k in session_keys), (
            f"Expected 'onboard:CXYZ' session key; found: {session_keys}"
        )
        # Normal channel session should NOT exist
        assert not any(k == "CXYZ" for k in session_keys)

    def test_no_approval_required(self):
        """_NoOpAdapter auto-approves — onboarding must not block on human approval."""
        config = _base_config(onboarding={"enabled": True, "history_scan": False})
        engine = _make_engine(config)

        # Should return without blocking even if tools are called
        result = engine.handle_channel_join("C300", "infra")
        # Returns a string (whatever the LLM says)
        assert result is not None


# ---------------------------------------------------------------------------
# get_agent_config — onboarding merge
# ---------------------------------------------------------------------------

class TestAgentConfigOnboardingMerge:
    def _config_with_agent(self, global_onboarding, agent_onboarding):
        return {
            "adapter": {"type": "slack"},
            "llm": {"provider": "anthropic"},
            "onboarding": global_onboarding,
            "agents": {
                "bot1": {"onboarding": agent_onboarding},
            },
        }

    def test_agent_onboarding_overrides_global(self):
        cfg = self._config_with_agent(
            global_onboarding={"enabled": False, "history_messages": 50},
            agent_onboarding={"enabled": True, "history_messages": 100},
        )
        merged = get_agent_config(cfg, "bot1")
        assert merged["onboarding"]["enabled"] is True
        assert merged["onboarding"]["history_messages"] == 100

    def test_agent_onboarding_inherits_unset_globals(self):
        cfg = self._config_with_agent(
            global_onboarding={"enabled": True, "intro": True},
            agent_onboarding={"history_messages": 200},
        )
        merged = get_agent_config(cfg, "bot1")
        # Global 'enabled' and 'intro' should be preserved
        assert merged["onboarding"]["enabled"] is True
        assert merged["onboarding"]["intro"] is True
        # Agent's override should win
        assert merged["onboarding"]["history_messages"] == 200

    def test_no_global_onboarding_uses_agent_only(self):
        config = {
            "adapter": {"type": "slack"},
            "llm": {"provider": "anthropic"},
            "agents": {
                "bot2": {"onboarding": {"enabled": True, "history_messages": 75}},
            },
        }
        merged = get_agent_config(config, "bot2")
        assert merged["onboarding"]["enabled"] is True
        assert merged["onboarding"]["history_messages"] == 75

    def test_missing_agent_returns_global(self):
        config = {
            "adapter": {"type": "slack"},
            "llm": {"provider": "anthropic"},
            "onboarding": {"enabled": False},
            "agents": {"bot3": {}},
        }
        merged = get_agent_config(config, "bot3")
        assert merged["onboarding"]["enabled"] is False


# ---------------------------------------------------------------------------
# SlackAdapter._resolve_user_ids
# ---------------------------------------------------------------------------

class TestResolveUserIds:
    def _make_adapter(self):
        """Create a SlackAdapter with a mocked Slack Bolt App."""
        from mithai.adapters.slack import SlackAdapter
        with patch("slack_bolt.App") as MockApp, \
             patch("slack_bolt.adapter.socket_mode.SocketModeHandler"):
            adapter = SlackAdapter(bot_token="xoxb-test", app_token="xapp-test")
            adapter._app = MockApp.return_value
            return adapter

    def test_returns_display_name(self):
        adapter = self._make_adapter()
        adapter._app.client.users_info.return_value = {
            "user": {
                "name": "alice_login",
                "profile": {"display_name": "Alice", "real_name": "Alice Smith"},
            }
        }
        result = adapter._resolve_user_ids({"U001"})
        assert result == {"U001": "Alice"}

    def test_falls_back_to_real_name_when_display_empty(self):
        adapter = self._make_adapter()
        adapter._app.client.users_info.return_value = {
            "user": {
                "name": "bob_login",
                "profile": {"display_name": "", "real_name": "Bob Jones"},
            }
        }
        result = adapter._resolve_user_ids({"U002"})
        assert result == {"U002": "Bob Jones"}

    def test_falls_back_to_raw_uid_on_api_error(self):
        adapter = self._make_adapter()
        adapter._app.client.users_info.side_effect = Exception("API error")
        result = adapter._resolve_user_ids({"UERR"})
        assert result == {"UERR": "UERR"}

    def test_multiple_users(self):
        adapter = self._make_adapter()

        def _users_info(user):
            if user == "U001":
                return {"user": {"name": "alice", "profile": {"display_name": "Alice", "real_name": ""}}}
            elif user == "U002":
                return {"user": {"name": "bob", "profile": {"display_name": "", "real_name": "Bob"}}}
            raise Exception("unknown")

        adapter._app.client.users_info.side_effect = _users_info
        result = adapter._resolve_user_ids({"U001", "U002"})
        assert result["U001"] == "Alice"
        assert result["U002"] == "Bob"


# ---------------------------------------------------------------------------
# SlackAdapter._fetch_channel_history
# ---------------------------------------------------------------------------

class TestFetchChannelHistory:
    def _make_adapter(self):
        from mithai.adapters.slack import SlackAdapter
        with patch("slack_bolt.App") as MockApp, \
             patch("slack_bolt.adapter.socket_mode.SocketModeHandler"):
            adapter = SlackAdapter(bot_token="xoxb-test", app_token="xapp-test")
            adapter._app = MockApp.return_value
            return adapter

    def _mock_history(self, adapter, messages: list[dict]):
        adapter._app.client.conversations_history.return_value = {"messages": messages}

    def _mock_users(self, adapter, user_map: dict):
        def _info(user):
            name = user_map.get(user, user)
            return {"user": {"name": name, "profile": {"display_name": name, "real_name": name}}}
        adapter._app.client.users_info.side_effect = _info

    def test_returns_oldest_first(self):
        adapter = self._make_adapter()
        # Slack returns newest first
        self._mock_history(adapter, [
            {"user": "U1", "text": "third"},
            {"user": "U1", "text": "second"},
            {"user": "U1", "text": "first"},
        ])
        self._mock_users(adapter, {"U1": "alice"})
        msgs, _ = adapter._fetch_channel_history("C1", 10)
        assert msgs[0].endswith("first")
        assert msgs[-1].endswith("third")

    def test_mentions_replaced_with_display_names(self):
        adapter = self._make_adapter()
        self._mock_history(adapter, [
            {"user": "U1", "text": "hey <@U2> what's up"},
        ])
        self._mock_users(adapter, {"U1": "alice", "U2": "bob"})
        msgs, _ = adapter._fetch_channel_history("C1", 10)
        assert "@bob" in msgs[0]
        assert "<@U2>" not in msgs[0]

    def test_user_map_returned(self):
        adapter = self._make_adapter()
        self._mock_history(adapter, [
            {"user": "U1", "text": "hello"},
        ])
        self._mock_users(adapter, {"U1": "alice"})
        _, user_map = adapter._fetch_channel_history("C1", 10)
        assert user_map.get("U1") == "alice"

    def test_empty_on_api_error(self):
        adapter = self._make_adapter()
        adapter._app.client.conversations_history.side_effect = Exception("fail")
        msgs, user_map = adapter._fetch_channel_history("C1", 10)
        assert msgs == []
        assert user_map == {}

    def test_skips_empty_messages(self):
        adapter = self._make_adapter()
        self._mock_history(adapter, [
            {"user": "U1", "text": ""},
            {"user": "U1", "text": "   "},
            {"user": "U1", "text": "real message"},
        ])
        self._mock_users(adapter, {"U1": "alice"})
        msgs, _ = adapter._fetch_channel_history("C1", 10)
        assert len(msgs) == 1
        assert "real message" in msgs[0]
