"""Tests for multi-agent support."""

import json
import logging
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from mithai.core.config import (
    get_agent_config,
    get_agents,
    get_default_agent_id,
)
from mithai.core.session import SessionManager
from mithai.core.skill_loader import Skill, ToolDefinition, filter_skills
from mithai.core.tool_router import ToolRouter


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

class TestGetAgents:
    def test_no_agents_key_returns_none(self):
        config = {"adapter": {"type": "cli"}, "llm": {"provider": "anthropic"}}
        assert get_agents(config) is None

    def test_empty_agents_returns_none(self):
        config = {"agents": {}}
        assert get_agents(config) is None

    def test_strips_default_agent_key(self):
        config = {
            "agents": {
                "devops": {"name": "DevOps Agent"},
                "support": {"name": "Support Agent"},
                "default_agent": "devops",
            }
        }
        agents = get_agents(config)
        assert "devops" in agents
        assert "support" in agents
        assert "default_agent" not in agents

    def test_default_agent_id(self):
        config = {"agents": {"default_agent": "devops", "devops": {}}}
        assert get_default_agent_id(config) == "devops"

    def test_default_agent_id_none_without_agents(self):
        config = {}
        assert get_default_agent_id(config) is None


class TestGetAgentConfig:
    def test_inherits_global_config(self):
        config = {
            "bot": {"system_prompt": "global prompt"},
            "llm": {"provider": "anthropic"},
            "agents": {"devops": {"name": "DevOps"}},
        }
        merged = get_agent_config(config, "devops")
        assert merged["llm"]["provider"] == "anthropic"

    def test_agent_system_prompt_overrides_global(self):
        config = {
            "bot": {"system_prompt": "global prompt"},
            "agents": {"devops": {"system_prompt": "devops prompt"}},
        }
        merged = get_agent_config(config, "devops")
        assert merged["bot"]["system_prompt"] == "devops prompt"

    def test_unknown_agent_returns_global(self):
        config = {"bot": {"system_prompt": "global"}, "agents": {"devops": {}}}
        merged = get_agent_config(config, "nonexistent")
        assert merged["bot"]["system_prompt"] == "global"

    def test_no_agents_section_returns_global(self):
        config = {"bot": {"system_prompt": "global"}}
        merged = get_agent_config(config, "devops")
        assert merged == config

    def test_agent_level_keys_other_than_system_prompt_are_not_merged(self):
        """get_agent_config only merges system_prompt from the agent definition.
        Other agent-level keys like 'llm' or 'adapter' are silently dropped."""
        config = {
            "bot": {"system_prompt": "global prompt"},
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            "agents": {
                "devops": {
                    "system_prompt": "devops prompt",
                    "llm": {"provider": "openai", "model": "gpt-4"},
                    "adapter": {"slack": {"bot_token": "agent-tok"}},
                }
            },
        }
        merged = get_agent_config(config, "devops")
        # system_prompt IS merged
        assert merged["bot"]["system_prompt"] == "devops prompt"
        # agent-level llm is NOT merged — global llm is preserved
        assert merged["llm"]["provider"] == "anthropic"
        assert merged["llm"]["model"] == "claude-sonnet-4-6"
        # agent-level adapter is NOT present in merged config top-level
        assert "adapter" not in merged or merged.get("adapter") == config.get("adapter")


# ---------------------------------------------------------------------------
# Skill filtering
# ---------------------------------------------------------------------------

def _make_skill(name: str) -> Skill:
    return Skill(
        name=name,
        prompt=f"{name} prompt",
        tools=[
            ToolDefinition(
                name="do_thing",
                description=f"{name} does a thing",
                input_schema={"type": "object", "properties": {}},
            )
        ],
        handle=lambda n, i, c: json.dumps({"ok": True}),
        source_dir=Path(f"/fake/{name}"),
    )


