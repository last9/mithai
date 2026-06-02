"""Tests for Bedrock format translation — pure functions, no boto3 needed."""

import pytest  # noqa: F401 — used by Tasks 3 and 4 when they add parametrize/raises tests

from mithai.llm._bedrock_translate import (
    anthropic_tools_to_bedrock,
    messages_to_bedrock,
    bedrock_response_to_llm_response,  # noqa: F401 — tested in Task 4
)


def test_anthropic_tools_to_bedrock_basic():
    tools = [
        {
            "name": "kubectl_get",
            "description": "Run kubectl get",
            "input_schema": {"type": "object", "properties": {"resource": {"type": "string"}}},
        }
    ]
    result = anthropic_tools_to_bedrock(tools)
    assert result == {
        "tools": [
            {
                "toolSpec": {
                    "name": "kubectl_get",
                    "description": "Run kubectl get",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {"resource": {"type": "string"}},
                        }
                    },
                }
            }
        ]
    }


def test_anthropic_tools_to_bedrock_empty():
    assert anthropic_tools_to_bedrock([]) == {"tools": []}
    assert anthropic_tools_to_bedrock(None) == {"tools": []}


def test_anthropic_tools_to_bedrock_preserves_multiple():
    tools = [
        {"name": "a", "description": "A", "input_schema": {"type": "object"}},
        {"name": "b", "description": "B", "input_schema": {"type": "object"}},
    ]
    result = anthropic_tools_to_bedrock(tools)
    assert len(result["tools"]) == 2
    assert result["tools"][0]["toolSpec"]["name"] == "a"
    assert result["tools"][1]["toolSpec"]["name"] == "b"


def test_messages_to_bedrock_text_user():
    messages = [{"role": "user", "content": "hello"}]
    result = messages_to_bedrock(messages)
    assert result == [{"role": "user", "content": [{"text": "hello"}]}]


def test_messages_to_bedrock_tool_use():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me check"},
                {"type": "tool_use", "id": "tu_1", "name": "kubectl_get", "input": {"resource": "pods"}},
            ],
        }
    ]
    result = messages_to_bedrock(messages)
    assert result == [
        {
            "role": "assistant",
            "content": [
                {"text": "let me check"},
                {"toolUse": {"toolUseId": "tu_1", "name": "kubectl_get", "input": {"resource": "pods"}}},
            ],
        }
    ]


def test_messages_to_bedrock_tool_result():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "5 pods found"},
            ],
        }
    ]
    result = messages_to_bedrock(messages)
    assert result == [
        {
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": "tu_1", "content": [{"text": "5 pods found"}]}},
            ],
        }
    ]


def test_messages_to_bedrock_multi_turn():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        {"role": "user", "content": "check pods"},
    ]
    result = messages_to_bedrock(messages)
    assert len(result) == 3
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"
    assert result[2]["content"] == [{"text": "check pods"}]


def test_bedrock_response_text_only():
    resp = {
        "output": {"message": {"role": "assistant", "content": [{"text": "hello"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 5, "outputTokens": 1},
        "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
    }
    result = bedrock_response_to_llm_response(resp)
    assert result.content == [{"type": "text", "text": "hello"}]
    assert result.stop_reason == "end_turn"
    assert result.model == "anthropic.claude-sonnet-4-20250514-v1:0"
    assert result.usage == {"input_tokens": 5, "output_tokens": 1}


def test_bedrock_response_tool_use():
    resp = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "checking"},
                    {"toolUse": {"toolUseId": "tu_1", "name": "kubectl_get", "input": {"resource": "pods"}}},
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 8, "outputTokens": 12},
        "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
    }
    result = bedrock_response_to_llm_response(resp)
    assert result.content == [
        {"type": "text", "text": "checking"},
        {"type": "tool_use", "id": "tu_1", "name": "kubectl_get", "input": {"resource": "pods"}},
    ]
    assert result.stop_reason == "tool_use"


def test_bedrock_response_missing_usage_defaults_to_zero():
    resp = {
        "output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}},
        "stopReason": "end_turn",
        "modelId": "x",
    }
    result = bedrock_response_to_llm_response(resp)
    assert result.usage == {"input_tokens": 0, "output_tokens": 0}
