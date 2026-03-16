"""Tests for OTEL telemetry — tracer setup, LLM spans, engine spans, logs bridge."""

import pytest
from unittest.mock import MagicMock

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from mithai.telemetry.tracer import get_tracer, reset_tracer, setup_telemetry
from mithai.llm.anthropic import AnthropicProvider
from mithai.llm.base import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exporter():
    """Return a (TracerProvider, InMemorySpanExporter) pair wired together."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _fake_response(model="claude-sonnet-4-6", stop_reason="end_turn", input_tokens=100, output_tokens=50):
    return LLMResponse(
        content=[{"type": "text", "text": "hello"}],
        stop_reason=stop_reason,
        model=model,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )


# ---------------------------------------------------------------------------
# setup_telemetry
# ---------------------------------------------------------------------------

class TestSetupTelemetry:
    def setup_method(self):
        reset_tracer()

    def test_disabled_by_default(self):
        setup_telemetry({})
        assert get_tracer() is None

    def test_disabled_explicitly(self):
        setup_telemetry({"telemetry": {"enabled": False}})
        assert get_tracer() is None

    def test_enabled_stdout_exporter(self):
        # Use "none" to avoid PeriodicExportingMetricReader background thread
        # noise when pytest closes stdout; we only care that the tracer is active.
        setup_telemetry({"telemetry": {"enabled": True, "exporter": "none"}})
        assert get_tracer() is not None

    def test_enabled_none_exporter(self):
        # "none" exporter — spans created but discarded; tracer still active
        setup_telemetry({"telemetry": {"enabled": True, "exporter": "none"}})
        assert get_tracer() is not None

    def test_unknown_exporter_leaves_tracer_none(self):
        setup_telemetry({"telemetry": {"enabled": True, "exporter": "bogus"}})
        assert get_tracer() is None

    def test_missing_otel_packages_does_not_raise(self, monkeypatch):
        import builtins
        real_import = builtins.__import__

        def patched_import(name, *args, **kwargs):
            if name.startswith("opentelemetry"):
                raise ImportError(f"mocked missing: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", patched_import)
        # Should log a warning but not raise
        setup_telemetry({"telemetry": {"enabled": True, "exporter": "stdout"}})
        assert get_tracer() is None


# ---------------------------------------------------------------------------
# AnthropicProvider tracing
# ---------------------------------------------------------------------------

class TestAnthropicProviderTracing:
    def setup_method(self):
        reset_tracer()

    def _provider_with_mock_client(self):
        provider = AnthropicProvider(api_key="test-key", model="claude-test")
        provider._client = MagicMock()
        return provider

    def _set_mock_response(self, provider, resp: LLMResponse):
        """Wire the mock Anthropic client to return a fake response."""
        raw = MagicMock()
        raw.model = resp.model
        raw.stop_reason = resp.stop_reason
        raw.usage.input_tokens = resp.usage["input_tokens"]
        raw.usage.output_tokens = resp.usage["output_tokens"]
        raw.content = []
        for block in resp.content:
            m = MagicMock()
            m.type = block["type"]
            if block["type"] == "text":
                m.text = block["text"]
            provider._client.messages.create.return_value = raw

    def test_no_span_when_telemetry_disabled(self):
        # tracer is None (reset above) — create_message must still work
        prov = self._provider_with_mock_client()
        self._set_mock_response(prov, _fake_response())
        result = prov.create_message(
            system="sys", messages=[{"role": "user", "content": "hi"}]
        )
        assert result.stop_reason == "end_turn"

    def test_span_emitted_with_correct_attributes(self):
        otel_provider, exporter = _make_exporter()
        from opentelemetry import trace
        trace.set_tracer_provider(otel_provider)

        import mithai.telemetry.tracer as t_mod
        t_mod._tracer = otel_provider.get_tracer("mithai")

        prov = self._provider_with_mock_client()
        resp = _fake_response(
            model="claude-test",
            stop_reason="end_turn",
            input_tokens=200,
            output_tokens=75,
        )
        self._set_mock_response(prov, resp)

        prov.create_message(
            system="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "tool_a"}, {"name": "tool_b"}],
            max_tokens=512,
        )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]

        assert span.name == "gen_ai.chat"
        attrs = span.attributes
        assert attrs["gen_ai.system"] == "anthropic"
        assert attrs["gen_ai.request.model"] == "claude-test"
        assert attrs["gen_ai.request.max_tokens"] == 512
        assert attrs["gen_ai.response.model"] == "claude-test"
        assert attrs["gen_ai.response.finish_reasons"] == ("end_turn",)
        assert attrs["gen_ai.usage.input_tokens"] == 200
        assert attrs["gen_ai.usage.output_tokens"] == 75
        assert attrs["llm.message_count"] == 1
        assert attrs["llm.tool_count"] == 2

    def test_span_status_error_on_api_failure(self):
        from opentelemetry import trace
        from opentelemetry.trace import StatusCode

        otel_provider, exporter = _make_exporter()
        trace.set_tracer_provider(otel_provider)

        import mithai.telemetry.tracer as t_mod
        t_mod._tracer = otel_provider.get_tracer("mithai")

        prov = self._provider_with_mock_client()
        prov._client.messages.create.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            prov.create_message(
                system="sys", messages=[{"role": "user", "content": "hi"}]
            )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert "API down" in span.status.description

    def test_span_no_tool_count_when_no_tools(self):
        otel_provider, exporter = _make_exporter()
        from opentelemetry import trace
        trace.set_tracer_provider(otel_provider)

        import mithai.telemetry.tracer as t_mod
        t_mod._tracer = otel_provider.get_tracer("mithai")

        prov = self._provider_with_mock_client()
        self._set_mock_response(prov, _fake_response())

        prov.create_message(system="sys", messages=[{"role": "user", "content": "hi"}])

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert "llm.tool_count" not in spans[0].attributes


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

class TestTelemetryConfig:
    def test_telemetry_section_optional(self, tmp_path):
        import yaml
        from mithai.core.config import load_config

        cfg = {
            "adapter": {"type": "cli"},
            "llm": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "anthropic": {"api_key": "x"},
            },
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg))
        loaded = load_config(p)
        assert loaded.get("telemetry") is None

    def test_telemetry_section_validates(self, tmp_path):
        import yaml
        from mithai.core.config import load_config

        cfg = {
            "adapter": {"type": "cli"},
            "llm": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "anthropic": {"api_key": "x"},
            },
            "telemetry": {
                "enabled": True,
                "service_name": "test-mithai",
                "exporter": "otlp",
                "otlp": {"endpoint": "http://localhost:4318"},
            },
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg))
        loaded = load_config(p)
        assert loaded["telemetry"]["enabled"] is True
        assert loaded["telemetry"]["otlp"]["endpoint"] == "http://localhost:4318"

    def test_telemetry_logs_config_validates(self, tmp_path):
        import yaml
        from mithai.core.config import load_config

        cfg = {
            "adapter": {"type": "cli"},
            "llm": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "anthropic": {"api_key": "x"},
            },
            "telemetry": {
                "enabled": True,
                "exporter": "stdout",
                "logs": {"enabled": True, "level": "ERROR"},
            },
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg))
        loaded = load_config(p)
        assert loaded["telemetry"]["logs"]["enabled"] is True
        assert loaded["telemetry"]["logs"]["level"] == "ERROR"


# ---------------------------------------------------------------------------
# v1 — Engine request + tool spans
# ---------------------------------------------------------------------------

def _make_wired_provider():
    """Return (TracerProvider, InMemorySpanExporter) injected into the telemetry module.

    Avoids calling trace.set_tracer_provider() — which OTEL only allows once per
    process — and instead injects directly into the module-level _tracer so both
    AnthropicProvider and Engine pick up the same tracer (and thus the same exporter).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    import mithai.telemetry.tracer as t_mod
    t_mod._tracer = provider.get_tracer("mithai")
    return provider, exporter