class TestFilterSkills:
    def test_filters_to_allowed(self):
        skills = {"shell": _make_skill("shell"), "memory": _make_skill("memory"), "k8s": _make_skill("k8s")}
        filtered = filter_skills(skills, ["shell", "memory"])
        assert set(filtered.keys()) == {"shell", "memory"}

    def test_missing_skill_warns(self, caplog):
        skills = {"shell": _make_skill("shell")}
        with caplog.at_level(logging.WARNING):
            filtered = filter_skills(skills, ["shell", "nonexistent"])
            assert "nonexistent" in caplog.text
        assert set(filtered.keys()) == {"shell"}

    def test_empty_allowlist_returns_empty(self):
        skills = {"shell": _make_skill("shell")}
        filtered = filter_skills(skills, [])
        assert filtered == {}


# ---------------------------------------------------------------------------
# Tool router allowlist
# ---------------------------------------------------------------------------

class TestToolRouterAllowlist:
    def test_allowed_tool_executes(self):
        skills = {"test": _make_skill("test")}
        router = ToolRouter(skills, allowed_tools={"test__do_thing"})
        result = json.loads(router.route("test__do_thing", {}, {}))
        assert result.get("ok") is True

    def test_disallowed_tool_rejected(self):
        skills = {"test": _make_skill("test")}
        router = ToolRouter(skills, allowed_tools={"test__do_thing"})
        result = json.loads(router.route("other__hack", {}, {}))
        assert "not available" in result["error"]

    def test_no_allowlist_passes_everything(self):
        skills = {"test": _make_skill("test")}
        router = ToolRouter(skills)
        result = json.loads(router.route("test__do_thing", {}, {}))
        assert result.get("ok") is True

    def test_unknown_tool_rejected_at_allowlist(self):
        """test__nonexistent is NOT in allowed_tools, so it's rejected at the
        allowlist check before the tool index is ever consulted."""
        skills = {"test": _make_skill("test")}
        router = ToolRouter(skills, allowed_tools={"test__do_thing"})
        result = json.loads(router.route("test__nonexistent", {}, {}))
        assert "not available" in result["error"]

    def test_in_allowlist_but_missing_from_index(self):
        """A tool that passes the allowlist but doesn't exist in any skill's
        tool index is rejected via the 'Unknown tool' path."""
        skills = {"test": _make_skill("test")}
        # Include a tool name in the allowlist that no skill actually provides
        router = ToolRouter(skills, allowed_tools={"test__do_thing", "test__phantom"})
        result = json.loads(router.route("test__phantom", {}, {}))
        assert "Unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# Session key scoping
# ---------------------------------------------------------------------------

class TestSessionKeyWithAgent:
    def test_no_agent_id(self):
        key = SessionManager.session_key("slack", "C123")
        assert key == "slack:C123"

    def test_with_agent_id(self):
        key = SessionManager.session_key("slack", "C123", agent_id="devops")
        assert key == "slack:C123:devops"

    def test_none_agent_id_same_as_omitted(self):
        key = SessionManager.session_key("slack", "C123", agent_id=None)
        assert key == "slack:C123"


# ---------------------------------------------------------------------------
# Per-agent adapter creation
# ---------------------------------------------------------------------------

