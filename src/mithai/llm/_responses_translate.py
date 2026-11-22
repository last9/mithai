"""Translate mithai's normalized messages to and from the OpenAI Responses API.

Requests are stateless (``store: false``): reasoning items come back with
``encrypted_content`` and are kept in the assistant message as ``reasoning`` blocks, so the
next turn can replay them in their original position before the function calls they led to.
"""

from __future__ import annotations

import json
from typing import Any

from mithai.llm.base import LLMResponse


def build_request(
    *,
    model: str,
    system: str,
    messages: list[dict],
    tools: list[dict] | None,
    max_tokens: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "instructions": system,
        "max_output_tokens": max_tokens,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    payload["input"] = [item for message in messages for item in _input_items(message)]
    if tools:
        payload["tools"] = [{
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        } for tool in tools]
    return payload


def _input_items(message: dict) -> list[dict]:
    role = message["role"]
    content = message.get("content", "")
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if role == "assistant":
        return _assistant_items(content)
    # Function outputs must directly follow the calls they answer, ahead of any user text.
    items: list[dict] = [{
        "type": "function_call_output",
        "call_id": block["tool_use_id"],
        "output": tool_result_text(block.get("content", "")),
    } for block in content if block.get("type") == "tool_result"]
    if any(block.get("type") == "image" for block in content):
        items.append({"role": role, "content": [
            part for part in map(_input_part, content) if part is not None
        ]})
        return items
    text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
    if text or not items:
        items.append({"role": role, "content": text})
    return items


def _input_part(block: dict) -> dict | None:
    kind = block.get("type")
    if kind == "text":
        return {"type": "input_text", "text": block.get("text", "")}
    if kind == "image" and block["source"]["type"] == "base64":
        source = block["source"]
        return {
            "type": "input_image",
            "image_url": f"data:{source['media_type']};base64,{source['data']}",
        }
    return None


def _assistant_items(content: list[dict]) -> list[dict]:
    items = []
    for block in content:
        kind = block.get("type")
        if kind == "reasoning":
            items.append({
                "type": "reasoning",
                "id": block["id"],
                "summary": block.get("summary", []),
                "encrypted_content": block.get("encrypted_content"),
            })
        elif kind == "text" and block.get("text"):
            items.append({"role": "assistant", "content": block["text"]})
        elif kind == "tool_use":
            items.append({
                "type": "function_call",
                "call_id": block["id"],
                "name": block["name"],
                "arguments": json.dumps(block.get("input", {}), separators=(",", ":")),
            })
    return items


def tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def normalize_response(raw: dict) -> LLMResponse:
    status = raw.get("status") or "completed"
    if status not in {"completed", "incomplete"}:
        raise ValueError(f"unexpected response status {status!r}")
    incomplete_reason = (raw.get("incomplete_details") or {}).get("reason")
    truncated = status == "incomplete" and incomplete_reason == "max_output_tokens"
    content: list[dict[str, Any]] = []
    for item in raw["output"]:
        kind = item["type"]
        if kind == "reasoning":
            content.append({
                "type": "reasoning",
                "id": item["id"],
                "summary": item.get("summary") or [],
                "encrypted_content": item.get("encrypted_content"),
            })
        elif kind == "message":
            text = "".join(
                part.get("text") or part.get("refusal") or "" for part in item["content"]
            )
            if text:
                content.append({"type": "text", "text": text})
        elif kind == "function_call":
            content.append({
                "type": "tool_use",
                "id": item["call_id"],
                "name": item["name"],
                "input": {} if truncated else json.loads(item.get("arguments") or "{}"),
            })

    if truncated:
        stop_reason = "max_tokens"
    elif status == "incomplete":
        stop_reason = incomplete_reason or "incomplete"
    elif any(block["type"] == "tool_use" for block in content):
        stop_reason = "tool_use"
    else:
        stop_reason = "end_turn"

    usage = raw.get("usage") or {}
    return LLMResponse(
        content=content,
        stop_reason=stop_reason,
        model=raw.get("model", ""),
        usage=normalize_usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached_input_tokens=(usage.get("input_tokens_details") or {}).get("cached_tokens"),
            reasoning_tokens=(usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
        ),
    )


def normalize_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int | None,
    reasoning_tokens: int | None,
) -> dict[str, int]:
    """Shared usage shape for both OpenAI APIs; detail counts appear only when reported."""
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if cached_input_tokens is not None:
        usage["cached_input_tokens"] = cached_input_tokens
    if reasoning_tokens is not None:
        usage["reasoning_tokens"] = reasoning_tokens
    return usage