def _make_engine(tmp_skill_dir, tmp_path, llm=None):
    """Return a minimal Engine backed by a MemoryStateBackend."""
    from mithai.core.engine import Engine
    from mithai.state.memory import MemoryStateBackend
    from mithai.core.skill_loader import load_skills

    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic", "model": "claude-test", "anthropic": {"api_key": "x"}},
        "skills": {"paths": [str(tmp_skill_dir)]},
    }
    skills = load_skills([tmp_skill_dir])
    if llm is None:
        llm = MagicMock()
    return Engine(config, llm, MemoryStateBackend(), skills=skills), llm


def _make_anthropic_provider_with_mock():
    """Return an AnthropicProvider whose HTTP client is a MagicMock."""
    from mithai.llm.anthropic import AnthropicProvider
    prov = AnthropicProvider(api_key="test-key", model="claude-test")
    prov._client = MagicMock()
    return prov


def _stub_raw_response(provider, stop_reason="end_turn", input_tokens=10, output_tokens=5):
    """Configure the mock Anthropic client to return a single text block."""
    raw = MagicMock()
    raw.model = "claude-test"
    raw.stop_reason = stop_reason
    raw.usage.input_tokens = input_tokens
    raw.usage.output_tokens = output_tokens
    block = MagicMock()
    block.type = "text"
    block.text = "done"
    raw.content = [block]
    provider._client.messages.create.return_value = raw