class TestPerAgentAdapters:
    """Test per-agent adapter creation (each agent = own Slack app)."""

    def test_create_adapter_with_explicit_config(self):
        """_create_adapter uses adapter_config when provided."""
        from mithai.cli.run_cmd import _create_adapter

        config = {"adapter": {"types": ["slack"], "slack": {"bot_token": "global", "app_token": "global"}}}
        per_agent = {"bot_token": "agent-tok", "app_token": "agent-app"}

        with patch("mithai.adapters.slack.SlackAdapter") as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_adapter(config, "slack", adapter_config=per_agent)
            mock_cls.assert_called_once_with(
                bot_token="agent-tok",
                app_token="agent-app",
                allowed_channels=None,
                approval_timeout=300,
                respond="all",
            )

    def test_create_adapter_falls_back_to_global(self):
        """Without adapter_config, global adapter section is used."""
        from mithai.cli.run_cmd import _create_adapter

        config = {"adapter": {"slack": {"bot_token": "global-tok", "app_token": "global-app"}}}

        with patch("mithai.adapters.slack.SlackAdapter") as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_adapter(config, "slack")
            mock_cls.assert_called_once_with(
                bot_token="global-tok",
                app_token="global-app",
                allowed_channels=None,
                approval_timeout=300,
                respond="all",
            )

    def test_create_adapter_cli_ignores_adapter_config(self):
        """CLI adapter doesn't use adapter_config (no credentials needed)."""
        from mithai.cli.run_cmd import _create_adapter

        with patch("mithai.adapters.cli.CLIAdapter") as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_adapter({}, "cli", adapter_config={"ignored": True})
            mock_cls.assert_called_once()

    def test_run_multi_agent_creates_per_agent_adapters(self):
        """_run_multi_agent creates one adapter per agent, each wired to its engine."""
        from mithai.cli.run_cmd import _run_multi_agent

        agents_config = {
            "devops": {
                "name": "DevOps",
                "adapter": {"slack": {"bot_token": "dev-tok", "app_token": "dev-app"}},
            },
            "support": {
                "name": "Support",
                "adapter": {"slack": {"bot_token": "sup-tok", "app_token": "sup-app"}},
            },
        }
        config = {
            "bot": {"system_prompt": "test"},
            "adapter": {"types": ["slack"], "slack": {}},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": []},
            "agents": {**agents_config, "default_agent": "devops"},
        }

        mock_devops_adapter = MagicMock()
        mock_support_adapter = MagicMock()
        adapters_created = []

        def fake_create_adapter(cfg, atype, adapter_config=None, respond="all"):
            if adapter_config and adapter_config.get("bot_token") == "dev-tok":
                adapters_created.append(("devops", mock_devops_adapter))
                return mock_devops_adapter
            elif adapter_config and adapter_config.get("bot_token") == "sup-tok":
                adapters_created.append(("support", mock_support_adapter))
                return mock_support_adapter
            return MagicMock()

        with patch("mithai.cli.run_cmd._create_engines_multi") as mock_engines, \
             patch("mithai.cli.run_cmd._create_adapter", side_effect=fake_create_adapter), \
             patch("mithai.cli.run_cmd.threading") as mock_threading:

            devops_engine = MagicMock()
            support_engine = MagicMock()
            mock_engines.return_value = {"devops": devops_engine, "support": support_engine}

            # Make threads join immediately
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread

            _run_multi_agent(config, agents_config)

            # Two adapters created — one per agent
            assert len(adapters_created) == 2
            assert adapters_created[0][0] == "devops"
            assert adapters_created[1][0] == "support"

            # Four threads started — _startup_onboard_channels + _run_adapter per adapter
            assert mock_threading.Thread.call_count == 4

            # Each engine got late_bind with its own adapter
            devops_engine.late_bind.assert_called_once()
            support_engine.late_bind.assert_called_once()

            # Verify devops engine got the devops adapter
            devops_adapters = devops_engine.late_bind.call_args[0][0]
            assert len(devops_adapters) == 1
            assert devops_adapters[0] == ("slack", mock_devops_adapter)

            # Verify support engine got the support adapter
            support_adapters = support_engine.late_bind.call_args[0][0]
            assert len(support_adapters) == 1
            assert support_adapters[0] == ("slack", mock_support_adapter)

            # Verify each thread is wired to the correct engine's handle method
            thread_calls = mock_threading.Thread.call_args_list
            thread_targets = {call.kwargs.get("args", call[1].get("args", ()))[1]: call.kwargs.get("args", call[1].get("args", ()))[2] for call in thread_calls}
            # The adapter → engine.handle mapping must be correct
            assert thread_targets[mock_devops_adapter] == devops_engine.handle
            assert thread_targets[mock_support_adapter] == support_engine.handle

    def test_run_multi_agent_no_adapters_raises(self):
        """If no agent has an adapter section, raise an error."""
        from mithai.cli.run_cmd import _run_multi_agent

        agents_config = {"devops": {"name": "DevOps"}}  # no adapter key
        config = {
            "bot": {"system_prompt": "test"},
            "adapter": {"types": ["slack"], "slack": {}},
            "llm": {"provider": "anthropic"},
            "skills": {"paths": []},
        }

        with patch("mithai.cli.run_cmd._create_engines_multi") as mock_engines:
            mock_engines.return_value = {"devops": MagicMock()}
            with pytest.raises(click.ClickException, match="adapter"):
                _run_multi_agent(config, agents_config)


