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

    def test_intro_prompt_includes_bot_name_from_config(self):
        """bot.name is injected into the phase-2 intro prompt."""
        config = _base_config(onboarding={"enabled": True})
        config["bot"]["name"] = "Aria"

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["last"] = kwargs
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi, I'm Aria!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = _make_engine(config, llm=llm)
        engine.handle_channel_join("C700", "ops")

        # Last call is the intro (phase 2) — must mention the bot name
        last_msg = captured["last"]["messages"][-1]["content"]
        assert "Aria" in last_msg

    def test_intro_prompt_falls_back_to_agent_id(self):
        """When bot.name is absent, agent_id is used as the name."""
        from mithai.core.engine import Engine
        from mithai.state.memory import MemoryStateBackend

        config = _base_config(onboarding={"enabled": True})

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["last"] = kwargs
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = Engine(
            config=config, llm=llm, state=MemoryStateBackend(), memory=None,
            skills={}, agent_id="ops-bot",
        )
        engine.handle_channel_join("C701", "infra")

        last_msg = captured["last"]["messages"][-1]["content"]
        assert "ops-bot" in last_msg

    def test_bot_name_takes_priority_over_agent_id(self):
        """bot.name wins over agent_id when both are set."""
        from mithai.core.engine import Engine
        from mithai.state.memory import MemoryStateBackend

        config = _base_config(onboarding={"enabled": True})
        config["bot"]["name"] = "Aria"

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["last"] = kwargs
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = Engine(
            config=config, llm=llm, state=MemoryStateBackend(), memory=None,
            skills={}, agent_id="ops-bot",
        )
        engine.handle_channel_join("C703", "ops")

        last_msg = captured["last"]["messages"][-1]["content"]
        assert "Aria" in last_msg
        assert "ops-bot" not in last_msg

    def test_agent_name_overrides_bot_name(self):
        """Agent-level name overrides global bot.name via get_agent_config merge."""
        from mithai.core.engine import Engine
        from mithai.core.config import get_agent_config
        from mithai.state.memory import MemoryStateBackend

        config = {
            "adapter": {"type": "slack"},
            "llm": {"provider": "anthropic", "anthropic": {"api_key": "test"}},
            "bot": {"name": "GlobalBot", "system_prompt": "You are a test bot."},
            "onboarding": {"enabled": True},
            "learning": {"enabled": False},
            "agents": {"my-agent": {"name": "Aria"}},
        }
        agent_config = get_agent_config(config, "my-agent")

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["last"] = kwargs
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = Engine(
            config=agent_config, llm=llm, state=MemoryStateBackend(), memory=None,
            skills={}, agent_id="my-agent",
        )
        engine.handle_channel_join("C704", "eng")

        last_msg = captured["last"]["messages"][-1]["content"]
        assert "Aria" in last_msg
        assert "GlobalBot" not in last_msg

    def test_intro_prompt_omits_name_clause_when_neither_set(self):
        """When bot.name and agent_id are both absent, no name clause is injected."""
        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            captured["last"] = kwargs
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        config = _base_config(onboarding={"enabled": True})
        llm.create_message.side_effect = _capture
        engine = _make_engine(config, llm=llm)  # no agent_id set
        engine.handle_channel_join("C702", "general")

        last_msg = captured["last"]["messages"][-1]["content"]
        assert "Your name is" not in last_msg

    def test_synthetic_prompt_contains_channel_name(self):
        """Verify the gather prompt sent to LLM references the channel name."""
        config = _base_config(onboarding={"enabled": True, "history_scan": False})

        captured = {}
        llm = MagicMock()

        def _capture_call(**kwargs):
            if not captured.get("messages"):  # only capture the first (gather) call
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
        """Gather prompt tells the bot to use its tools — no pre-fetched history injected."""
        config = _base_config(onboarding={"enabled": True})

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            if not captured.get("messages"):  # only capture the first (gather) call
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
        # Prompt names specific tools to call — bot must not receive pre-fetched history
        assert "slack_get_members" in text
        assert "slack_get_history" in text
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
        # After the denial result, LLM ends gather phase
        resp2 = MagicMock()
        resp2.content = [{"type": "text", "text": "Understood, I cannot run that."}]
        resp2.stop_reason = "end_turn"
        # Phase 2 intro call
        resp3 = MagicMock()
        resp3.content = [{"type": "text", "text": "Hi team!"}]
        resp3.stop_reason = "end_turn"
        llm.create_message.side_effect = [resp1, resp2, resp3]

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
        # After the tool result, gather phase ends
        resp2 = MagicMock()
        resp2.content = [{"type": "text", "text": "Done gathering."}]
        resp2.stop_reason = "end_turn"
        # Phase 2 intro call
        resp3 = MagicMock()
        resp3.content = [{"type": "text", "text": "Hi team, great to be here!"}]
        resp3.stop_reason = "end_turn"
        llm.create_message.side_effect = [resp1, resp2, resp3]

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

    def test_synthetic_prompt_instructs_member_fetch(self):
        """Gather prompt must tell the bot to call slack_get_members for the full roster."""
        config = _base_config(onboarding={"enabled": True})

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            if not captured.get("messages"):
                captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = _make_engine(config, llm=llm)
        engine.handle_channel_join("C500", "backend")

        first_user = next(m for m in captured["messages"] if m.get("role") == "user")
        text = first_user["content"]
        if not isinstance(text, str):
            text = text[0].get("text", "")
        assert "slack_get_members" in text

    def test_synthetic_prompt_instructs_memory_read_first(self):
        """Gather prompt must instruct the bot to read MEMORY.md before anything else."""
        config = _base_config(onboarding={"enabled": True})

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            if not captured.get("messages"):
                captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = _make_engine(config, llm=llm)
        engine.handle_channel_join("C501", "frontend")

        first_user = next(m for m in captured["messages"] if m.get("role") == "user")
        text = first_user["content"]
        if not isinstance(text, str):
            text = text[0].get("text", "")
        assert "MEMORY.md" in text
        # Read must appear before member fetch in the prompt
        assert text.index("MEMORY.md") < text.index("slack_get_members")

    def test_synthetic_prompt_acknowledges_multi_channel_context(self):
        """Gather prompt must tell the bot it operates across multiple channels."""
        config = _base_config(onboarding={"enabled": True})

        captured = {}
        llm = MagicMock()

        def _capture(**kwargs):
            if not captured.get("messages"):
                captured["messages"] = kwargs.get("messages", [])
            resp = MagicMock()
            resp.content = [{"type": "text", "text": "Hi!"}]
            resp.stop_reason = "end_turn"
            return resp

        llm.create_message.side_effect = _capture
        engine = _make_engine(config, llm=llm)
        engine.handle_channel_join("C502", "data")

        first_user = next(m for m in captured["messages"] if m.get("role") == "user")
        text = first_user["content"]
        if not isinstance(text, str):
            text = text[0].get("text", "")
        # Prompt should convey that the bot is already in multiple channels
        assert any(word in text.lower() for word in ("several", "multiple", "channels"))

    def test_noop_adapter_approves_slack_read_tools(self):
        """_NoOpAdapter must approve slack__slack_get_history and slack__slack_get_members during onboarding."""
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill, ToolDefinition
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path

        slack_handle = MagicMock(return_value='{"members": [], "count": 0}')
        slack_skill = Skill(
            name="slack",
            prompt="slack tools",
            tools=[
                ToolDefinition(
                    name="slack_get_members",
                    description="get members",
                    input_schema={"type": "object", "properties": {}},
                    human="approve",
                ),
                ToolDefinition(
                    name="slack_get_history",
                    description="get history",
                    input_schema={"type": "object", "properties": {}},
                    human="approve",
                ),
            ],
            handle=slack_handle,
            source_dir=Path("/fake"),
        )

        llm = MagicMock()
        resp1 = MagicMock()
        resp1.content = [{"type": "tool_use", "id": "t1", "name": "slack__slack_get_members", "input": {"channel_id": "C1"}}]
        resp1.stop_reason = "tool_use"
        resp2 = MagicMock()
        resp2.content = [{"type": "text", "text": "Done gathering."}]
        resp2.stop_reason = "end_turn"
        # Phase 2 intro call
        resp3 = MagicMock()
        resp3.content = [{"type": "text", "text": "Hi team!"}]
        resp3.stop_reason = "end_turn"
        llm.create_message.side_effect = [resp1, resp2, resp3]

        config = _base_config(onboarding={"enabled": True})
        engine = Engine(
            config=config, llm=llm, state=MemoryStateBackend(), memory=None,
            skills={"slack": slack_skill},
        )
        engine.handle_channel_join("C600", "ops")

        slack_handle.assert_called_once()

    def test_noop_adapter_denies_slack_send_message(self):
        """_NoOpAdapter must deny slack__slack_send_message — bot must not post during onboarding."""
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill, ToolDefinition
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path

        slack_handle = MagicMock(return_value='{"ok": true}')
        slack_skill = Skill(
            name="slack",
            prompt="slack tools",
            tools=[ToolDefinition(
                name="slack_send_message",
                description="send a message",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
                human="approve",
            )],
            handle=slack_handle,
            source_dir=Path("/fake"),
        )

        llm = MagicMock()
        resp1 = MagicMock()
        resp1.content = [{"type": "tool_use", "id": "t1", "name": "slack__slack_send_message", "input": {"text": "hi"}}]
        resp1.stop_reason = "tool_use"
        resp2 = MagicMock()
        resp2.content = [{"type": "text", "text": "Done."}]
        resp2.stop_reason = "end_turn"
        # Phase 2 intro call
        resp3 = MagicMock()
        resp3.content = [{"type": "text", "text": "Hi team!"}]
        resp3.stop_reason = "end_turn"
        llm.create_message.side_effect = [resp1, resp2, resp3]

        config = _base_config(onboarding={"enabled": True})
        engine = Engine(
            config=config, llm=llm, state=MemoryStateBackend(), memory=None,
            skills={"slack": slack_skill},
        )
        engine.handle_channel_join("C601", "ops")

        slack_handle.assert_not_called()

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