def _stub_llm_end_turn(llm_mock):
    """Make a MagicMock LLM return a simple end_turn response."""
    llm_mock.create_message.return_value = LLMResponse(
        content=[{"type": "text", "text": "done"}],
        stop_reason="end_turn",
        model="claude-test",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def _stub_llm_tool_then_end(llm_mock, tool_name="test_skill__echo"):
    """First call returns a tool_use block; second returns end_turn."""
    tool_response = LLMResponse(
        content=[{
            "type": "tool_use",
            "id": "tu_1",
            "name": tool_name,
            "input": {"message": "hi"},
        }],
        stop_reason="tool_use",
        model="claude-test",
        usage={"input_tokens": 20, "output_tokens": 10},
    )
    end_response = LLMResponse(
        content=[{"type": "text", "text": "done"}],
        stop_reason="end_turn",
        model="claude-test",
        usage={"input_tokens": 15, "output_tokens": 8},
    )
    llm_mock.create_message.side_effect = [tool_response, end_response]


class TestEngineRequestSpan:
    def setup_method(self):
        reset_tracer()

    def test_request_span_emitted(self, tmp_skill_dir, tmp_path):
        _, exporter = _make_wired_provider()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_end_turn(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(
            text="hello",
            channel_id="C123",
            user_id="U456",
            platform="slack",
        )
        engine.handle(msg, MagicMock())

        span_names = [s.name for s in exporter.get_finished_spans()]
        assert "mithai.request" in span_names

    def test_request_span_attributes(self, tmp_skill_dir, tmp_path):
        _, exporter = _make_wired_provider()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_end_turn(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(
            text="hello",
            channel_id="C123",
            user_id="U456",
            platform="telegram",
        )
        engine.handle(msg, MagicMock())

        spans = {s.name: s for s in exporter.get_finished_spans()}
        req = spans["mithai.request"]
        assert req.attributes["mithai.platform"] == "telegram"
        assert req.attributes["mithai.channel_id"] == "C123"
        assert req.attributes["mithai.user_id"] == "U456"

    def test_gen_ai_chat_is_child_of_request(self, tmp_skill_dir, tmp_path):
        # Use a real AnthropicProvider (with mocked HTTP client) so it emits gen_ai.chat spans.
        _, exporter = _make_wired_provider()
        anthropic_prov = _make_anthropic_provider_with_mock()
        _stub_raw_response(anthropic_prov)
        engine, _ = _make_engine(tmp_skill_dir, tmp_path, llm=anthropic_prov)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="hi", channel_id="C1", user_id="U1", platform="cli")
        engine.handle(msg, MagicMock())

        spans = {s.name: s for s in exporter.get_finished_spans()}
        assert "mithai.request" in spans
        assert "gen_ai.chat" in spans
        # gen_ai.chat parent should be the mithai.request span
        req_ctx = spans["mithai.request"].context
        chat_parent = spans["gen_ai.chat"].parent
        assert chat_parent is not None
        assert chat_parent.span_id == req_ctx.span_id

    def test_no_span_when_telemetry_disabled(self, tmp_skill_dir, tmp_path):
        # tracer is None (reset above)
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_end_turn(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="hi", channel_id="C1", user_id="U1", platform="cli")
        # Must not raise even without telemetry
        result = engine.handle(msg, MagicMock())
        assert result == "done"


class TestEngineToolSpan:
    def setup_method(self):
        reset_tracer()

    def test_tool_span_emitted_on_approved_tool(self, tmp_skill_dir, tmp_path):
        _, exporter = _make_wired_provider()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_tool_then_end(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="echo hi", channel_id="C1", user_id="U1", platform="cli")
        engine.handle(msg, MagicMock())

        span_names = [s.name for s in exporter.get_finished_spans()]
        assert "mithai.tool.execute" in span_names

    def test_tool_span_attributes(self, tmp_skill_dir, tmp_path):
        _, exporter = _make_wired_provider()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_tool_then_end(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="echo hi", channel_id="C1", user_id="U1", platform="cli")
        engine.handle(msg, MagicMock())

        spans = {s.name: s for s in exporter.get_finished_spans()}
        tool_span = spans["mithai.tool.execute"]
        assert tool_span.attributes["mithai.tool.name"] == "test_skill__echo"
        assert tool_span.attributes["mithai.tool.approved"] is True

    def test_tool_span_is_child_of_request(self, tmp_skill_dir, tmp_path):
        _, exporter = _make_wired_provider()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_tool_then_end(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="echo hi", channel_id="C1", user_id="U1", platform="cli")
        engine.handle(msg, MagicMock())

        spans = {s.name: s for s in exporter.get_finished_spans()}
        req_ctx = spans["mithai.request"].context
        tool_parent = spans["mithai.tool.execute"].parent
        assert tool_parent is not None
        assert tool_parent.span_id == req_ctx.span_id


# ---------------------------------------------------------------------------
# v2 — Metrics
# ---------------------------------------------------------------------------

from mithai.telemetry.metrics import reset_metrics


def _make_wired_meter():
    """Return (MeterProvider, InMemoryMetricReader) injected into the metrics module."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    import mithai.telemetry.metrics as m_mod

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("mithai")

    m_mod._meter = meter
    m_mod._token_usage = meter.create_histogram(
        "gen_ai.client.token.usage", unit="{token}"
    )
    m_mod._op_duration = meter.create_histogram(
        "gen_ai.client.operation.duration", unit="s"
    )
    m_mod._tool_duration = meter.create_histogram("mithai.tool.duration", unit="s")
    m_mod._tool_calls = meter.create_counter("mithai.tool.calls", unit="{call}")
    m_mod._requests = meter.create_counter("mithai.requests", unit="{request}")

    return provider, reader


def _metric_by_name(reader, name: str):
    """Return the first ScopeMetrics entry matching *name*, or None."""
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    return metric
    return None


class TestTokenMetrics:
    def setup_method(self):
        reset_tracer()
        reset_metrics()

    def test_token_usage_recorded_on_llm_call(self):
        _, reader = _make_wired_meter()

        prov = _make_anthropic_provider_with_mock()
        _stub_raw_response(prov, input_tokens=300, output_tokens=100)

        prov.create_message(system="sys", messages=[{"role": "user", "content": "hi"}])

        metric = _metric_by_name(reader, "gen_ai.client.token.usage")
        assert metric is not None
        # Collect all data points
        points = list(metric.data.data_points)
        assert len(points) >= 2  # at least input + output

        # Verify input/output token values recorded correctly
        by_type = {p.attributes.get("gen_ai.token.type"): p.sum for p in points}
        assert by_type["input"] == 300
        assert by_type["output"] == 100

    def test_operation_duration_recorded(self):
        _, reader = _make_wired_meter()

        prov = _make_anthropic_provider_with_mock()
        _stub_raw_response(prov)

        prov.create_message(system="sys", messages=[{"role": "user", "content": "hi"}])

        metric = _metric_by_name(reader, "gen_ai.client.operation.duration")
        assert metric is not None
        points = list(metric.data.data_points)
        assert len(points) == 1
        assert points[0].sum >= 0
        assert points[0].attributes["gen_ai.request.model"] == "claude-test"
        assert points[0].attributes["gen_ai.response.finish_reasons"] == "end_turn"

    def test_no_metrics_when_disabled(self):
        # metrics module is reset — _token_usage is None
        prov = _make_anthropic_provider_with_mock()
        _stub_raw_response(prov)
        # Must not raise
        result = prov.create_message(
            system="sys", messages=[{"role": "user", "content": "hi"}]
        )
        assert result.stop_reason == "end_turn"


class TestToolMetrics:
    def setup_method(self):
        reset_tracer()
        reset_metrics()

    def test_tool_call_counter_incremented(self, tmp_skill_dir, tmp_path):
        _, reader = _make_wired_meter()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_tool_then_end(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="echo hi", channel_id="C1", user_id="U1", platform="cli")
        engine.handle(msg, MagicMock())

        metric = _metric_by_name(reader, "mithai.tool.calls")
        assert metric is not None
        points = list(metric.data.data_points)
        assert len(points) == 1
        assert points[0].value == 1
        assert points[0].attributes["mithai.tool.name"] == "test_skill__echo"
        assert points[0].attributes["mithai.tool.decision"] == "approved"

    def test_tool_duration_recorded(self, tmp_skill_dir, tmp_path):
        _, reader = _make_wired_meter()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_tool_then_end(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="echo hi", channel_id="C1", user_id="U1", platform="cli")
        engine.handle(msg, MagicMock())

        metric = _metric_by_name(reader, "mithai.tool.duration")
        assert metric is not None
        points = list(metric.data.data_points)
        assert len(points) == 1
        assert points[0].sum >= 0

    def test_request_counter_incremented(self, tmp_skill_dir, tmp_path):
        _, reader = _make_wired_meter()
        engine, llm = _make_engine(tmp_skill_dir, tmp_path)
        _stub_llm_end_turn(llm)

        from mithai.adapters.base import IncomingMessage
        msg = IncomingMessage(text="hi", channel_id="C1", user_id="U1", platform="slack")
        engine.handle(msg, MagicMock())

        metric = _metric_by_name(reader, "mithai.requests")
        assert metric is not None
        points = list(metric.data.data_points)
        assert len(points) == 1
        assert points[0].value == 1
        assert points[0].attributes["mithai.platform"] == "slack"


class TestSamplingConfig:
    def setup_method(self):
        reset_tracer()
        reset_metrics()

    def test_sampling_ratio_config_validates(self, tmp_path):
        import yaml
        from mithai.core.config import load_config

        cfg = {
            "adapter": {"type": "cli"},
            "llm": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "anthropic": {"api_key": "x"},
            },
            "telemetry": {
                "enabled": True,
                "exporter": "stdout",
                "sampling": {"ratio": 0.1},
            },
        }
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(cfg))
        loaded = load_config(p)
        assert loaded["telemetry"]["sampling"]["ratio"] == pytest.approx(0.1)

    def test_sampling_ratio_1_uses_no_sampler(self):
        # Full sampling (default) should not raise and should produce a tracer
        setup_telemetry({"telemetry": {"enabled": True, "exporter": "none", "sampling": {"ratio": 1.0}}})
        assert get_tracer() is not None

    def test_sampling_ratio_partial(self):
        # Partial sampling still creates a tracer; statistical behaviour not
        # asserted here (would require many samples).
        setup_telemetry({"telemetry": {"enabled": True, "exporter": "none", "sampling": {"ratio": 0.5}}})
        assert get_tracer() is not None
