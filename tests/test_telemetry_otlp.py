"""OTLP transport configuration shared by traces, metrics and logs."""

import logging
from unittest.mock import MagicMock

import pytest

from mithai.telemetry.metrics import reset_metrics
from mithai.telemetry.tracer import reset_tracer, setup_telemetry


@pytest.mark.parametrize(
    "otlp,env_endpoint,env_headers,endpoint,headers",
    [
        (
            {},
            "https://env.example/",
            "Authorization=Bearer%20env",
            "https://env.example",
            {"authorization": "Bearer env"},
        ),
        (
            {"endpoint": "https://config.example/", "headers": {"Authorization": "Bearer config"}},
            "https://env.example",
            "Authorization=Bearer%20env",
            "https://config.example",
            {"authorization": "Bearer config"},
        ),
        (
            {"endpoint": None, "headers": None},
            "https://env.example",
            "Authorization=Bearer%20env,X-Tenant=a%2Cb",
            "https://env.example",
            {"authorization": "Bearer env", "x-tenant": "a,b"},
        ),
        ({}, None, None, "http://localhost:4318", {}),
    ],
)
def test_otlp_settings_for_all_signals(
    monkeypatch, otlp, env_endpoint, env_headers, endpoint, headers
):
    from opentelemetry.exporter.otlp.proto.http import (
        _log_exporter,
        metric_exporter,
        trace_exporter,
    )

    # Exercise the real exporters' env parsing without starting network workers.
    for name in ("ENDPOINT", "HEADERS"):
        for signal in ("", "TRACES_", "METRICS_", "LOGS_"):
            monkeypatch.delenv(f"OTEL_EXPORTER_OTLP_{signal}{name}", raising=False)
    if env_endpoint is not None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", env_endpoint)
    if env_headers is not None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", env_headers)

    exporters = {}
    for module, name, signal in (
        (trace_exporter, "OTLPSpanExporter", "traces"),
        (metric_exporter, "OTLPMetricExporter", "metrics"),
        (_log_exporter, "OTLPLogExporter", "logs"),
    ):
        cls = getattr(module, name)

        def capture(*args, _cls=cls, _signal=signal, **kwargs):
            exporter = _cls(*args, **kwargs)
            exporters[_signal] = exporter
            return exporter

        monkeypatch.setattr(module, name, capture)

    for target in (
        "opentelemetry.sdk.trace.TracerProvider",
        "opentelemetry.sdk.trace.export.BatchSpanProcessor",
        "opentelemetry.sdk.metrics.MeterProvider",
        "opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader",
        "opentelemetry.sdk._logs.LoggerProvider",
        "opentelemetry.sdk._logs.export.BatchLogRecordProcessor",
        "opentelemetry.trace.set_tracer_provider",
        "opentelemetry._logs.set_logger_provider",
    ):
        monkeypatch.setattr(target, MagicMock())
    monkeypatch.setattr(
        "opentelemetry.sdk._logs.LoggingHandler", lambda **kw: logging.NullHandler()
    )
    try:
        setup_telemetry(
            {
                "telemetry": {
                    "enabled": True,
                    "exporter": "otlp",
                    "otlp": otlp,
                    "logs": {"enabled": True},
                }
            }
        )
        assert set(exporters) == {"traces", "metrics", "logs"}
        for signal, exporter in exporters.items():
            assert exporter._endpoint == f"{endpoint}/v1/{signal}"
            actual = {
                key.lower(): value
                for key, value in getattr(exporter, "_client", exporter)._headers.items()
            }
            assert {
                key: value for key, value in actual.items() if key in {"authorization", "x-tenant"}
            } == headers
    finally:
        reset_tracer()
        reset_metrics()
        for exporter in exporters.values():
            exporter.shutdown()
