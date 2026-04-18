"""Tests for post-turn response verifier."""

from unittest.mock import MagicMock

from mithai.core.verifier import verify, verified_skills_called


def _make_llm(text):
    llm = MagicMock()
    llm.create_message.return_value = MagicMock(
        content=[{"type": "text", "text": text}]
    )
    return llm


# ---------------------------------------------------------------------------
# verify() — core fact-check logic
# ---------------------------------------------------------------------------

class TestVerify:
    def test_returns_none_when_llm_says_pass(self):
        llm = _make_llm("PASS")
        result = verify("costs are $142", [{"tool": "aws__get_costs", "result_summary": "Cost MTD: $142.31"}], llm)
        assert result is None

    def test_returns_failure_description_on_fail(self):
        llm = _make_llm("FAIL: response says 2 alarms but tool returned 3")
        result = verify(
            "there are 2 active alarms",
            [{"tool": "aws__get_alarms", "result_summary": "3 alarms in ALARM state"}],
            llm,
        )
        assert result == "response says 2 alarms but tool returned 3"

    def test_returns_none_when_no_tool_calls(self):
        llm = _make_llm("PASS")
        result = verify("hello there", [], llm)
        assert result is None
        llm.create_message.assert_not_called()

    def test_strips_fail_prefix(self):
        llm = _make_llm("FAIL: cost is wrong")
        result = verify("cost is $10", [{"tool": "aws__get_costs", "result_summary": "Cost MTD: $142.31"}], llm)
        assert result == "cost is wrong"
        assert not result.startswith("FAIL")

    def test_handles_pass_with_trailing_whitespace(self):
        llm = _make_llm("  PASS  \n")
        result = verify("all good", [{"tool": "aws__get_metrics", "result_summary": "CPU: 42%"}], llm)
        assert result is None

    def test_llm_prompt_includes_tool_results(self):
        llm = _make_llm("PASS")
        verify(
            "response text",
            [
                {"tool": "aws__get_costs", "result_summary": "Cost MTD: $42.00"},
                {"tool": "aws__get_alarms", "result_summary": "0 alarms"},
            ],
            llm,
        )
        call_kwargs = llm.create_message.call_args
        prompt = call_kwargs.kwargs["messages"][0]["content"]
        assert "aws__get_costs" in prompt
        assert "Cost MTD: $42.00" in prompt
        assert "aws__get_alarms" in prompt
        assert "response text" in prompt

    def test_llm_called_with_small_max_tokens(self):
        """Verifier should use a small token budget — it only needs PASS/FAIL."""
        llm = _make_llm("PASS")
        verify("text", [{"tool": "t", "result_summary": "r"}], llm)
        assert llm.create_message.call_args.kwargs["max_tokens"] <= 150

    def test_handles_llm_exception_gracefully(self):
        """If the verifier LLM call fails, it should not crash the main response."""
        llm = MagicMock()
        llm.create_message.side_effect = Exception("API down")
        result = verify("text", [{"tool": "t", "result_summary": "r"}], llm)
        assert result is None

    def test_handles_empty_llm_response(self):
        llm = MagicMock()
        llm.create_message.return_value = MagicMock(content=[])
        result = verify("text", [{"tool": "t", "result_summary": "r"}], llm)
        assert result is None

    def test_multiple_tool_results_in_prompt(self):
        """All tool results must be included so the LLM can cross-check."""
        llm = _make_llm("PASS")
        tool_calls = [
            {"tool": "aws__get_costs", "result_summary": "Cost MTD: $100"},
            {"tool": "aws__get_alarms", "result_summary": "1 alarm"},
            {"tool": "aws__get_metrics", "result_summary": "CPU p95: 88%"},
        ]
        verify("response", tool_calls, llm)
        prompt = llm.create_message.call_args.kwargs["messages"][0]["content"]
        assert "aws__get_costs" in prompt
        assert "aws__get_alarms" in prompt
        assert "aws__get_metrics" in prompt

    def test_skips_entries_without_result_summary(self):
        """Error/unknown-tool entries have no result_summary — must not raise KeyError."""
        llm = _make_llm("PASS")
        tool_calls = [
            {"tool": "aws__get_costs", "result_summary": "Cost MTD: $100"},
            {"tool": "aws__unknown", "error": "Unknown tool: aws__unknown"},  # no result_summary
        ]
        # Should not raise
        result = verify("response", tool_calls, llm)
        assert result is None
        # Only the entry with a summary should appear in the prompt
        prompt = llm.create_message.call_args.kwargs["messages"][0]["content"]
        assert "aws__get_costs" in prompt
        assert "aws__unknown" not in prompt

    def test_returns_none_when_all_entries_lack_result_summary(self):
        """If no entries have result_summary, nothing to verify — skip LLM call."""
        llm = _make_llm("PASS")
        tool_calls = [
            {"tool": "aws__bad", "error": "Unknown tool"},
        ]
        result = verify("response", tool_calls, llm)
        assert result is None
        llm.create_message.assert_not_called()