# ---------------------------------------------------------------------------
# Engine with agent_id
# ---------------------------------------------------------------------------

class TestEngineAgentId:
    """Test that Engine's agent_id produces isolated session keys per agent."""

    def _make_engine(self, agent_id=None):
        from mithai.core.engine import Engine

        llm = MagicMock()
        state = MagicMock()
        state.get.return_value = None
        skills = {"shell": _make_skill("shell")}
        config = {
            "bot": {"system_prompt": "test"},
            "adapter": {"type": "cli"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": []},
        }
        return Engine(config=config, llm=llm, state=state, agent_id=agent_id, skills=skills)

    def test_different_agent_ids_produce_different_session_keys(self):
        """Two engines with different agent_id on the same channel must use
        different session keys, so their conversation histories stay isolated."""
        key_devops = SessionManager.session_key("slack", "C123", agent_id="devops")
        key_support = SessionManager.session_key("slack", "C123", agent_id="support")
        assert key_devops != key_support
        assert "devops" in key_devops
        assert "support" in key_support

    def test_engine_without_agent_id_uses_bare_key(self):
        """An engine with no agent_id produces a session key without an agent
        suffix, preserving backward compatibility."""
        key_no_agent = SessionManager.session_key("slack", "C123", agent_id=None)
        key_with_agent = SessionManager.session_key("slack", "C123", agent_id="devops")
        assert key_no_agent == "slack:C123"
        assert key_no_agent != key_with_agent

    def test_engine_uses_agent_id_for_session_isolation(self):
        """Two engines with different agent_ids sharing the same state must not
        see each other's conversation history on the same channel."""
        from mithai.adapters.base import IncomingMessage
        from mithai.state.memory import MemoryStateBackend

        shared_state = MemoryStateBackend()

        llm = MagicMock()
        resp = MagicMock()
        resp.content = [{"type": "text", "text": "ok"}]
        resp.stop_reason = "end_turn"
        llm.create_message.return_value = resp

        config = {
            "bot": {"system_prompt": "test"},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "k"},
            "skills": {"paths": []},
        }
        from mithai.core.engine import Engine
        engine_devops = Engine(config=config, llm=llm, state=shared_state, agent_id="devops", skills={})

        adapter = MagicMock()
        adapter.fetch_thread_context.return_value = None

        msg = IncomingMessage(
            text="hello from devops",
            channel_id="C1", user_id="alice",
            platform="slack", thread_id="111.000",
        )
        engine_devops.handle(msg, adapter)

        # Support engine's session for the same thread must be empty
        key_support = SessionManager.session_key("slack", "111.000", agent_id="support")
        session = shared_state.get("sessions", key_support)
        assert session is None or not session.get("turns")

    def test_engine_without_agent_id_does_not_pollute_named_agent_sessions(self):
        """An engine with no agent_id must use a bare session key, leaving
        named-agent sessions untouched."""
        from mithai.adapters.base import IncomingMessage
        from mithai.state.memory import MemoryStateBackend

        shared_state = MemoryStateBackend()

        llm = MagicMock()
        resp = MagicMock()
        resp.content = [{"type": "text", "text": "ok"}]
        resp.stop_reason = "end_turn"
        llm.create_message.return_value = resp

        config = {
            "bot": {"system_prompt": "test"},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "k"},
            "skills": {"paths": []},
        }
        from mithai.core.engine import Engine
        engine_bare = Engine(config=config, llm=llm, state=shared_state, agent_id=None, skills={})
        adapter = MagicMock()
        adapter.fetch_thread_context.return_value = None

        msg = IncomingMessage(
            text="hello", channel_id="C1", user_id="alice",
            platform="slack", thread_id="111.000",
        )
        engine_bare.handle(msg, adapter)

        # Named-agent session for the same thread must be empty
        key_named = SessionManager.session_key("slack", "111.000", agent_id="devops")
        session = shared_state.get("sessions", key_named)
        assert session is None or not session.get("turns")


