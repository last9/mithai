"""Tests for config loading."""

import os
import yaml

from mithai.core.config import load_config, get_skill_paths, get_llm_config


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


def test_missing_config():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")


def test_get_skill_paths():
    config = {"skills": {"paths": ["./skills", "/opt/mithai/skills"]}}
    paths = get_skill_paths(config)
    assert len(paths) == 2


def test_get_skill_paths_default():
    paths = get_skill_paths({})
    assert len(paths) == 1
    assert str(paths[0]) == "skills"
