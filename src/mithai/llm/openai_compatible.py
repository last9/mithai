"""OpenAI Chat Completions compatible LLM provider."""

from __future__ import annotations

import json
import time
from enum import Enum
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from mithai.llm.base import LLMProvider, LLMResponse


class UnavailableReason(str, Enum):
    """Failures callers may treat as provider unavailability."""

    USAGE_LIMIT = "usage_limit"
    AUTHENTICATION = "authentication"
    OVERLOAD = "overload"


class ProviderError(RuntimeError):
    """A sanitized OpenAI-compatible provider failure."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AvailabilityFailure(ProviderError):
    """A provider failure that explicitly means the configured provider is unavailable."""

    def __init__(self, reason: UnavailableReason, *, status_code: int):
        super().__init__(f"LLM provider unavailable: {reason.value}", status_code=status_code)
        self.reason = reason


_USAGE_LIMIT_CODES = frozenset({
    "billing_hard_limit_reached",
    "credit_balance_too_low",
    "insufficient_quota",
})
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504, 529})


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible Chat Completions API, including OpenAI and xAI."""

    def __init__(
        self,
        api_key: str | None,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        *,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        timeout: float = 600,
    ):
        if not api_key or not api_key.strip():
            raise ValueError("openai_compatible requires a non-blank api_key")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._api_key = api_key
        self._model = model
        self._url = _completion_url(base_url)
        self._max_retries = max_retries
        self._retry_delay = retry_delay
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

        started = time.monotonic()
        tracer = get_tracer()
        if tracer is None:
            response = self._call_api(system, messages, tools, max_tokens)
        else:
            response = self._call_traced(
                tracer, system, messages, tools, max_tokens, call_type, after_tools
            )
        record_token_usage(
            self._model,
            response.usage.get("input_tokens", 0),
            response.usage.get("output_tokens", 0),
            system="openai_compatible",
        )
        record_operation_duration(
            self._model,
            response.stop_reason,
            time.monotonic() - started,
            system="openai_compatible",
        )
        return response

    def _call_traced(
        self, tracer, system, messages, tools, max_tokens, call_type, after_tools
    ) -> LLMResponse:
        from opentelemetry.trace import SpanKind, StatusCode

        with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.system", "openai_compatible")
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
                # ProviderError messages contain classifications only, never response bodies.
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
        if not _is_safe_header_value(self._api_key):
            raise ProviderError("LLM provider credentials are invalid") from None

        payload: dict[str, Any] = {
            "model": self._model,
            _token_limit_field(self._url): max_tokens,
            "messages": [{"role": "system", "content": system}, *_translate_messages(messages)],
        }
        if tools:
            payload["tools"] = [_translate_tool(tool) for tool in tools]
        encoded = json.dumps(payload, separators=(",", ":")).encode()

        for attempt in range(self._max_retries + 1):
            request = Request(
                self._url,
                data=encoded,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                try:
                    raw = urlopen(request, timeout=self._timeout)
                except HTTPError as exc:
                    with exc:
                        body = _read_error(exc)
                    classified = _classify_http_error(exc.code, body)
                    if (
                        isinstance(classified, AvailabilityFailure)
                        and classified.reason is UnavailableReason.USAGE_LIMIT
                    ):
                        raise classified from None
                    if exc.code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                        self._wait(attempt)
                        continue
                    raise classified from None
                with raw:
                    body = json.load(raw)
                return _normalize_response(body)
            except (OSError, HTTPException):
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise ProviderError("LLM provider transport failed") from None
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                raise ProviderError("LLM provider returned an invalid response") from None
        raise AssertionError("retry loop exhausted without returning or raising")

    def _wait(self, attempt: int) -> None:
        if self._retry_delay:
            time.sleep(self._retry_delay * (2**attempt))


def _completion_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("base_url must be an HTTP(S) origin and path without query or fragment")
    path = f"{parsed.path.rstrip('/')}/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _token_limit_field(completion_url: str) -> str:
    if urlsplit(completion_url).hostname == "api.openai.com":
        return "max_completion_tokens"
    return "max_tokens"


def _is_safe_header_value(value: str) -> bool:
    return all(32 <= ord(char) <= 126 for char in value)


def _translate_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _translate_messages(messages: list[dict]) -> list[dict]:
    translated = []
    for message in messages:
        role = message["role"]
        content = message.get("content", "")
        if isinstance(content, str):
            translated.append({"role": role, "content": content})
            continue
        text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
        tool_uses = [block for block in content if block.get("type") == "tool_use"]
        tool_results = [block for block in content if block.get("type") == "tool_result"]
        if role == "assistant" and tool_uses:
            item: dict[str, Any] = {"role": "assistant"}
            if text:
                item["content"] = text
            item["tool_calls"] = [{
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input", {}), separators=(",", ":")),
                },
            } for block in tool_uses]
            translated.append(item)
        elif tool_results:
            translated.extend({
                "role": "tool",
                "tool_call_id": block["tool_use_id"],
                "content": _tool_result_content(block.get("content", "")),
            } for block in tool_results)
            if text:
                translated.append({"role": role, "content": text})
        else:
            translated.append({"role": role, "content": text})
    return translated


def _tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _normalize_response(raw: dict) -> LLMResponse:
    choice = raw["choices"][0]
    message = choice["message"]
    finish_reason = choice.get("finish_reason") or "stop"
    truncated = finish_reason == "length"
    content = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        function = call["function"]
        content.append({
            "type": "tool_use",
            "id": call["id"],
            "name": function["name"],
            "input": {} if truncated else json.loads(function.get("arguments") or "{}"),
        })
    stop_reason = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}.get(
        finish_reason, finish_reason
    )
    usage = raw.get("usage") or {}
    return LLMResponse(
        content=content,
        stop_reason=stop_reason,
        model=raw.get("model", ""),
        usage={
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    )


def _read_error(error: HTTPError) -> dict:
    try:
        parsed = json.loads(error.read())
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _classify_http_error(status: int, body: dict) -> ProviderError:
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = error.get("code")
    if code in _USAGE_LIMIT_CODES:
        return AvailabilityFailure(UnavailableReason.USAGE_LIMIT, status_code=status)
    if status in {401, 403}:
        return AvailabilityFailure(UnavailableReason.AUTHENTICATION, status_code=status)
    if status == 529:
        return AvailabilityFailure(UnavailableReason.OVERLOAD, status_code=status)
    return ProviderError(f"LLM provider request failed with HTTP {status}", status_code=status)
