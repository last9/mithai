"""Tests for Bedrock format translation — pure functions, no boto3 needed."""

import pytest  # noqa: F401 — used by Tasks 3 and 4 when they add parametrize/raises tests

from mithai.llm._bedrock_translate import (
    anthropic_tools_to_bedrock,
    messages_to_bedrock,  # noqa: F401 — tested in Task 3
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
