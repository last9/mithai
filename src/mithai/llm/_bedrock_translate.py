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
                    elif isinstance(raw, list):
                        # Normalize each item to a Bedrock content block.
                        # Anthropic-style {"type": "text", "text": ...} becomes
                        # {"text": ...}; already-shaped Bedrock blocks pass through;
                        # plain strings get wrapped; anything else is coerced to text
                        # so the whole converse call doesn't get rejected.
                        inner = []
                        for item in raw:
                            if isinstance(item, str):
                                inner.append({"text": item})
                            elif isinstance(item, dict):
                                if item.get("type") == "text":
                                    inner.append({"text": item.get("text", "")})
                                elif "text" in item or "json" in item or "image" in item or "document" in item:
                                    inner.append(item)
                                else:
                                    inner.append({"text": str(item)})
                            else:
                                inner.append({"text": str(item)})
                    else:
                        # Unknown shape — stringify so Bedrock accepts it.
                        inner = [{"text": str(raw)}]
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
    """Convert a Bedrock Converse response into the normalized LLMResponse.

    Translates content blocks back to Anthropic-style ({"type", ...}) and
    renames camelCase usage fields to snake_case.

    Reasoning blocks (emitted by reasoning-capable models like Sonnet 4 with
    extended thinking) are surfaced as text so the assistant message is never
    empty — an empty content list would cause Bedrock to reject the next turn
    with ValidationException.
    """
    out_msg = response.get("output", {}).get("message", {})
    raw_blocks = out_msg.get("content", [])
    content: list[dict[str, Any]] = []
    for block in raw_blocks:
        if "text" in block:
            content.append({"type": "text", "text": block["text"]})
        elif "toolUse" in block:
            tu = block["toolUse"]
            content.append(
                {
                    "type": "tool_use",
                    "id": tu["toolUseId"],
                    "name": tu["name"],
                    "input": tu.get("input", {}),
                }
            )
        elif "reasoningContent" in block:
            # Surface the reasoning text so the assistant message has content.
            # Shape: {"reasoningContent": {"reasoningText": {"text": "...", "signature": "..."}}}
            # Known limitation: the `signature` is dropped. If extended thinking
            # is enabled (Anthropic-on-Bedrock), re-submitting a reasoning turn
            # without its signature can be rejected by the model. Full reasoning
            # round-tripping would need a dedicated reasoning block type in
            # LLMResponse rather than flattening to text.
            reasoning_text = (
                block.get("reasoningContent", {}).get("reasoningText", {}).get("text")
            )
            if reasoning_text:
                content.append({"type": "text", "text": reasoning_text})
        # Other unknown block kinds are dropped silently — but the safeguard
        # below guarantees content is never empty.

    # Safeguard: never return empty content. An empty assistant message will
    # cause Bedrock to reject the next turn. If everything was dropped or the
    # response was genuinely empty, emit a placeholder so the engine can
    # continue. The stop_reason tells the engine why generation ended.
    if not content:
        content = [{"type": "text", "text": ""}]

    usage = response.get("usage", {}) or {}
    return LLMResponse(
        content=content,
        stop_reason=response.get("stopReason", ""),
        model=response.get("modelId", ""),
        usage={
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
        },
    )
