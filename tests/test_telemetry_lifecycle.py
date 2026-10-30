"""Telemetry cleanup drains every owned pipeline, even if one fails."""

from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult

from mithai.telemetry import tracer
from mithai.telemetry.metrics import record_request


@pytest.mark.parametrize("failure", [None, "force_flush", "shutdown"])
def test_shutdown_drains_all_providers_once(monkeypatch, failure):
    tracer.reset_tracer()
    providers = [MagicMock(), MagicMock(), MagicMock()]
    if failure:
        getattr(providers[0], failure).side_effect = RuntimeError("export failed")
    monkeypatch.setattr(tracer, "_providers", providers.copy())
    tracer.shutdown_telemetry()
    tracer.shutdown_telemetry()
    for provider in providers:
        provider.force_flush.assert_called_once()
        provider.shutdown.assert_called_once()
    assert tracer.get_tracer() is None


def test_shutdown_exports_pending_metrics(monkeypatch):
    class Exporter(MetricExporter):
        def __init__(self):
            super().__init__()
            self.batches = []
            self.closed = False

        def export(self, metrics_data, timeout_millis=10000, **kwargs):
            self.batches.append(metrics_data)
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis=10000):
            return True

        def shutdown(self, timeout_millis=30000, **kwargs):
            self.closed = True

    exporter = Exporter()
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda provider: None)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", MagicMock()
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter",
        lambda **kwargs: exporter,
    )
    try:
        tracer.setup_telemetry({"telemetry": {"enabled": True, "exporter": "otlp"}})
        record_request("cli")
        tracer.shutdown_telemetry()
        assert exporter.closed
        metrics = [
            metric
            for batch in exporter.batches
            for resource in batch.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        ]
        requests = [metric for metric in metrics if metric.name == "mithai.requests"]
        assert requests
        assert requests[0].data.data_points[0].value == 1
    finally:
        tracer.reset_tracer()
