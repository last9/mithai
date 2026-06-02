"""Tests for _create_llm provider dispatch."""

import pytest

from mithai.cli.run_cmd import _create_llm


def test_create_llm_anthropic():
    config = {
        "llm": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "anthropic": {"api_key": "sk-test"},
        }
    }
    p = _create_llm(config)
    assert type(p).__name__ == "AnthropicProvider"


def test_create_llm_bedrock():
    config = {
        "llm": {
            "provider": "bedrock",
            "model": "anthropic.claude-sonnet-4-20250514-v1:0",
            "bedrock": {
                "access_key_id": "AKIA-test",
                "secret_access_key": "secret",
                "region": "us-east-1",
            },
        }
    }
    p = _create_llm(config)
    assert type(p).__name__ == "BedrockProvider"


def test_create_llm_unknown_provider_raises():
    import click

    config = {"llm": {"provider": "gemini", "model": "x"}}
    with pytest.raises(click.ClickException) as exc_info:
        _create_llm(config)
    assert "Unknown LLM provider: gemini" in str(exc_info.value.message)