# ---------------------------------------------------------------------------
# Backwards compatibility — no agents: key
# ---------------------------------------------------------------------------

class TestBackwardsCompatibility:
    def test_create_engine_single(self):
        from mithai.cli.run_cmd import _create_engine_single

        config = {
            "bot": {"system_prompt": "test"},
            "adapter": {"type": "cli"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": []},
            "state": {"backend": "memory"},
            "learning": {"memory": {"backend": "filesystem", "filesystem": {"path": "/tmp/mithai-test-mem"}}},
        }

        with patch("mithai.cli.run_cmd._create_llm") as mock_llm, \
             patch("mithai.cli.run_cmd._create_state") as mock_state, \
             patch("mithai.cli.run_cmd._create_memory_backend") as mock_mem:
            mock_llm.return_value = MagicMock()
            mock_state.return_value = MagicMock()
            mock_mem.return_value = MagicMock()

            engines = _create_engine_single(config)
            assert "default" in engines
            assert len(engines) == 1


# ---------------------------------------------------------------------------
# Integration test for _create_engines_multi
# ---------------------------------------------------------------------------

def _write_fake_skill(skill_dir: Path, skill_name: str) -> None:
    """Create a minimal skill directory with prompt.md and tools.py."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "prompt.md").write_text(f"{skill_name} skill prompt")
    (skill_dir / "tools.py").write_text(textwrap.dedent(f"""\
        import json

        TOOLS = [
            {{
                "name": "do_{skill_name}",
                "description": "{skill_name} does a thing",
                "input_schema": {{"type": "object", "properties": {{}}}},
            }}
        ]

        def handle(name, input, ctx):
            return json.dumps({{"ok": True, "skill": "{skill_name}"}})
    """))


class TestCreateEnginesMultiIntegration:
    """Integration tests for _create_engines_multi that exercise real skill
    loading, filtering, and memory backend creation instead of mocking
    them out entirely."""

    def test_skill_filtering_and_memory_isolation(self, tmp_path):
        """Agents with skills.allowed get only those skills, and per-agent
        memory paths produce separate FilesystemMemoryBackend instances."""
        from mithai.cli.run_cmd import _create_engines_multi
        from mithai.memory.filesystem import FilesystemMemoryBackend

        # Create two fake skills on disk
        skills_dir = tmp_path / "skills"
        _write_fake_skill(skills_dir / "shell", "shell")
        _write_fake_skill(skills_dir / "memory", "memory")

        # Per-agent memory directories
        devops_mem = tmp_path / "mem_devops"
        support_mem = tmp_path / "mem_support"

        config = {
            "bot": {"system_prompt": "test"},
            "adapter": {"type": "cli"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": [str(skills_dir)]},
            "agents": {
                "devops": {
                    "skills": {"allowed": ["shell"]},
                    "memory": {"path": str(devops_mem)},
                },
                "support": {
                    "skills": {"allowed": ["memory"]},
                    "memory": {"path": str(support_mem)},
                },
            },
        }
        agents_config = {
            "devops": config["agents"]["devops"],
            "support": config["agents"]["support"],
        }

        with patch("mithai.cli.run_cmd._create_llm") as mock_llm, \
             patch("mithai.cli.run_cmd._create_state") as mock_state:
            mock_llm.return_value = MagicMock()
            mock_state_inst = MagicMock()
            mock_state_inst.get.return_value = None
            mock_state.return_value = mock_state_inst

            engines = _create_engines_multi(config, agents_config)

        assert set(engines.keys()) == {"devops", "support"}

        # devops agent should only have shell skill
        devops_skills = engines["devops"]._skills
        assert set(devops_skills.keys()) == {"shell"}

        # support agent should only have memory skill
        support_skills = engines["support"]._skills
        assert set(support_skills.keys()) == {"memory"}

        # Each agent has its own FilesystemMemoryBackend with its own path
        devops_memory = engines["devops"]._memory
        support_memory = engines["support"]._memory
        assert isinstance(devops_memory, FilesystemMemoryBackend)
        assert isinstance(support_memory, FilesystemMemoryBackend)
        assert devops_memory._base != support_memory._base
        assert str(devops_mem) in str(devops_memory._base)
        assert str(support_mem) in str(support_memory._base)

    def test_agent_without_skill_allowlist_gets_all_skills(self, tmp_path):
        """An agent without skills.allowed gets every loaded skill."""
        from mithai.cli.run_cmd import _create_engines_multi

        # Create two fake skills on disk
        skills_dir = tmp_path / "skills"
        _write_fake_skill(skills_dir / "shell", "shell")
        _write_fake_skill(skills_dir / "memory", "memory")

        config = {
            "bot": {"system_prompt": "test"},
            "adapter": {"type": "cli"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": [str(skills_dir)]},
            "agents": {
                "all_access": {},  # no skills.allowed — should get everything
                "limited": {"skills": {"allowed": ["shell"]}},
            },
        }
        agents_config = {
            "all_access": config["agents"]["all_access"],
            "limited": config["agents"]["limited"],
        }

        with patch("mithai.cli.run_cmd._create_llm") as mock_llm, \
             patch("mithai.cli.run_cmd._create_state") as mock_state:
            mock_llm.return_value = MagicMock()
            mock_state_inst = MagicMock()
            mock_state_inst.get.return_value = None
            mock_state.return_value = mock_state_inst

            engines = _create_engines_multi(config, agents_config)

        # all_access agent has both fake skills plus any bundled skills
        all_skills = set(engines["all_access"]._skills.keys())
        assert {"shell", "memory"}.issubset(all_skills)

        # limited agent has only shell
        assert "shell" in engines["limited"]._skills

    def test_shared_llm_and_state(self, tmp_path):
        """All agents share the same LLM and state instances (created once)."""
        from mithai.cli.run_cmd import _create_engines_multi

        skills_dir = tmp_path / "skills"
        _write_fake_skill(skills_dir / "shell", "shell")

        config = {
            "bot": {"system_prompt": "test"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": [str(skills_dir)]},
        }
        agents_config = {
            "alpha": {"skills": {"allowed": ["shell"]}},
            "beta": {"skills": {"allowed": ["shell"]}},
        }

        with patch("mithai.cli.run_cmd._create_llm") as mock_llm, \
             patch("mithai.cli.run_cmd._create_state") as mock_state:
            llm_instance = MagicMock()
            state_instance = MagicMock()
            state_instance.get.return_value = None
            mock_llm.return_value = llm_instance
            mock_state.return_value = state_instance

            engines = _create_engines_multi(config, agents_config)

            # LLM and state created exactly once
            mock_llm.assert_called_once()
            mock_state.assert_called_once()

        # Both engines share the same LLM and state object
        assert engines["alpha"]._llm is engines["beta"]._llm
        assert engines["alpha"]._state is engines["beta"]._state

    def test_agent_id_propagated_to_engines(self, tmp_path):
        """Each engine stores its own agent_id."""
        from mithai.cli.run_cmd import _create_engines_multi

        skills_dir = tmp_path / "skills"
        _write_fake_skill(skills_dir / "shell", "shell")

        config = {
            "bot": {"system_prompt": "test"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": [str(skills_dir)]},
        }
        agents_config = {
            "devops": {},
            "support": {},
        }

        with patch("mithai.cli.run_cmd._create_llm") as mock_llm, \
             patch("mithai.cli.run_cmd._create_state") as mock_state:
            mock_llm.return_value = MagicMock()
            state_instance = MagicMock()
            state_instance.get.return_value = None
            mock_state.return_value = state_instance

            engines = _create_engines_multi(config, agents_config)

        assert engines["devops"]._agent_id == "devops"
        assert engines["support"]._agent_id == "support"

    def test_per_agent_system_prompt_override(self, tmp_path):
        """Agent-level system_prompt overrides the global one in the engine config."""
        from mithai.cli.run_cmd import _create_engines_multi

        skills_dir = tmp_path / "skills"
        _write_fake_skill(skills_dir / "shell", "shell")

        agents_config = {
            "devops": {"system_prompt": "devops prompt"},
            "support": {},  # inherits global
        }
        config = {
            "bot": {"system_prompt": "global prompt"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": [str(skills_dir)]},
            "agents": {**agents_config, "default_agent": "devops"},
        }

        with patch("mithai.cli.run_cmd._create_llm") as mock_llm, \
             patch("mithai.cli.run_cmd._create_state") as mock_state:
            mock_llm.return_value = MagicMock()
            state_instance = MagicMock()
            state_instance.get.return_value = None
            mock_state.return_value = state_instance

            engines = _create_engines_multi(config, agents_config)

        assert engines["devops"]._config["bot"]["system_prompt"] == "devops prompt"
        assert engines["support"]._config["bot"]["system_prompt"] == "global prompt"

    def test_default_memory_fallback(self, tmp_path):
        """Agent without memory.path falls back to global memory backend."""
        from mithai.cli.run_cmd import _create_engines_multi

        skills_dir = tmp_path / "skills"
        _write_fake_skill(skills_dir / "shell", "shell")

        custom_mem = tmp_path / "custom_mem"

        config = {
            "bot": {"system_prompt": "test"},
            "llm": {"provider": "anthropic", "model": "test", "anthropic": {"api_key": "k"}},
            "skills": {"paths": [str(skills_dir)]},
            "learning": {
                "memory": {
                    "backend": "filesystem",
                    "filesystem": {"path": str(tmp_path / "global_mem")},
                },
            },
        }
        agents_config = {
            "with_path": {"memory": {"path": str(custom_mem)}},
            "without_path": {},
        }

        with patch("mithai.cli.run_cmd._create_llm") as mock_llm, \
             patch("mithai.cli.run_cmd._create_state") as mock_state:
            mock_llm.return_value = MagicMock()
            state_instance = MagicMock()
            state_instance.get.return_value = None
            mock_state.return_value = state_instance

            engines = _create_engines_multi(config, agents_config)

        from mithai.memory.filesystem import FilesystemMemoryBackend

        # Agent with explicit memory.path uses it
        assert isinstance(engines["with_path"]._memory, FilesystemMemoryBackend)
        assert str(custom_mem) in str(engines["with_path"]._memory._base)

        # Agent without memory.path gets global memory backend
        assert isinstance(engines["without_path"]._memory, FilesystemMemoryBackend)
        assert str(tmp_path / "global_mem") in str(engines["without_path"]._memory._base)

        # They are different instances
        assert engines["with_path"]._memory is not engines["without_path"]._memory
