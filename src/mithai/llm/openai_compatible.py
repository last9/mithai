"""Provider for OpenAI-compatible chat-completions APIs."""

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mithai.llm.base import (
    LLMProvider,
    LLMResponse,
    ProviderResponseError,
    ProviderUnavailableError,
)


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI chat completions over an explicitly configured API endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        timeout: float = 60,
    ):
        missing = [
            name
            for name, value in (("api_key", api_key), ("base_url", base_url), ("model", model))
            if not value or not value.strip()
        ]
        if missing:
            raise ValueError("OpenAICompatibleProvider requires non-empty: " + ", ".join(missing))
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_retries = max(0, max_retries)
        self._retry_backoff = max(0, retry_backoff)
        self._timeout = timeout

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        call_type: str = "initial",
        after_tools: list[str] | None = None,
    ) -> LLMResponse:
        from mithai.telemetry import get_tracer
        from mithai.telemetry.metrics import record_operation_duration, record_token_usage

        tracer = get_tracer()
        started = time.monotonic()
        if tracer is None:
            response = self._call_api(system, messages, tools, max_tokens)
        else:
            response = self._call_traced(
                tracer, system, messages, tools, max_tokens, call_type, after_tools
            )
        elapsed = time.monotonic() - started
        record_token_usage(
            self._model,
            response.usage.get("input_tokens", 0),
            response.usage.get("output_tokens", 0),
            system="openai",
        )
        record_operation_duration(
            self._model, response.stop_reason, elapsed, system="openai"
        )
        return response

    def _call_traced(
        self, tracer, system, messages, tools, max_tokens, call_type, after_tools
    ) -> LLMResponse:
        from opentelemetry.trace import SpanKind, StatusCode

        with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.system", "openai")
            span.set_attribute("gen_ai.request.model", self._model)
            span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            span.set_attribute("llm.message_count", len(messages))
            span.set_attribute("gen_ai.call.type", call_type)
            if after_tools:
                span.set_attribute("gen_ai.call.after_tools", after_tools)
            if tools:
                span.set_attribute("llm.tool_count", len(tools))
            try:
                response = self._call_api(system, messages, tools, max_tokens)
            except Exception as exc:
                # Provider exceptions contain only status/category, never keys or bodies.
                span.set_status(StatusCode.ERROR, str(exc))
                span.record_exception(exc)
                raise
            span.set_attribute("gen_ai.response.model", response.model)
            span.set_attribute("gen_ai.response.finish_reasons", [response.stop_reason])
            span.set_attribute("gen_ai.usage.input_tokens", response.usage["input_tokens"])
            span.set_attribute("gen_ai.usage.output_tokens", response.usage["output_tokens"])
            span.set_status(StatusCode.OK)
            return response

    def _call_api(self, system, messages, tools, max_tokens) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": _messages_to_openai(system, messages),
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [_tool_to_openai(tool) for tool in tools]
        request = Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        attempts = self._max_retries + 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=self._timeout) as raw:
                    return _response_from_openai(json.load(raw))
            except HTTPError as exc:
                # Consume and discard the response so neither it nor the request key
                # can escape through exception messages, logs, or telemetry.
                exc.read()
                if exc.code in {401, 403, 408, 409, 429} or exc.code >= 500:
                    if exc.code >= 500 and attempt + 1 < attempts:
                        time.sleep(self._retry_backoff * (2**attempt))
                        continue
                    raise ProviderUnavailableError(
                        f"OpenAI-compatible provider unavailable (HTTP {exc.code})"
                    ) from None
                raise ProviderResponseError(
                    f"OpenAI-compatible provider rejected request (HTTP {exc.code})"
                ) from None
            except (URLError, TimeoutError, OSError):
                if attempt + 1 < attempts:
                    time.sleep(self._retry_backoff * (2**attempt))
                    continue
                raise ProviderUnavailableError(
                    "OpenAI-compatible provider unavailable (transport error)"
                ) from None
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ProviderResponseError(
                    f"OpenAI-compatible provider returned an invalid response ({_safe_reason(exc)})"
                ) from None
        raise AssertionError("unreachable")


def _safe_reason(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "invalid JSON"
    if isinstance(exc, ValueError) and str(exc) == "invalid tool arguments":
        return "invalid tool arguments"
    return "unexpected shape"


def _tool_to_openai(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {}),
        },
    }


def _messages_to_openai(system: str, messages: list[dict]) -> list[dict]:
    result = [{"role": "system", "content": system}] if system else []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        content_parts: list[dict[str, Any]] = []
        tool_calls: list[dict] = []
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                content_parts.append({"type": "text", "text": block.get("text", "")})
            elif block_type == "image":
                source = block.get("source") or {}
                if source.get("type") != "base64" or not all(
                    source.get(key) for key in ("media_type", "data")
                ):
                    raise ValueError("unsupported image source")
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{source['media_type']};base64,{source['data']}"
                    },
                })
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif block_type == "tool_result":
                result.append({
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": str(block.get("content", "")),
                })
        if tool_calls:
            converted: dict[str, Any] = {"role": "assistant", "tool_calls": tool_calls}
            text = "\n".join(
                part["text"] for part in content_parts if part["type"] == "text"
            )
            if text:
                converted["content"] = text
            result.append(converted)
        elif content_parts:
            if all(part["type"] == "text" for part in content_parts):
                converted_content: str | list[dict[str, Any]] = "\n".join(
                    part["text"] for part in content_parts
                )
            else:
                converted_content = content_parts
            result.append({"role": role, "content": converted_content})
    return result


def _response_from_openai(raw: dict) -> LLMResponse:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise KeyError("choices")
    choice = choices[0]
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            raise ValueError("invalid tool arguments") from None
        if not isinstance(arguments, dict):
            raise ValueError("invalid tool arguments")
        content.append({
            "type": "tool_use",
            "id": call["id"],
            "name": function["name"],
            "input": arguments,
        })

    finish_reason = choice.get("finish_reason")
    stop_reason = "tool_use" if finish_reason == "tool_calls" else (
        "max_tokens" if finish_reason == "length" else "end_turn"
    )
    usage = raw.get("usage") or {}
    return LLMResponse(
        content=content,
        stop_reason=stop_reason,
        model=raw.get("model") or "",
        usage={
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
        },
    )