# ---------------------------------------------------------------------------
# SlackAdapter._decline_and_leave
# ---------------------------------------------------------------------------

class TestDeclineAndLeave:
    def _make_adapter(self, allowed_channels=None):
        """Create a SlackAdapter with mocked internals."""
        from mithai.adapters.slack import SlackAdapter
        with patch("slack_bolt.App") as MockApp, \
             patch("slack_bolt.adapter.socket_mode.SocketModeHandler"):
            adapter = SlackAdapter(
                bot_token="xoxb-test", app_token="xapp-test",
                allowed_channels=allowed_channels,
            )
            adapter._app = MockApp.return_value
            adapter._app.client = MagicMock()
            return adapter

    def test_sends_message_and_leaves(self):
        adapter = self._make_adapter(allowed_channels=["C_ALLOWED"])
        adapter._decline_and_leave("C_OTHER")

        adapter._app.client.chat_postMessage.assert_called_once()
        call_kwargs = adapter._app.client.chat_postMessage.call_args
        assert call_kwargs[1]["channel"] == "C_OTHER"
        assert "not onboarded" in call_kwargs[1]["text"].lower()

        adapter._app.client.conversations_leave.assert_called_once_with(channel="C_OTHER")

    def test_skips_dm_channels(self):
        adapter = self._make_adapter(allowed_channels=["C_ALLOWED"])
        adapter._decline_and_leave("D_DIRECT_MSG")

        adapter._app.client.chat_postMessage.assert_not_called()
        adapter._app.client.conversations_leave.assert_not_called()

    def test_skips_group_dm_channels(self):
        adapter = self._make_adapter(allowed_channels=["C_ALLOWED"])
        adapter._decline_and_leave("G_GROUP_DM")

        adapter._app.client.chat_postMessage.assert_not_called()
        adapter._app.client.conversations_leave.assert_not_called()

    def test_dedup_prevents_double_decline(self):
        """Concurrent calls for the same channel should only send one message."""
        import threading
        adapter = self._make_adapter(allowed_channels=["C_ALLOWED"])

        # Simulate slow chat_postMessage so both threads overlap
        event = threading.Event()

        def slow_post(**kwargs):
            event.wait(timeout=2)

        adapter._app.client.chat_postMessage.side_effect = slow_post

        t1 = threading.Thread(target=adapter._decline_and_leave, args=("C_DUP",))
        t2 = threading.Thread(target=adapter._decline_and_leave, args=("C_DUP",))
        t1.start()
        t2.start()

        # Let the slow post complete
        event.set()
        t1.join(timeout=3)
        t2.join(timeout=3)

        # Only one message should have been sent
        assert adapter._app.client.chat_postMessage.call_count == 1

    def test_leave_still_called_if_post_fails(self):
        adapter = self._make_adapter(allowed_channels=["C_ALLOWED"])
        adapter._app.client.chat_postMessage.side_effect = Exception("no permission")

        adapter._decline_and_leave("C_FAIL")

        # Leave should still be attempted even if posting failed
        adapter._app.client.conversations_leave.assert_called_once_with(channel="C_FAIL")

    def test_clears_dedup_set_after_completion(self):
        """After decline_and_leave completes, the channel should be removed from the dedup set."""
        adapter = self._make_adapter(allowed_channels=["C_ALLOWED"])
        adapter._decline_and_leave("C_CLEAN")

        assert "C_CLEAN" not in adapter._leaving_channels

    def test_clears_dedup_set_even_on_error(self):
        """Dedup set is cleaned up even if both API calls fail."""
        adapter = self._make_adapter(allowed_channels=["C_ALLOWED"])
        adapter._app.client.chat_postMessage.side_effect = Exception("fail")
        adapter._app.client.conversations_leave.side_effect = Exception("fail")

        adapter._decline_and_leave("C_ERR")

        assert "C_ERR" not in adapter._leaving_channels


