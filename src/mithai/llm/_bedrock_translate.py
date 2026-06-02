"""Pure functions translating between Anthropic-style and Bedrock Converse formats.

Kept separate from bedrock.py so the translation logic is testable without
importing boto3.
"""

from typing import Any  # noqa: F401 — used by Tasks 3 and 4

from mithai.llm.base import LLMResponse


def anthropic_tools_to_bedrock(tools: list[dict] | None) -> dict:
    """Convert Anthropic-style tool definitions to Bedrock toolConfig shape.

    Anthropic: [{"name", "description", "input_schema"}]
    Bedrock:   {"tools": [{"toolSpec": {"name", "description", "inputSchema": {"json": ...}}}]}
    """
    if not tools:
        return {"tools": []}
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "inputSchema": {"json": t["input_schema"]},
                }
            }
            for t in tools
        ]
    }


def messages_to_bedrock(messages: list[dict]) -> list[dict]:
    """Stub — implemented in Task 3."""
    raise NotImplementedError


def bedrock_response_to_llm_response(response: dict) -> LLMResponse:
    """Stub — implemented in Task 4."""
    raise NotImplementedError
