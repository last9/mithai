"""Contract tests for the OpenAI-compatible provider."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mithai.llm.openai_compatible import (
    AvailabilityFailure,
    OpenAICompatibleProvider,
    ProviderError,
    UnavailableReason,
)


class _Handler(BaseHTTPRequestHandler):
    responses = []
    requests = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append((self.command, self.headers, self.path, body))
        status, response = type(self).responses.pop(0)
        payload = json.dumps(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@pytest.fixture
def api_server():
    _Handler.responses = []
    _Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server, _Handler
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _provider(server, **kwargs):
    return OpenAICompatibleProvider(
        api_key="secret-token",
        model="grok-test",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        retry_delay=0,
        **kwargs,
    )


def _success(*, tool=False):
    message = {"role": "assistant", "content": "done"}
    finish_reason = "stop"
    if tool:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell__run", "arguments": '{"command":"uptime"}'},
            }],
        }
        finish_reason = "tool_calls"
    return {
        "model": "grok-test",
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def test_request_uses_exact_transport_boundary_and_normalizes_response(api_server):
    server, handler = api_server
    handler.responses = [(200, _success())]

    response = _provider(server).create_message(
        system="system prompt",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=23,
    )

    method, headers, path, body = handler.requests[0]
    assert (method, headers["Host"], path) == (
        "POST",
        f"127.0.0.1:{server.server_port}",
        "/v1/chat/completions",
    )
    assert headers["Authorization"] == "Bearer secret-token"
    assert body == {
        "model": "grok-test",
        "max_tokens": 23,
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
        ],
    }
    assert response.content == [{"type": "text", "text": "done"}]
    assert response.stop_reason == "end_turn"
    assert response.usage == {"input_tokens": 11, "output_tokens": 7}


def test_verifier_request_uses_configured_provider_endpoint(api_server):
    from mithai.cli.run_cmd import _create_llm
    from mithai.core.engine import Engine
    from mithai.state.memory import MemoryStateBackend

    server, handler = api_server
    handler.responses = [(200, _success())]
    config = {
        "llm": {
            "provider": "openai",
            "model": "grok-test",
            "openai": {
                "api_key": "synthetic-key",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            },
        },
        "verifier": {"model": "grok-small"},
    }
    engine = Engine(config, _create_llm(config), MemoryStateBackend(), skills={})

    engine._verifier_llm.create_message(
        system="verify",
        messages=[{"role": "user", "content": "tool result"}],
    )

    _, headers, path, body = handler.requests[0]
    assert headers["Host"] == f"127.0.0.1:{server.server_port}"
    assert path == "/v1/chat/completions"
    assert body["model"] == "grok-small"


def test_tool_call_round_trip(api_server):
    server, handler = api_server
    handler.responses = [(200, _success(tool=True)), (200, _success())]
    provider = _provider(server)
    tools = [{
        "name": "shell__run",
        "description": "Run a command",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
    }]

    first = provider.create_message(system="system", messages=[], tools=tools)
    provider.create_message(
        system="system",
        messages=[
            {"role": "assistant", "content": first.content},
            {"role": "user", "content": [provider.format_tool_result("call_1", "ok")]},
        ],
        tools=tools,
    )

    assert first.content == [{
        "type": "tool_use",
        "id": "call_1",
        "name": "shell__run",
        "input": {"command": "uptime"},
    }]
    assert first.stop_reason == "tool_use"
    second_body = handler.requests[1][3]
    assert second_body["tools"] == [{
        "type": "function",
        "function": {
            "name": "shell__run",
            "description": "Run a command",
            "parameters": tools[0]["input_schema"],
        },
    }]
    assert second_body["messages"][1] == {
        "role": "assistant",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "shell__run", "arguments": '{"command":"uptime"}'},
        }],
    }
    assert second_body["messages"][2] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_blank_or_missing_credentials_fail_before_transport(api_server, api_key):
    server, handler = api_server
    with pytest.raises(ValueError, match="non-blank api_key"):
        OpenAICompatibleProvider(
            api_key=api_key,
            model="grok-test",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
        )
    assert handler.requests == []


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("code", ["invalid_api_key", "token_expired"])
def test_refused_and_expired_authentication_are_classified(api_server, status, code):
    server, handler = api_server
    handler.responses = [(status, {"error": {"code": code, "message": "credential secret"}})]

    with pytest.raises(AvailabilityFailure) as caught:
        _provider(server).create_message(system="s", messages=[])

    assert caught.value.reason is UnavailableReason.AUTHENTICATION
    assert caught.value.status_code == status
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("code", ["insufficient_quota", "billing_hard_limit_reached", "credit_balance_too_low"])
def test_usage_limit_http_400_is_classified(api_server, code):
    server, handler = api_server
    handler.responses = [(400, {"error": {"code": code, "message": "limit"}})]
    with pytest.raises(AvailabilityFailure) as caught:
        _provider(server).create_message(system="s", messages=[])
    assert caught.value.reason is UnavailableReason.USAGE_LIMIT


@pytest.mark.parametrize("status,code", [
    (400, "invalid_request_error"),
    (400, "content_policy_violation"),
    (400, None),
    (404, "not_found"),
    (429, "rate_limit_exceeded"),
    (500, "server_error"),
    (502, "bad_gateway"),
    (503, "unavailable"),
])
def test_other_statuses_are_not_availability_failures(api_server, status, code):
    server, handler = api_server
    handler.responses = [(status, {"error": {"code": code, "message": "detail"}})] * 3
    with pytest.raises(ProviderError) as caught:
        _provider(server).create_message(system="s", messages=[])
    assert not isinstance(caught.value, AvailabilityFailure)
    assert caught.value.status_code == status


def test_http_529_is_classified_only_after_retry_exhaustion(api_server):
    server, handler = api_server
    handler.responses = [(529, {"error": {"message": "overloaded"}})] * 3

    with pytest.raises(AvailabilityFailure) as caught:
        _provider(server, max_retries=2).create_message(system="s", messages=[])

    assert caught.value.reason is UnavailableReason.OVERLOAD
    assert caught.value.status_code == 529
    assert len(handler.requests) == 3


def test_retry_can_recover_from_transient_failure(api_server):
    server, handler = api_server
    handler.responses = [(503, {"error": {"message": "busy"}}), (200, _success())]
    response = _provider(server, max_retries=2).create_message(system="s", messages=[])
    assert response.stop_reason == "end_turn"
    assert len(handler.requests) == 2


def test_telemetry_contains_metadata_but_no_content_or_credentials(api_server, monkeypatch):
    from mithai.telemetry import tracer as tracer_module

    server, handler = api_server
    handler.responses = [(200, _success())]
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracer_module, "_tracer", tracer_provider.get_tracer("test"))

    _provider(server).create_message(
        system="private-system",
        messages=[{"role": "user", "content": "private-prompt"}],
        tools=[{
            "name": "private_tool",
            "description": "private-description",
            "input_schema": {"type": "object"},
        }],
    )

    span = exporter.get_finished_spans()[0]
    serialized = json.dumps({"attributes": dict(span.attributes), "events": [e.name for e in span.events]})
    assert span.attributes["gen_ai.system"] == "openai_compatible"
    assert span.attributes["gen_ai.usage.input_tokens"] == 11
    for secret in ("private-system", "private-prompt", "private_tool", "secret-token"):
        assert secret not in serialized
