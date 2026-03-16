"""OpenTelemetry integration for mithai — traces, metrics, and logs for LLM calls."""

from mithai.telemetry.metrics import get_meter
from mithai.telemetry.tracer import get_tracer, setup_telemetry

__all__ = ["setup_telemetry", "get_tracer", "get_meter"]
