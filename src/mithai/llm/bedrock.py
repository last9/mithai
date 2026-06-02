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

    Authentication uses explicit IAM credentials (access key + secret + region).
    boto3 is imported lazily so the bedrock extra remains optional.
    """

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        region: str,
        model: str,
    ):
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region
        self._model = model
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
        from mithai.telemetry.metrics import record_operation_duration, record_token_usage

        t0 = time.monotonic()

        kwargs: dict = {
            "modelId": self._model,
            "system": [{"text": system}],
            "messages": messages_to_bedrock(messages),
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if tools:
            kwargs["toolConfig"] = anthropic_tools_to_bedrock(tools)

        client = self._get_client()
        raw = client.converse(**kwargs)
        response = bedrock_response_to_llm_response(raw)

        elapsed = time.monotonic() - t0
        record_token_usage(
            self._model,
            response.usage.get("input_tokens", 0),
            response.usage.get("output_tokens", 0),
        )
        record_operation_duration(self._model, response.stop_reason, elapsed)
        return response
