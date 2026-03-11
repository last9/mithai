"""Tests for config loading."""

import yaml

from mithai.core.config import load_config, get_skill_paths


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
        "state": {"filesystem": {"path": "${STATE_PATH:-./.engineer9/state}"}},
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
        "state": {"filesystem": {"path": "${STATE_PATH:-./.engineer9/state}"}},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))

    loaded = load_config(config_path)
    assert loaded["state"]["filesystem"]["path"] == "./.engineer9/state"


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