# ---------------------------------------------------------------------------
# verified_skills_called() — checks if any verified skill was used this turn
# ---------------------------------------------------------------------------

class TestVerifiedSkillsCalled:
    def test_returns_true_when_verified_skill_present(self):
        tool_calls = [{"tool": "aws__get_costs", "result_summary": "..."}]
        assert verified_skills_called(tool_calls, {"aws"}) is True

    def test_returns_false_when_no_verified_skill(self):
        tool_calls = [{"tool": "shell__run", "result_summary": "..."}]
        assert verified_skills_called(tool_calls, {"aws"}) is False

    def test_returns_false_when_tool_calls_empty(self):
        assert verified_skills_called([], {"aws"}) is False

    def test_returns_false_when_verified_set_empty(self):
        tool_calls = [{"tool": "aws__get_costs", "result_summary": "..."}]
        assert verified_skills_called(tool_calls, set()) is False

    def test_matches_by_skill_prefix(self):
        tool_calls = [{"tool": "kubernetes__get_pods", "result_summary": "..."}]
        assert verified_skills_called(tool_calls, {"kubernetes"}) is True

    def test_multiple_tools_one_verified(self):
        tool_calls = [
            {"tool": "memory__read", "result_summary": "..."},
            {"tool": "aws__get_alarms", "result_summary": "..."},
        ]
        assert verified_skills_called(tool_calls, {"aws"}) is True


# ---------------------------------------------------------------------------
# Engine integration — verifier annotates response when fact-check fails
# ---------------------------------------------------------------------------

class TestEngineVerifierIntegration:
    """Tests that engine applies verifier output to the response text.

    These tests patch `mithai.core.verifier.verify` and
    `mithai.core.verifier.verified_skills_called` directly — that is the
    contract the engine must honour: call those two functions and act on
    the result, without caring about LLM internals.
    """

    def _make_engine(self, llm, verify_skill_names=None, verifier_llm=None):
        """Build engine with pre-built Skill objects that have verify=True/False."""
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path

        skills = {}
        for name in (verify_skill_names or ["aws"]):
            skills[name] = Skill(
                name=name,
                prompt="",
                tools=[],
                handle=lambda n, i, c: "{}",
                source_dir=Path("."),
                verify=True,
            )

        config = {
            "bot": {"system_prompt": ""},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "k", "max_tokens": 1024},
            "skills": {"paths": []},
        }
        if verifier_llm is not None:
            config["verifier"] = {"model": "claude-haiku-4-5-20251001"}

        engine = Engine(config=config, llm=llm, state=MemoryStateBackend(), skills=skills)
        if verifier_llm is not None:
            engine._verifier_llm = verifier_llm
        return engine

    def _plain_llm(self, text="response text"):
        """LLM that returns a simple text response with no tool calls."""
        llm = MagicMock()
        llm.create_message.return_value = MagicMock(
            content=[{"type": "text", "text": text}],
            stop_reason="end_turn",
        )
        return llm

    def _make_msg(self):
        from mithai.adapters.base import IncomingMessage
        return IncomingMessage(
            text="how many alarms?", channel_id="C1", user_id="U1",
            platform="slack", thread_id="t1",
        )

    def _make_adapter(self):
        a = MagicMock()
        a.fetch_thread_context.return_value = None
        return a

    def test_verifier_annotates_response_on_failure(self):
        from unittest.mock import patch

        engine = self._make_engine(self._plain_llm("there are 2 alarms"))

        with patch("mithai.core.engine.verified_skills_called", return_value=True), \
             patch("mithai.core.engine.verify", return_value="response says 2 alarms but tool returned 3"):
            response = engine.handle(self._make_msg(), self._make_adapter())

        assert "⚠️" in response
        assert "2 alarms" in response

    def test_verifier_does_not_annotate_on_pass(self):
        from unittest.mock import patch

        engine = self._make_engine(self._plain_llm("there are 3 alarms"))

        with patch("mithai.core.engine.verified_skills_called", return_value=True), \
             patch("mithai.core.engine.verify", return_value=None):
            response = engine.handle(self._make_msg(), self._make_adapter())

        assert "⚠️" not in response

    def test_verifier_skipped_for_non_verified_skill(self):
        from unittest.mock import patch

        llm = self._plain_llm("done")
        engine = self._make_engine(llm, verify_skill_names=["aws"])

        with patch("mithai.core.engine.verified_skills_called", return_value=False), \
             patch("mithai.core.engine.verify") as mock_verify:
            engine.handle(self._make_msg(), self._make_adapter())

        mock_verify.assert_not_called()

    def test_verify_called_with_tool_results_and_response(self):
        """verify() must receive the actual tool results and final response text."""
        from unittest.mock import patch

        engine = self._make_engine(self._plain_llm("costs are $42"))

        with patch("mithai.core.engine.verified_skills_called", return_value=True), \
             patch("mithai.core.engine.verify", return_value=None) as mock_verify:
            engine.handle(self._make_msg(), self._make_adapter())

        mock_verify.assert_called_once()
        args = mock_verify.call_args
        assert args.args[0] == "costs are $42"   # response_text
        assert isinstance(args.args[1], list)      # tool_calls list
        # llm arg is the engine's verifier llm (defaults to main llm)
        assert args.args[2] is engine._verifier_llm


