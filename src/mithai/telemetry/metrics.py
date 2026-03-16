"""Metric instruments for mithai.

Instruments follow OpenTelemetry GenAI semantic conventions where applicable.
All state is module-level so callers simply call record_* functions without
holding a reference to a meter object.

setup_metrics() is called once from tracer.setup_telemetry().
reset_metrics() is for tests only.
"""

import logging

logger = logging.getLogger(__name__)

_meter = None

# Instruments — created once in setup_metrics(), None until then.
_token_usage = None        # gen_ai.client.token.usage  (histogram, {token})
_op_duration = None        # gen_ai.client.operation.duration (histogram, s)
_tool_duration = None      # mithai.tool.duration  (histogram, s)
_tool_calls = None         # mithai.tool.calls     (counter, {call})
_requests = None           # mithai.requests       (counter, {request})


def setup_metrics(*, resource, exporter_type: str, otlp_cfg: dict) -> None:
    """Initialize MeterProvider and create all instruments.

    Called from tracer.setup_telemetry() after tracing is set up.
    """
    global _meter, _token_usage, _op_duration, _tool_duration, _tool_calls, _requests

    try:
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    except ImportError:
        logger.debug("opentelemetry-sdk metrics not available — skipping metric setup.")
        return

    if exporter_type == "stdout":
        try:
            from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
            reader = PeriodicExportingMetricReader(ConsoleMetricExporter())
        except ImportError:
            return
    elif exporter_type == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        except ImportError:
            logger.debug("OTLP metric exporter not installed — skipping metrics.")
            return
        endpoint = otlp_cfg.get("endpoint") or "http://localhost:4318"
        headers = otlp_cfg.get("headers") or {}
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=f"{endpoint.rstrip('/')}/v1/metrics",
                headers=headers,
            )
        )
    else:
        return  # "none" or unknown — no metrics

    provider = MeterProvider(resource=resource, metric_readers=[reader])
    _meter = provider.get_meter("mithai")

    _token_usage = _meter.create_histogram(
        name="gen_ai.client.token.usage",
        unit="{token}",
        description="Number of tokens used in LLM calls",
    )
    _op_duration = _meter.create_histogram(
        name="gen_ai.client.operation.duration",
        unit="s",
        description="Duration of LLM API calls in seconds",
    )
    _tool_duration = _meter.create_histogram(
        name="mithai.tool.duration",
        unit="s",
        description="Duration of tool executions in seconds",
    )
    _tool_calls = _meter.create_counter(
        name="mithai.tool.calls",
        unit="{call}",
        description="Number of tool calls made",
    )
    _requests = _meter.create_counter(
        name="mithai.requests",
        unit="{request}",
        description="Number of incoming messages handled",
    )


def get_meter():
    """Return the active meter, or None if metrics are disabled."""
    return _meter


def record_token_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    if _token_usage is None:
        return
    attrs = {"gen_ai.system": "anthropic", "gen_ai.request.model": model}
    _token_usage.record(input_tokens, {**attrs, "gen_ai.token.type": "input"})
    _token_usage.record(output_tokens, {**attrs, "gen_ai.token.type": "output"})


def record_operation_duration(model: str, finish_reason: str, duration_s: float) -> None:
    if _op_duration is None:
        return
    _op_duration.record(
        duration_s,
        {
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": model,
            "gen_ai.response.finish_reasons": finish_reason,
        },
    )


def record_tool_call(tool_name: str, approved: bool, duration_s: float) -> None:
    if _tool_calls is None or _tool_duration is None:
        return
    decision = "approved" if approved else "denied"
    attrs = {"mithai.tool.name": tool_name, "mithai.tool.approved": str(approved)}
    _tool_calls.add(1, {"mithai.tool.name": tool_name, "mithai.tool.decision": decision})
    _tool_duration.record(duration_s, attrs)


def record_request(platform: str) -> None:
    if _requests is None:
        return
    _requests.add(1, {"mithai.platform": platform})


def reset_metrics() -> None:
    """Reset all module state — for use in tests only."""
    global _meter, _token_usage, _op_duration, _tool_duration, _tool_calls, _requests
    _meter = None
    _token_usage = None
    _op_duration = None
    _tool_duration = None
    _tool_calls = None
    _requests = None
