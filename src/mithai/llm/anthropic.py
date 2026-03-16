"""Anthropic Claude LLM provider."""

import json
import logging
import time

import anthropic

from mithai.llm.base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class AnthropicProvider(LLMProvider):
    """Claude via the Anthropic API."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

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
        t0 = time.monotonic()

        if tracer is not None:
            response = self._create_message_traced(
                tracer=tracer,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                call_type=call_type,
                after_tools=after_tools,
            )
        else:
            response = self._call_api(
                system=system, messages=messages, tools=tools, max_tokens=max_tokens
            )

        elapsed = time.monotonic() - t0
        record_token_usage(
            self._model,
            response.usage.get("input_tokens", 0),
            response.usage.get("output_tokens", 0),
        )
        record_operation_duration(self._model, response.stop_reason, elapsed)
        return response

    def _create_message_traced(
        self,
        *,
        tracer,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        call_type: str = "initial",
        after_tools: list[str] | None = None,
    ) -> LLMResponse:
        from opentelemetry.trace import SpanKind, StatusCode

        with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.system", "anthropic")
            span.set_attribute("gen_ai.request.model", self._model)
            span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            span.set_attribute("llm.message_count", len(messages))
            span.set_attribute("gen_ai.call.type", call_type)
            if after_tools:
                span.set_attribute("gen_ai.call.after_tools", after_tools)
            if tools:
                span.set_attribute("llm.tool_count", len(tools))

            # Record prompt events — system + messages
            span.add_event("gen_ai.content.prompt", attributes={
                "gen_ai.prompt": json.dumps(
                    [{"role": "system", "content": system[:1000]}]
                    + [
                        {"role": m.get("role", ""), "content": _summarise_content(m.get("content", ""))}
                        for m in messages[-4:]  # last 4 messages to bound size
                    ]
                ),
            })

            try:
                response = self._call_api(
                    system=system, messages=messages, tools=tools, max_tokens=max_tokens
                )
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                span.record_exception(exc)
                raise

            # Record completion event — LLM response
            span.add_event("gen_ai.content.completion", attributes={
                "gen_ai.completion": json.dumps(
                    _summarise_content(response.content)
                ),
            })

            span.set_attribute("gen_ai.response.model", response.model)
            span.set_attribute("gen_ai.response.finish_reasons", [response.stop_reason])
            span.set_attribute("gen_ai.usage.input_tokens", response.usage.get("input_tokens", 0))
            span.set_attribute("gen_ai.usage.output_tokens", response.usage.get("output_tokens", 0))
            span.set_status(StatusCode.OK)
            return response

    def _call_api(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
    ) -> LLMResponse:
        kwargs = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = self._client.messages.create(**kwargs)

        # Normalize content blocks to dicts
        content = []
        for block in response.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return LLMResponse(
            content=content,
            stop_reason=response.stop_reason,
            model=response.model,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )


def _summarise_content(content, max_len: int = 1000) -> str | list:
    """Truncate message content for span events.

    Handles both string content and Anthropic-style block lists
    (tool_use, tool_result, text).
    """
    if isinstance(content, str):
        return content[:max_len]
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block[:max_len])
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append({"type": "text", "text": block.get("text", "")[:max_len]})
                elif btype == "tool_use":
                    inp = json.dumps(block.get("input", {}))[:500]
                    parts.append({"type": "tool_use", "name": block.get("name", ""), "input": inp})
                elif btype == "tool_result":
                    parts.append({"type": "tool_result", "content": str(block.get("content", ""))[:500]})
                else:
                    parts.append({"type": btype})
        return parts
    return str(content)[:max_len]
