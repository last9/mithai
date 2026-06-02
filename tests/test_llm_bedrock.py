"""Tests for BedrockProvider — boto3 client is mocked, no real AWS calls."""

from unittest.mock import MagicMock

from mithai.llm.bedrock import BedrockProvider


def _stub_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.converse.return_value = response
    return client


def test_bedrock_provider_instantiates_with_creds():
    """Construction should not raise — boto3 client is created lazily on first call."""
    p = BedrockProvider(
        access_key_id="AKIA-test",
        secret_access_key="secret-test",
        region="us-east-1",
        model="anthropic.claude-sonnet-4-20250514-v1:0",
    )
    assert p._model == "anthropic.claude-sonnet-4-20250514-v1:0"
    assert p._region == "us-east-1"


def test_bedrock_provider_create_message_text_only():
    client = _stub_client(
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "hello"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 5, "outputTokens": 1},
            "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
        }
    )
    p = BedrockProvider(
        access_key_id="x", secret_access_key="y", region="us-east-1",
        model="anthropic.claude-sonnet-4-20250514-v1:0",
    )
    p._client = client  # inject mock

    response = p.create_message(
        system="you are helpful",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
    )
    assert response.content == [{"type": "text", "text": "hello"}]
    assert response.stop_reason == "end_turn"

    # Verify the call shape
    client.converse.assert_called_once()
    kwargs = client.converse.call_args.kwargs
    assert kwargs["modelId"] == "anthropic.claude-sonnet-4-20250514-v1:0"
    assert kwargs["system"] == [{"text": "you are helpful"}]
    assert kwargs["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert kwargs["inferenceConfig"] == {"maxTokens": 100}
    assert "toolConfig" not in kwargs  # no tools → no toolConfig key


def test_bedrock_provider_passes_tools():
    client = _stub_client(
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1},
            "modelId": "x",
        }
    )
    p = BedrockProvider(
        access_key_id="x", secret_access_key="y", region="us-east-1", model="x",
    )
    p._client = client

    p.create_message(
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "t", "description": "T", "input_schema": {"type": "object"}}],
        max_tokens=10,
    )
    kwargs = client.converse.call_args.kwargs
    assert kwargs["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "t",
                    "description": "T",
                    "inputSchema": {"json": {"type": "object"}},
                }
            }
        ]
    }


def test_bedrock_provider_returns_tool_use_response():
    client = _stub_client(
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"toolUse": {"toolUseId": "tu_1", "name": "kubectl_get", "input": {"r": "p"}}},
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 1, "outputTokens": 5},
            "modelId": "x",
        }
    )
    p = BedrockProvider(
        access_key_id="x", secret_access_key="y", region="us-east-1", model="x",
    )
    p._client = client

    response = p.create_message(
        system="s",
        messages=[{"role": "user", "content": "check"}],
        tools=[{"name": "kubectl_get", "description": "", "input_schema": {"type": "object"}}],
    )
    assert response.stop_reason == "tool_use"
    assert response.content == [
        {"type": "tool_use", "id": "tu_1", "name": "kubectl_get", "input": {"r": "p"}}
    ]


def test_bedrock_provider_telemetry_uses_aws_bedrock_system(monkeypatch):
    """Token usage and operation duration must be recorded with system=aws.bedrock,
    not anthropic — otherwise Bedrock traffic mislabels as Anthropic in dashboards."""
    captured_systems = []

    def fake_record_token_usage(model, in_tokens, out_tokens, system="anthropic"):
        captured_systems.append(("tokens", system))

    def fake_record_operation_duration(model, finish_reason, duration_s, system="anthropic"):
        captured_systems.append(("duration", system))

    monkeypatch.setattr(
        "mithai.telemetry.metrics.record_token_usage", fake_record_token_usage
    )
    monkeypatch.setattr(
        "mithai.telemetry.metrics.record_operation_duration", fake_record_operation_duration
    )

    client = _stub_client(
        {
            "output": {"message": {"role": "assistant", "content": [{"text": "hi"}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1},
            "modelId": "x",
        }
    )
    p = BedrockProvider(
        access_key_id="x", secret_access_key="y", region="us-east-1", model="x",
    )
    p._client = client

    p.create_message(system="s", messages=[{"role": "user", "content": "hi"}], max_tokens=10)

    assert ("tokens", "aws.bedrock") in captured_systems, captured_systems
    assert ("duration", "aws.bedrock") in captured_systems, captured_systems


def test_bedrock_provider_rejects_empty_creds():
    """Empty/whitespace creds must fail at construction, not at first call."""
    import pytest

    for kwargs in [
        {"access_key_id": "", "secret_access_key": "y", "region": "us-east-1", "model": "x"},
        {"access_key_id": "  ", "secret_access_key": "y", "region": "us-east-1", "model": "x"},
        {"access_key_id": "x", "secret_access_key": "", "region": "us-east-1", "model": "x"},
        {"access_key_id": "x", "secret_access_key": "y", "region": "", "model": "x"},
        {"access_key_id": "x", "secret_access_key": "y", "region": "us-east-1", "model": ""},
    ]:
        with pytest.raises(ValueError, match="BedrockProvider requires non-empty"):
            BedrockProvider(**kwargs)


def test_bedrock_provider_wraps_boto3_clienterror():
    """boto3 ClientError / EndpointConnectionError must be wrapped as RuntimeError."""
    import pytest

    class FakeBotoError(Exception):
        pass

    client = MagicMock()
    client.converse.side_effect = FakeBotoError("ValidationException: Invalid model id")

    p = BedrockProvider(
        access_key_id="x", secret_access_key="y", region="us-east-1", model="bad-model",
    )
    p._client = client

    with pytest.raises(RuntimeError, match="bedrock converse failed for model bad-model"):
        p.create_message(system="s", messages=[{"role": "user", "content": "hi"}])
