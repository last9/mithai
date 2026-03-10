"""Tests for Slack channel onboarding."""

from unittest.mock import MagicMock, patch
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

    def test_synthetic_prompt_instructs_tool_use(self):
        """Prompt tells the bot to use its tools — no pre-fetched history injected."""
        config = _base_config(onboarding={"enabled": True})

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = _make_engine(config, llm=llm)
        engine.handle_channel_join("C100", "deploys")

        first_user = next(m for m in captured["messages"] if m.get("role") == "user")
        text = first_user["content"]
        if not isinstance(text, str):
            text = text[0].get("text", "")
        # Prompt should tell bot to use tools — not dump raw history
        assert "tools" in text.lower()
        # No pre-fetched history or user map injected by the framework
        assert "Recent channel messages" not in text
        assert "Known Slack users" not in text

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
        """Smoke test: onboarding completes without hanging when no tools are called."""
        config = _base_config(onboarding={"enabled": True, "history_scan": False})
        engine = _make_engine(config)
        result = engine.handle_channel_join("C300", "infra")
        assert result is not None

    def test_noop_adapter_denies_non_memory_tools(self):
        """_NoOpAdapter must deny non-memory tools (e.g. shell) during onboarding.

        This is the critical security fix: if the shell skill is loaded, the LLM
        calling shell__run must be denied — the tool handler must never execute.
        """
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill, ToolDefinition
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path

        shell_handle = MagicMock(return_value="executed")
        shell_skill = Skill(
            name="shell",
            prompt="run shell commands",
            tools=[ToolDefinition(
                name="run",
                description="run a command",
                input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
                human="approve",
            )],
            handle=shell_handle,
            source_dir=Path("/fake"),
        )

        llm = MagicMock()
        # LLM attempts to call shell__run
        resp1 = MagicMock()
        resp1.content = [{"type": "tool_use", "id": "t1", "name": "shell__run", "input": {"command": "rm -rf /"}}]
        resp1.stop_reason = "tool_use"
        # After the denial result, LLM ends turn
        resp2 = MagicMock()
        resp2.content = [{"type": "text", "text": "Understood, I cannot run that."}]
        resp2.stop_reason = "end_turn"
        llm.create_message.side_effect = [resp1, resp2]

        config = _base_config(onboarding={"enabled": True, "history_scan": False})
        engine = Engine(
            config=config, llm=llm, state=MemoryStateBackend(), memory=None,
            skills={"shell": shell_skill},
        )

        engine.handle_channel_join("C400", "security-test")

        # The actual shell handler must never have been invoked
        shell_handle.assert_not_called()

    def test_noop_adapter_approves_memory_tools(self):
        """_NoOpAdapter must approve memory__ tools during onboarding.

        Onboarding's primary job is to save channel context to memory —
        memory tools must be allowed through without blocking.
        """
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill, ToolDefinition
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path

        memory_handle = MagicMock(return_value="saved")
        memory_skill = Skill(
            name="memory",
            prompt="store and retrieve memory",
            tools=[ToolDefinition(
                name="write",
                description="write a memory entry",
                input_schema={"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}},
                human="approve",
            )],
            handle=memory_handle,
            source_dir=Path("/fake"),
        )

        llm = MagicMock()
        # LLM calls memory__write
        resp1 = MagicMock()
        resp1.content = [{"type": "tool_use", "id": "t1", "name": "memory__write", "input": {"key": "channel_context", "value": "ops channel"}}]
        resp1.stop_reason = "tool_use"
        # After the tool result, LLM produces intro text
        resp2 = MagicMock()
        resp2.content = [{"type": "text", "text": "Hi team, I've saved context about this channel."}]
        resp2.stop_reason = "end_turn"
        llm.create_message.side_effect = [resp1, resp2]

        config = _base_config(onboarding={"enabled": True, "history_scan": False})
        engine = Engine(
            config=config, llm=llm, state=MemoryStateBackend(), memory=None,
            skills={"memory": memory_skill},
        )

        engine.handle_channel_join("C401", "teamchat")

        # memory__write must have been executed (approved by _NoOpAdapter)
        memory_handle.assert_called_once()

    def test_stale_session_cleared_on_rejoin(self):
        """Re-joining a channel clears the previous onboarding session before running."""
        config = _base_config(onboarding={"enabled": True, "history_scan": False})
        from mithai.state.memory import MemoryStateBackend
        from mithai.core.engine import Engine
        from mithai.core.session import SessionManager

        state = MemoryStateBackend()
        llm = MagicMock()
        resp = MagicMock()
        resp.content = [{"type": "text", "text": "Hi again!"}]
        resp.stop_reason = "end_turn"
        llm.create_message.return_value = resp

        engine = Engine(config=config, llm=llm, state=state, memory=None, skills={})

        # First join — creates a session
        engine.handle_channel_join("CREJOIN", "ops")
        session_key = SessionManager.session_key("slack", "onboard:CREJOIN")
        assert state.get("sessions", session_key) is not None

        # Second join — session should be cleared and re-created fresh
        llm.create_message.reset_mock()
        engine.handle_channel_join("CREJOIN", "ops")

        # LLM was called again (not skipped)
        assert llm.create_message.called
        # Fresh session exists
        new_session = state.get("sessions", session_key)
        assert new_session is not None
        # Only 1 turn — from the second join (old turns were cleared before it ran)
        assert len(new_session["turns"]) == 1


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

    def test_prompt_has_no_hardcoded_memory_paths(self):
        """Framework onboarding prompt must never dictate specific memory paths."""
        config = _base_config(onboarding={"enabled": True})

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = _make_engine(config, llm=llm)
        engine.handle_channel_join("C2", "ops")

        first_user = next(m for m in captured["messages"] if m.get("role") == "user")
        text = first_user["content"]
        if not isinstance(text, str):
            text = text[0].get("text", "")
        assert "team/slack_users.md" not in text
        assert "team/members" not in text
        assert "team/dependencies" not in text


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
        adapter._app.client.conversations_history.return_value = {"ok": True, "messages": messages}

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

    def test_empty_on_ok_false(self):
        adapter = self._make_adapter()
        adapter._app.client.conversations_history.return_value = {
            "ok": False,
            "error": "not_in_channel",
        }
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