class TestResolveUserIds:
    def _make_adapter(self):
        """Create a SlackAdapter with mocked Slack Bolt App and SlackClient."""
        from unittest.mock import MagicMock
        from mithai.adapters.slack import SlackAdapter
        with patch("slack_bolt.App") as MockApp, \
             patch("slack_bolt.adapter.socket_mode.SocketModeHandler"):
            adapter = SlackAdapter(bot_token="xoxb-test", app_token="xapp-test")
            adapter._app = MockApp.return_value
            adapter._slack_client._client = MagicMock()
            return adapter

    def test_returns_display_name(self):
        adapter = self._make_adapter()
        adapter._slack_client._client.users_info.return_value = {
            "user": {
                "name": "alice_login",
                "profile": {"display_name": "Alice", "real_name": "Alice Smith"},
            }
        }
        result = adapter._resolve_user_ids({"U001"})
        assert result == {"U001": "Alice"}

    def test_falls_back_to_real_name_when_display_empty(self):
        adapter = self._make_adapter()
        adapter._slack_client._client.users_info.return_value = {
            "user": {
                "name": "bob_login",
                "profile": {"display_name": "", "real_name": "Bob Jones"},
            }
        }
        result = adapter._resolve_user_ids({"U002"})
        assert result == {"U002": "Bob Jones"}

    def test_falls_back_to_raw_uid_on_api_error(self):
        adapter = self._make_adapter()
        adapter._slack_client._client.users_info.side_effect = Exception("API error")
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

        adapter._slack_client._client.users_info.side_effect = _users_info
        result = adapter._resolve_user_ids({"U001", "U002"})
        assert result["U001"] == "Alice"
        assert result["U002"] == "Bob"


