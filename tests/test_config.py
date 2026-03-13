"""Tests for config loading."""

import yaml

from mithai.core.config import load_config, get_skill_paths
from mithai.cli.run_cmd import _parse_id_list


def test_load_config(tmp_path):
    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    loaded = load_config(config_path)
    assert loaded["adapter"]["type"] == "cli"
    assert loaded["llm"]["provider"] == "anthropic"


def test_env_var_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test-123")

    config = {
        "adapter": {"type": "cli"},
        "llm": {
            "provider": "anthropic",
            "anthropic": {"api_key": "${TEST_API_KEY}"},
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    loaded = load_config(config_path)
    assert loaded["llm"]["anthropic"]["api_key"] == "sk-test-123"


def test_env_var_default_syntax_with_var_set(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_PATH", "/app/data/state")

    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic"},
        "state": {"filesystem": {"path": "${STATE_PATH:-./.REDACTED_INTERNAL_CHANNEL/state}"}},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    loaded = load_config(config_path)
    assert loaded["state"]["filesystem"]["path"] == "/app/data/state"


def test_env_var_default_syntax_with_var_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("STATE_PATH", raising=False)

    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic"},
        "state": {"filesystem": {"path": "${STATE_PATH:-./.REDACTED_INTERNAL_CHANNEL/state}"}},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    loaded = load_config(config_path)
    assert loaded["state"]["filesystem"]["path"] == "./.REDACTED_INTERNAL_CHANNEL/state"


def test_missing_config():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")


def test_get_skill_paths():
    config = {"skills": {"paths": ["./skills", "/opt/mithai/skills"]}}
    paths = get_skill_paths(config)
    # Includes: bundled path + config paths + user ~/.mithai/skills/ (if exists)
    path_strs = [str(p) for p in paths]
    assert any("/opt/mithai/skills" in s for s in path_strs)


def test_get_skill_paths_default():
    paths = get_skill_paths({})
    # At minimum includes bundled skills path and default ./skills
    assert len(paths) >= 1


def test_schema_valid_minimal_config(tmp_path):
    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    loaded = load_config(config_path)
    assert loaded["adapter"]["type"] == "cli"


def test_schema_unknown_top_level_key_allowed(tmp_path):
    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic"},
        "unknown_future_key": {"foo": "bar"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    # Should not raise
    loaded = load_config(config_path)
    assert loaded["unknown_future_key"]["foo"] == "bar"


def test_schema_wrong_type_max_tokens(tmp_path):
    import pytest
    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic", "max_tokens": "notanint"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    with pytest.raises(ValueError, match=r"llm -> max_tokens"):
        load_config(config_path)


def test_schema_wrong_type_heartbeat_enabled(tmp_path):
    import pytest
    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic"},
        "heartbeat": {"enabled": "yes_please"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    with pytest.raises(ValueError, match=r"heartbeat -> enabled"):
        load_config(config_path)


# ---------------------------------------------------------------------------
# Regression: multi-agent config must load without top-level adapter/llm
#
# `_validate_config` and MithaiConfig schema must not reject configs where
# `adapter` / `llm` are absent at the top level — multi-agent configs define
# adapters under `agents.<id>.adapter` and may define llm per-agent.
# ---------------------------------------------------------------------------

def test_load_config_multi_agent_without_top_level_adapter(tmp_path):
    """A valid multi-agent config has no top-level 'adapter' — must load cleanly."""
    config = {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "agents": {
            "ops": {
                "adapter": {"slack": {"bot_token": "xoxb-ops", "app_token": "xapp-ops"}},
            },
            "dev": {
                "adapter": {"slack": {"bot_token": "xoxb-dev", "app_token": "xapp-dev"}},
            },
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    loaded = load_config(config_path)
    assert "agents" in loaded
    assert "ops" in loaded["agents"]


def test_load_config_multi_agent_without_top_level_llm(tmp_path):
    """A valid multi-agent config may omit top-level 'llm' — must load cleanly."""
    config = {
        "agents": {
            "ops": {
                "adapter": {"slack": {"bot_token": "xoxb-ops", "app_token": "xapp-ops"}},
                "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            },
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    loaded = load_config(config_path)
    assert "agents" in loaded


# ---------------------------------------------------------------------------
# _parse_id_list — comma-separated env var coercion
# ---------------------------------------------------------------------------

class TestParseIdList:
    def test_none_returns_none(self):
        assert _parse_id_list(None) is None

    def test_list_returned_as_is(self):
        assert _parse_id_list(["C1", "C2"]) == ["C1", "C2"]

    def test_comma_separated_string_split(self):
        assert _parse_id_list("C123,C456,C789") == ["C123", "C456", "C789"]

    def test_trims_whitespace(self):
        assert _parse_id_list("C1, C2 , C3") == ["C1", "C2", "C3"]

    def test_single_id_string_wrapped_in_list(self):
        assert _parse_id_list("C123") == ["C123"]

    def test_empty_string_returns_empty_list(self):
        assert _parse_id_list("") == []

    def test_only_commas_returns_empty_list(self):
        assert _parse_id_list(",,,") == []

    def test_mixed_whitespace_and_commas(self):
        assert _parse_id_list("  C1  ,  C2  ") == ["C1", "C2"]

    def test_schema_accepts_comma_string_for_allowed_channels(self, tmp_path, monkeypatch):
        """Schema validation must not reject a comma-separated string for allowed_channels.

        When ALLOWED_CHANNELS=C1,C2 is set, _resolve_env_vars produces the string
        "C1,C2" before _parse_id_list() can coerce it. The Pydantic schema must accept
        str so load_config() doesn't raise before coercion ever runs.
        """
        monkeypatch.setenv("ALLOWED_CHANNELS", "C1,C2,C3")

        config = {
            "adapter": {
                "type": "slack",
                "slack": {
                    "bot_token": "xoxb-test",
                    "app_token": "xapp-test",
                    "allowed_channels": "${ALLOWED_CHANNELS}",
                },
            },
            "llm": {"provider": "anthropic"},
        }
        config_path = tmp_path / "config.yaml"
        import yaml as _yaml
        config_path.write_text(_yaml.dump(config))

        # Must not raise a schema validation error
        loaded = load_config(config_path)
        raw = loaded["adapter"]["slack"]["allowed_channels"]
        assert _parse_id_list(raw) == ["C1", "C2", "C3"]

    def test_unresolved_placeholder_raises(self):
        """An unset env var leaves the placeholder literal — must raise, not silently allow it."""
        import pytest
        with pytest.raises(ValueError, match=r"Unresolved env var"):
            _parse_id_list("${ALLOWED_CHANNELS}")

    def test_env_var_simulation(self, monkeypatch, tmp_path):
        """End-to-end: ALLOWED_CHANNELS=C1,C2 in env → list after config load."""
        monkeypatch.setenv("ALLOWED_CHANNELS", "C1,C2,C3")

        config = {
            "adapter": {"type": "cli"},
            "llm": {"provider": "anthropic"},
            "adapter_slack": {"allowed_channels": "${ALLOWED_CHANNELS}"},
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config))

        loaded = load_config(config_path)
        # After env var resolution the value is a comma-separated string;
        # _parse_id_list must turn it into a proper list.
        raw = loaded["adapter_slack"]["allowed_channels"]
        assert _parse_id_list(raw) == ["C1", "C2", "C3"]
