"""Pure functions translating between Anthropic-style and Bedrock Converse formats.

Kept separate from bedrock.py so the translation logic is testable without
importing boto3.
"""

from typing import Any

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
    """Convert engine messages (Anthropic-style) to Bedrock Converse messages.

    All content becomes a list of blocks (no bare strings):
    - bare text          → [{"text": "..."}]
    - {"type": "text"}   → {"text": "..."}
    - {"type": "tool_use"}    → {"toolUse": {"toolUseId", "name", "input"}}
    - {"type": "tool_result"} → {"toolResult": {"toolUseId", "content": [{"text": ...}]}}
    """
    result = []
    for msg in messages:
        content = msg.get("content", "")
        blocks: list[dict[str, Any]] = []
        if isinstance(content, str):
            blocks.append({"text": content})
        else:
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    blocks.append({"text": block.get("text", "")})
                elif btype == "tool_use":
                    blocks.append(
                        {
                            "toolUse": {
                                "toolUseId": block["id"],
                                "name": block["name"],
                                "input": block.get("input", {}),
                            }
                        }
                    )
                elif btype == "tool_result":
                    raw = block.get("content", "")
                    if isinstance(raw, str):
                        inner = [{"text": raw}]
                    else:
                        inner = raw  # already-shaped blocks pass through
                    blocks.append(
                        {
                            "toolResult": {
                                "toolUseId": block["tool_use_id"],
                                "content": inner,
                            }
                        }
                    )
                else:
                    # Unknown block type — pass through verbatim so failures are visible.
                    blocks.append(block)
        result.append({"role": msg.get("role", "user"), "content": blocks})
    return result


def bedrock_response_to_llm_response(response: dict) -> LLMResponse:
    """Stub — implemented in Task 4."""
    raise NotImplementedError