# ---------------------------------------------------------------------------
# Verifier derives verified skills from VERIFY=True on Skill, not config
# ---------------------------------------------------------------------------

class TestVerifiedSkillsFromSkillTag:
    def test_engine_uses_verify_flag_from_skill(self):
        """Engine must collect verified skill names from Skill.verify, not config."""
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path

        aws_skill = Skill(
            name="aws", prompt="", tools=[], handle=lambda n, i, c: "{}",
            source_dir=Path("."), verify=True,
        )
        shell_skill = Skill(
            name="shell", prompt="", tools=[], handle=lambda n, i, c: "{}",
            source_dir=Path("."), verify=False,
        )

        config = {
            "bot": {"system_prompt": ""},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "k"},
            "skills": {"paths": []},
        }
        engine = Engine(
            config=config, llm=MagicMock(), state=MemoryStateBackend(),
            skills={"aws": aws_skill, "shell": shell_skill},
        )

        assert "aws" in engine._verified_skills
        assert "shell" not in engine._verified_skills

    def test_engine_verified_skills_empty_when_none_tagged(self):
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path

        shell_skill = Skill(
            name="shell", prompt="", tools=[], handle=lambda n, i, c: "{}",
            source_dir=Path("."), verify=False,
        )
        config = {
            "bot": {"system_prompt": ""},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "k"},
            "skills": {"paths": []},
        }
        engine = Engine(
            config=config, llm=MagicMock(), state=MemoryStateBackend(),
            skills={"shell": shell_skill},
        )
        assert engine._verified_skills == set()


# ---------------------------------------------------------------------------
# Verifier uses separate LLM when verifier.model is configured
# ---------------------------------------------------------------------------

class TestVerifierModel:
    def test_engine_uses_verifier_llm_not_main_llm(self):
        """When verifier.model is set, verify() is called with _verifier_llm, not _llm."""
        from mithai.core.engine import Engine
        from mithai.core.skill_loader import Skill
        from mithai.state.memory import MemoryStateBackend
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        from mithai.adapters.base import IncomingMessage

        main_llm = MagicMock()
        main_llm.create_message.return_value = MagicMock(
            content=[{"type": "text", "text": "3 alarms"}], stop_reason="end_turn"
        )

        aws_skill = Skill(
            name="aws", prompt="", tools=[], handle=lambda n, i, c: "{}",
            source_dir=Path("."), verify=True,
        )
        config = {
            "bot": {"system_prompt": ""},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "k"},
            "skills": {"paths": []},
            "verifier": {"model": "claude-haiku-4-5-20251001"},
        }
        engine = Engine(
            config=config, llm=main_llm, state=MemoryStateBackend(),
            skills={"aws": aws_skill},
        )

        assert engine._verifier_llm is not engine._llm

        adapter = MagicMock()
        adapter.fetch_thread_context.return_value = None
        msg = IncomingMessage(
            text="how many alarms?", channel_id="C1", user_id="U1",
            platform="slack", thread_id="t1",
        )

        with patch("mithai.core.engine.verified_skills_called", return_value=True), \
             patch("mithai.core.engine.verify", return_value=None) as mock_verify:
            engine.handle(msg, adapter)

        assert mock_verify.call_args.args[2] is engine._verifier_llm

    def test_engine_uses_main_llm_as_verifier_when_no_model_configured(self):
        """When verifier.model is not set, _verifier_llm falls back to _llm."""
        from mithai.core.engine import Engine
        from mithai.state.memory import MemoryStateBackend

        main_llm = MagicMock()
        config = {
            "bot": {"system_prompt": ""},
            "learning": {"enabled": False},
            "llm": {"provider": "anthropic", "api_key": "k"},
            "skills": {"paths": []},
        }
        engine = Engine(config=config, llm=main_llm, state=MemoryStateBackend(), skills={})
        assert engine._verifier_llm is engine._llm
