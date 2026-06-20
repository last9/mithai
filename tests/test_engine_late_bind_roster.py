"""Tests for Engine.late_bind roster injection into Slack adapters (U4)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from mithai.core.engine import Engine


def _bare_engine(memory):
    """Engine with just the attributes late_bind needs (bypasses heavy __init__)."""
    eng = Engine.__new__(Engine)
    eng._memory = memory
    eng._skills = {}
    return eng


# ---------------------------------------------------------------------------
# _parse_roster_pairs — tolerant of roster format
# ---------------------------------------------------------------------------

def test_parse_roster_pairs_parens():
    text = "## Alice Example (U012ALICE)\n**Bob** (U012BOB)"
    pairs = Engine._parse_roster_pairs(text)
    assert pairs["Alice Example"] == "U012ALICE"
    assert pairs["Nishant"] == "U012BOB"


def test_parse_roster_pairs_table():
    text = "| Slack ID | Name | Role |\n| U012CAROL | Carol | Lead |"
    pairs = Engine._parse_roster_pairs(text)
    assert pairs["Carol"] == "U012CAROL"


def test_parse_roster_pairs_dash():
    text = "**Dana Example** - U012DANA"
    pairs = Engine._parse_roster_pairs(text)
    assert pairs["Dana Example"] == "U012DANA"


def test_parse_roster_pairs_empty():
    assert Engine._parse_roster_pairs("") == {}
    assert Engine._parse_roster_pairs(None) == {}
    assert Engine._parse_roster_pairs("no slack ids in this text") == {}


# ---------------------------------------------------------------------------
# late_bind injection
# ---------------------------------------------------------------------------

def test_late_bind_injects_roster_into_slack_adapter():
    memory = MagicMock()
    memory.read.return_value = "**Alice** (REDACTED_SLACK_USER_ID)"
    eng = _bare_engine(memory)
    client = MagicMock()
    slack_adapter = SimpleNamespace(slack_client=client)

    eng.late_bind([("slack", slack_adapter)])

    client.set_roster_fallback.assert_called_once()
    pairs = client.set_roster_fallback.call_args[0][0]
    assert pairs["Alice"] == "REDACTED_SLACK_USER_ID"


def test_late_bind_skips_non_slack_adapter():
    memory = MagicMock()
    memory.read.return_value = "**X** (REDACTED_SLACK_USER_ID)"
    eng = _bare_engine(memory)
    cli_adapter = SimpleNamespace()  # no slack_client attribute

    # Must not raise; nothing to inject into.
    eng.late_bind([("cli", cli_adapter)])


def test_late_bind_empty_roster_is_noop_call():
    memory = MagicMock()
    memory.read.return_value = None
    eng = _bare_engine(memory)
    client = MagicMock()
    adapter = SimpleNamespace(slack_client=client)

    eng.late_bind([("slack", adapter)])

    client.set_roster_fallback.assert_called_once_with({})


def test_late_bind_only_slack_adapters_get_call():
    memory = MagicMock()
    memory.read.return_value = "**Alice** (REDACTED_SLACK_USER_ID)"
    eng = _bare_engine(memory)
    slack_client = MagicMock()
    slack_adapter = SimpleNamespace(slack_client=slack_client)
    cli_adapter = SimpleNamespace()

    eng.late_bind([("cli", cli_adapter), ("slack", slack_adapter)])

    slack_client.set_roster_fallback.assert_called_once()
