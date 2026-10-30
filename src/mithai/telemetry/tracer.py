"""TracerProvider (+ optional logs bridge) setup for mithai.

Initializes OpenTelemetry tracing and log forwarding from the
``telemetry`` config section.  All OTEL imports are deferred so the
package works without the optional ``opentelemetry-*`` packages installed.
"""

import logging

logger = logging.getLogger(__name__)

# Module-level tracer — None when telemetry is disabled or packages missing.
_tracer = None
# Reference to the LoggingHandler so tests can remove it cleanly.
_log_handler = None
# Providers owned by this module, in setup order (traces, metrics, logs).
_providers = []


def setup_telemetry(config: dict) -> None:
    """Initialize OpenTelemetry from config.

    Safe to call unconditionally — does nothing when ``telemetry.enabled``
    is false or the opentelemetry packages are not installed.
    """
    global _tracer, _log_handler

    tel = config.get("telemetry") or {}
    if not tel.get("enabled", False):
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "opentelemetry-sdk not installed — telemetry disabled. "
            "Install with: pip install 'mithai[telemetry]'"
        )
        return

    service_name = tel.get("service_name", "mithai")
    resource = Resource.create({SERVICE_NAME: service_name})

    # ------------------------------------------------------------------ traces
    exporter_type = tel.get("exporter", "otlp")
    sampling_cfg = tel.get("sampling") or {}
    _ratio = sampling_cfg.get("ratio")
    ratio = float(_ratio if _ratio is not None else 1.0)

    if ratio < 1.0:
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
        sampler = TraceIdRatioBased(ratio)
        trace_provider = TracerProvider(resource=resource, sampler=sampler)
    else:
        trace_provider = TracerProvider(resource=resource)

    _providers.append(trace_provider)

    if exporter_type == "stdout":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        trace_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    elif exporter_type == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            logger.warning(
                "opentelemetry-exporter-otlp-proto-http not installed — telemetry disabled. "
                "Install with: pip install 'mithai[telemetry]'"
            )
            return
        otlp = tel.get("otlp") or {}
        # Unset values let the SDK resolve OTEL_EXPORTER_OTLP_* and defaults.
        endpoint = otlp.get("endpoint")
        headers = otlp.get("headers") or None
        trace_provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{endpoint.rstrip('/')}/v1/traces" if endpoint else None,
                headers=headers,
            )
        ))

    elif exporter_type == "none":
        pass  # spans created but silently discarded

    else:
        logger.warning("Unknown telemetry exporter %r — telemetry disabled.", exporter_type)
        return

    # Last9 GenAI span processor — enriches spans with conversation/workflow context
    try:
        from last9_genai import Last9SpanProcessor
        trace_provider.add_span_processor(Last9SpanProcessor())
        logger.info("Last9 GenAI span processor enabled")
    except ImportError:
        logger.debug("last9-genai not installed — skipping Last9 span processor")

    trace.set_tracer_provider(trace_provider)
    _tracer = trace_provider.get_tracer("mithai")

    # ----------------------------------------------------------------- metrics
    from mithai.telemetry.metrics import setup_metrics
    meter_provider = setup_metrics(
        resource=resource, exporter_type=exporter_type, otlp_cfg=tel.get("otlp") or {}
    )
    if meter_provider is not None:
        _providers.append(meter_provider)

    # ------------------------------------------------------------------- logs
    logs_cfg = tel.get("logs") or {}
    if logs_cfg.get("enabled", False):
        _log_handler = _setup_logs_bridge(
            resource=resource,
            exporter_type=exporter_type,
            otlp_cfg=tel.get("otlp") or {},
            level_name=logs_cfg.get("level", "WARNING"),
        )

    logger.info("Telemetry enabled: service=%s exporter=%s", service_name, exporter_type)


def _setup_logs_bridge(*, resource, exporter_type: str, otlp_cfg: dict, level_name: str):
    """Attach a LoggingHandler to the Python root logger.

    Forwards log records at or above *level_name* to the OTEL log pipeline.
    Returns the handler so callers can remove it (e.g. in tests).
    """
    try:
        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogExporter
    except ImportError:
        logger.debug("OTEL logs bridge unavailable — opentelemetry-sdk not installed.")
        return None

    log_provider = LoggerProvider(resource=resource)
    _providers.append(log_provider)

    if exporter_type == "stdout":
        log_provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogExporter()))
    elif exporter_type == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        except ImportError:
            logger.debug("OTLP log exporter not installed — skipping logs bridge.")
            return None
        # Unset values let the SDK resolve OTEL_EXPORTER_OTLP_* and defaults.
        endpoint = otlp_cfg.get("endpoint")
        headers = otlp_cfg.get("headers") or None
        log_provider.add_log_record_processor(BatchLogRecordProcessor(
            OTLPLogExporter(
                endpoint=f"{endpoint.rstrip('/')}/v1/logs" if endpoint else None,
                headers=headers,
            )
        ))
    else:
        return None

    set_logger_provider(log_provider)

    level = getattr(logging, (level_name or "WARNING").upper(), logging.WARNING)
    from opentelemetry.sdk._logs import LoggingHandler as OTELLoggingHandler
    otel_handler = OTELLoggingHandler(level=level, logger_provider=log_provider)
    logging.getLogger().addHandler(otel_handler)
    return otel_handler


def get_tracer():
    """Return the active tracer, or None if telemetry is disabled."""
    return _tracer


def shutdown_telemetry() -> None:
    """Drain and close our providers without masking a chat/startup failure.

    SDK globals may belong to an embedding application. Only shut down the
    providers we created, and detach the logs bridge before closing its pipeline.
    Safe to call again, or when telemetry was never enabled.
    """
    global _tracer, _log_handler
    providers = list(_providers)
    _providers.clear()
    if _log_handler is not None:
        logging.getLogger().removeHandler(_log_handler)
        _log_handler.close()
        _log_handler = None
    _tracer = None

    from mithai.telemetry.metrics import reset_metrics
    reset_metrics()
    for provider in providers:
        try:
            try:
                provider.force_flush()
            finally:
                provider.shutdown()
        except Exception:
            logger.exception("Failed to shut down telemetry provider %s", type(provider).__name__)


def reset_tracer() -> None:
    """Reset module state and close owned providers — for use in tests only."""
    shutdown_telemetry()
