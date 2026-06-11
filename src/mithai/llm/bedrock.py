"""AWS Bedrock LLM provider — unified Converse API for all model families."""

import logging
import time

from mithai.llm._bedrock_translate import (
    anthropic_tools_to_bedrock,
    bedrock_response_to_llm_response,
    messages_to_bedrock,
)
from mithai.llm.base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)


class BedrockProvider(LLMProvider):
    """LLM provider for AWS Bedrock via the Converse API.

    Supports any model family Bedrock exposes (Anthropic, Llama, Cohere,
    Mistral, ...) — the Converse API is unified, so callers select the model
    by passing the Bedrock model ID (e.g. ``anthropic.claude-sonnet-4-20250514-v1:0``).

    Authentication uses explicit IAM credentials (access key + secret + region),
    with an optional session token for temporary/STS credentials.
    boto3 is imported lazily so the bedrock extra remains optional.
    """

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        model: str,
        session_token: str | None = None,
    ):
        # Validate at construction so misconfiguration surfaces at startup —
        # not on the first user message via an opaque boto3 UnrecognizedClientException.
        missing = [
            name for name, value in (
                ("access_key_id", access_key_id),
                ("secret_access_key", secret_access_key),
                ("region", region),
                ("model", model),
            )
            if not value or not value.strip()
        ]
        if missing:
            raise ValueError(
                "BedrockProvider requires non-empty: " + ", ".join(missing)
            )
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region
        self._model = model
        # Optional — required only for temporary credentials (STS / assumed
        # roles). boto3 treats None as "no session token".
        self._session_token = session_token or None
        self._client = None  # lazy-built on first call

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for Bedrock. Install with: pip install 'mithai[bedrock]'"
                ) from exc
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
                aws_access_key_id=self._access_key_id,
                aws_secret_access_key=self._secret_access_key,
                aws_session_token=self._session_token,
            )
        return self._client

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
            response = self._call_converse(
                system=system, messages=messages, tools=tools, max_tokens=max_tokens
            )

        elapsed = time.monotonic() - t0
        record_token_usage(
            self._model,
            response.usage.get("input_tokens", 0),
            response.usage.get("output_tokens", 0),
            system="aws.bedrock",
        )
        record_operation_duration(
            self._model, response.stop_reason, elapsed, system="aws.bedrock"
        )
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
        # Mirrors AnthropicProvider._create_message_traced so Bedrock calls get
        # the same gen_ai.chat span coverage in traces.
        import json

        from opentelemetry.trace import SpanKind, StatusCode

        from mithai.llm.anthropic import _summarise_content

        with tracer.start_as_current_span("gen_ai.chat", kind=SpanKind.CLIENT) as span:
            span.set_attribute("gen_ai.system", "aws.bedrock")
            span.set_attribute("gen_ai.request.model", self._model)
            span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            span.set_attribute("llm.message_count", len(messages))
            span.set_attribute("gen_ai.call.type", call_type)
            if after_tools:
                span.set_attribute("gen_ai.call.after_tools", after_tools)
            if tools:
                span.set_attribute("llm.tool_count", len(tools))

            # Record prompt events — system + messages. The system entry is
            # included only when non-empty, matching what is actually sent
            # (empty system is omitted from the Converse call entirely).
            span.add_event("gen_ai.content.prompt", attributes={
                "gen_ai.prompt": json.dumps(
                    ([{"role": "system", "content": system[:1000]}] if system else [])
                    + [
                        {"role": m.get("role", ""), "content": _summarise_content(m.get("content", ""))}
                        for m in messages[-4:]  # last 4 messages to bound size
                    ]
                ),
            })

            try:
                response = self._call_converse(
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

    def _call_converse(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
    ) -> LLMResponse:
        kwargs: dict = {
            "modelId": self._model,
            "messages": messages_to_bedrock(messages),
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        # Bedrock rejects empty text blocks with ValidationException — only
        # send the system block when there is actual content.
        if system:
            kwargs["system"] = [{"text": system}]
        if tools:
            kwargs["toolConfig"] = anthropic_tools_to_bedrock(tools)

        client = self._get_client()
        try:
            raw = client.converse(**kwargs)
        except Exception as exc:
            # Wrap boto3 errors (ClientError, EndpointConnectionError, ...) in a
            # RuntimeError so the caller can distinguish "Bedrock call failed"
            # from internal exceptions and so the engine doesn't lose the agent
            # adapter to an uncaught boto exception. The original exception is
            # chained via `from exc` for debugging.
            logger.warning(
                "bedrock converse failed",
                extra={"model": self._model, "error": str(exc)},
            )
            raise RuntimeError(
                f"bedrock converse failed for model {self._model}: {exc}"
            ) from exc
        return bedrock_response_to_llm_response(raw)
