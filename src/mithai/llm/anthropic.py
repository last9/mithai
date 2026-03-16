"""Anthropic Claude LLM provider."""

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
    ) -> LLMResponse:
        from opentelemetry.trace import SpanKind, StatusCode

        with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.system", "anthropic")
            span.set_attribute("gen_ai.request.model", self._model)
            span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            span.set_attribute("llm.message_count", len(messages))
            if tools:
                span.set_attribute("llm.tool_count", len(tools))

            try:
                response = self._call_api(
                    system=system, messages=messages, tools=tools, max_tokens=max_tokens
                )
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                span.record_exception(exc)
                raise

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
