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


def test_create_llm_bedrock_plumbs_session_token():
    """llm.bedrock.session_token must reach the provider (temporary STS creds)."""
    config = {
        "llm": {
            "provider": "bedrock",
            "model": "x",
            "bedrock": {
                "access_key_id": "AKIA-test",
                "secret_access_key": "secret",
                "region": "us-east-1",
                "session_token": "token-123",
            },
        }
    }
    p = _create_llm(config)
    assert p._session_token == "token-123"


def test_create_llm_unknown_provider_raises():
    import click

    config = {"llm": {"provider": "gemini", "model": "x"}}
    with pytest.raises(click.ClickException) as exc_info:
        _create_llm(config)
    assert "Unknown LLM provider: gemini" in str(exc_info.value.message)


def test_create_llm_bedrock_missing_keys_raises_clickexception():
    """Missing bedrock config keys must produce a clean ClickException, not KeyError."""
    import click

    config = {
        "llm": {
            "provider": "bedrock",
            "model": "x",
            "bedrock": {
                # access_key_id and region intentionally missing
                "secret_access_key": "y",
            },
        }
    }
    with pytest.raises(click.ClickException) as exc_info:
        _create_llm(config)
    msg = str(exc_info.value.message)
    assert "bedrock provider requires" in msg
    assert "access_key_id" in msg
    assert "region" in msg


@pytest.mark.parametrize(
    ("base_url", "model"),
    [
        ("https://api.openai.com/v1", "gpt-5.1"),
        ("https://api.x.ai/v1", "grok-4-fast"),
    ],
)
def test_create_llm_openai_compatible(base_url, model):
    config = {
        "llm": {
            "provider": "openai_compatible",
            "model": model,
            "openai_compatible": {"api_key": "key", "base_url": base_url},
        }
    }
    provider = _create_llm(config)
    assert provider._base_url == base_url
    assert provider._model == model


@pytest.mark.parametrize("api_key", [None, "", "${OPENAI_API_KEY}"])
def test_create_llm_openai_compatible_missing_optional_key_is_not_configured(api_key):
    import click

    config = {
        "llm": {
            "provider": "openai_compatible",
            "model": "gpt-5.1",
            "openai_compatible": {
                "api_key": api_key,
                "base_url": "https://api.openai.com/v1",
            },
        }
    }
    with pytest.raises(click.ClickException, match="not configured"):
        _create_llm(config)


def test_create_llm_openai_compatible_requires_explicit_base_url():
    import click

    config = {
        "llm": {
            "provider": "openai_compatible",
            "model": "gpt-5.1",
            "openai_compatible": {"api_key": "key"},
        }
    }
    with pytest.raises(click.ClickException, match="base_url"):
        _create_llm(config)


def test_create_llm_openai_compatible_requires_explicit_model():
    import click

    config = {
        "llm": {
            "provider": "openai_compatible",
            "openai_compatible": {
                "api_key": "key",
                "base_url": "https://api.openai.com/v1",
            },
        }
    }
    with pytest.raises(click.ClickException, match="model"):
        _create_llm(config)
