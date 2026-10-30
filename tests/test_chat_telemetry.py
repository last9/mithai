"""Chat owns telemetry from initialization through every exit path."""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from mithai.cli.chat_cmd import chat
from mithai.llm.anthropic import AnthropicProvider
from mithai.state.memory import MemoryStateBackend
from mithai.telemetry.metrics import reset_metrics
from mithai.telemetry.tracer import reset_tracer


@pytest.fixture
def chat_session(monkeypatch, tmp_skill_dir):
    import mithai.cli.chat_cmd as chat_mod
    import mithai.cli.run_cmd as run_mod

    reset_tracer()
    reset_metrics()
    spans = InMemorySpanExporter()
    logs = InMemoryLogRecordExporter()
    # Keep records inspectable after the real batch processors shut down.
    monkeypatch.setattr(spans, "shutdown", MagicMock())
    monkeypatch.setattr(logs, "shutdown", MagicMock())
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        lambda **kwargs: spans,
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http._log_exporter.OTLPLogExporter",
        lambda **kwargs: logs,
    )
    monkeypatch.setattr("mithai.telemetry.metrics.setup_metrics", MagicMock())
    # SDK globals can only be assigned once; isolate this CLI invocation from other tests.
    from opentelemetry import trace

    monkeypatch.setattr(
        trace,
        "set_tracer_provider",
        lambda provider: monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider),
    )
    monkeypatch.setattr("opentelemetry._logs.set_logger_provider", lambda provider: None)

    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "anthropic", "model": "claude-test"},
        "skills": {"paths": [str(tmp_skill_dir)]},
        "telemetry": {"enabled": True, "exporter": "otlp", "logs": {"enabled": True}},
    }
    provider = AnthropicProvider(api_key="test", model="claude-test")
    provider._client = MagicMock()
    monkeypatch.setattr(chat_mod, "load_config", lambda path: config)
    monkeypatch.setattr(chat_mod, "setup_logging", lambda verbose: None)
    monkeypatch.setattr(run_mod, "_create_llm", lambda config: provider)
    monkeypatch.setattr(run_mod, "_create_state", lambda config: MemoryStateBackend())
    monkeypatch.setattr(run_mod, "_create_memory_backend", lambda config: None)
    yield SimpleNamespace(config=config, provider=provider, spans=spans, logs=logs)
    reset_tracer()
    reset_metrics()


def response(reason="end_turn", tool=False):
    block = SimpleNamespace(type="text", text="done")
    if tool:
        block = SimpleNamespace(
            type="tool_use", id="tu1", name="test_skill__echo", input={"message": "hi"}
        )
    return SimpleNamespace(
        model="claude-test",
        stop_reason=reason,
        content=[block],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def test_piped_chat_exports_request_and_children(chat_session):
    def call(**kwargs):
        logging.getLogger(__name__).warning("provider called")
        return next(responses)

    responses = iter([response("tool_use", tool=True), response()])
    chat_session.provider._client.messages.create.side_effect = call
    result = CliRunner().invoke(chat, input="echo hi\n")
    assert result.exit_code == 0, result.output
    spans = chat_session.spans.get_finished_spans()
    requests = [span for span in spans if span.name == "mithai.request"]
    assert len(requests) == 1
    children = [span for span in spans if span.parent is not None]
    assert sorted(span.name for span in children) == [
        "gen_ai.chat",
        "gen_ai.chat",
        "test_skill__echo",
    ]
    assert all(span.parent.span_id == requests[0].context.span_id for span in children)
    assert all(span.context.trace_id == requests[0].context.trace_id for span in children)
    records = chat_session.logs.get_finished_logs()
    assert len(records) == 2
    assert all(record.log_record.trace_id == requests[0].context.trace_id for record in records)
    chat_session.spans.shutdown.assert_called_once()
    chat_session.logs.shutdown.assert_called_once()


@pytest.mark.parametrize("reason", ["end_turn", "max_tokens"])
def test_piped_chat_exports_finish_reason(chat_session, reason):
    chat_session.provider._client.messages.create.return_value = response(reason)
    result = CliRunner().invoke(chat, input="hi")
    assert result.exit_code == 0, result.output
    spans = [span for span in chat_session.spans.get_finished_spans() if span.name == "gen_ai.chat"]
    assert len(spans) == 1
    assert spans[0].attributes["gen_ai.response.finish_reasons"] == (reason,)


def test_piped_chat_exports_provider_error(chat_session):
    chat_session.provider._client.messages.create.side_effect = RuntimeError("SDK rejected request")
    result = CliRunner().invoke(chat, input="hi")
    assert result.exit_code == 1
    spans = [span for span in chat_session.spans.get_finished_spans() if span.name == "gen_ai.chat"]
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR
    assert any(
        event.name == "exception"
        and event.attributes["exception.message"] == "SDK rejected request"
        for event in spans[0].events
    )
    chat_session.spans.shutdown.assert_called_once()
    chat_session.logs.shutdown.assert_called_once()


@pytest.mark.parametrize("exit_path", ["eof", "interrupt", "startup_error"])
def test_chat_shuts_down_telemetry_on_exit(chat_session, monkeypatch, exit_path):
    if exit_path == "interrupt":
        monkeypatch.setattr(
            "mithai.adapters.cli.CLIAdapter.start", MagicMock(side_effect=KeyboardInterrupt)
        )
    elif exit_path == "startup_error":
        monkeypatch.setattr(
            "mithai.cli.run_cmd._create_llm", MagicMock(side_effect=RuntimeError("bad config"))
        )
    result = CliRunner().invoke(chat, input="")
    assert result.exit_code == (1 if exit_path == "startup_error" else 0)
    chat_session.spans.shutdown.assert_called_once()
    chat_session.logs.shutdown.assert_called_once()
    assert not chat_session.spans.get_finished_spans()


def test_chat_without_telemetry_is_unchanged(chat_session):
    chat_session.config.pop("telemetry")
    chat_session.provider._client.messages.create.return_value = response()
    result = CliRunner().invoke(chat, input="hi")
    assert result.exit_code == 0, result.output
    assert "done" in result.output
    assert not chat_session.spans.get_finished_spans()
    assert not chat_session.logs.get_finished_logs()
    chat_session.spans.shutdown.assert_not_called()


@pytest.mark.parametrize("multi_agent", [False, True])
def test_interactive_chat_exports_one_trace_per_turn(chat_session, monkeypatch, multi_agent):
    from mithai.adapters.cli import CLIAdapter

    if multi_agent:
        chat_session.config["agents"] = {"ops": {"skills": {"allowed": ["test_skill"]}}}
    original_init = CLIAdapter.__init__

    def interactive_init(self):
        original_init(self)
        self._piped = False
        self._prompt_session = MagicMock()
        self._prompt_session.prompt.side_effect = ["hi", "again", EOFError()]

    monkeypatch.setattr(CLIAdapter, "__init__", interactive_init)
    monkeypatch.setattr("mithai.adapters.cli._flush_stdin", lambda: None)
    chat_session.provider._client.messages.create.return_value = response()
    result = CliRunner().invoke(chat)
    assert result.exit_code == 0, result.output
    spans = chat_session.spans.get_finished_spans()
    requests = [span for span in spans if span.name == "mithai.request"]
    assert len(requests) == 2
    assert len({span.context.trace_id for span in requests}) == 2
    for request in requests:
        children = [
            span for span in spans if span.parent and span.parent.span_id == request.context.span_id
        ]
        assert len(children) == 1
        assert children[0].name == "gen_ai.chat"
    chat_session.spans.shutdown.assert_called_once()
    chat_session.logs.shutdown.assert_called_once()