# ---------------------------------------------------------------------------
# SlackAdapter._fetch_channel_history
# ---------------------------------------------------------------------------

class TestFetchChannelHistory:
    def _make_adapter(self):
        from unittest.mock import MagicMock
        from mithai.adapters.slack import SlackAdapter
        with patch("slack_bolt.App") as MockApp, \
             patch("slack_bolt.adapter.socket_mode.SocketModeHandler"):
            adapter = SlackAdapter(bot_token="xoxb-test", app_token="xapp-test")
            adapter._app = MockApp.return_value
            adapter._slack_client._client = MagicMock()
            return adapter

    def _mock_history(self, adapter, messages: list[dict]):
        adapter._slack_client._client.conversations_history.return_value = {"ok": True, "messages": messages}

    def _mock_users(self, adapter, user_map: dict):
        def _info(user):
            name = user_map.get(user, user)
            return {"user": {"name": name, "profile": {"display_name": name, "real_name": name}}}
        adapter._slack_client._client.users_info.side_effect = _info

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
        adapter._slack_client._client.conversations_history.side_effect = Exception("fail")
        msgs, user_map = adapter._fetch_channel_history("C1", 10)
        assert msgs == []
        assert user_map == {}

    def test_empty_on_ok_false(self):
        adapter = self._make_adapter()
        adapter._slack_client._client.conversations_history.return_value = {
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
