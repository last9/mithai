"""Contract tests for the OpenAI-compatible provider."""

import json
import socket
import struct
import threading
import traceback
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

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


@pytest.fixture(params=["chat_completions", "responses"])
def any_api(request):
    return request.param


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


@pytest.mark.parametrize(("base_url", "token_field"), [
    ("https://api.openai.com/v1", "max_completion_tokens"),
    ("https://api.x.ai/v1", "max_tokens"),
])
def test_token_limit_field_matches_provider_endpoint(monkeypatch, base_url, token_field):
    requests = []

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def respond(request, timeout):
        requests.append((request, timeout))
        return Response(json.dumps(_success()).encode())

    monkeypatch.setattr("mithai.llm.openai_compatible.urlopen", respond)
    provider = OpenAICompatibleProvider(
        api_key="secret-token", model="current-model", base_url=base_url
    )

    provider.create_message(system="s", messages=[], max_tokens=23)

    body = json.loads(requests[0][0].data)
    assert body[token_field] == 23
    assert ({"max_tokens", "max_completion_tokens"} - {token_field}).isdisjoint(body)


def _responses_success(*, tool=False):
    output = [{
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "done"}],
    }]
    if tool:
        output = [
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "blob"},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "shell__run",
                "arguments": '{"command":"uptime"}',
            },
        ]
    return {
        "model": "gpt-test",
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def _ok(api):
    return _responses_success() if api == "responses" else _success()


def _openai_config(server, **openai):
    return {
        "llm": {
            "provider": "openai",
            "model": "gpt-test",
            "openai": {
                "api_key": "synthetic-key",
                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                "max_retries": 0,
                **openai,
            },
        },
    }


def test_default_config_sends_chat_completions(api_server):
    from mithai.cli.run_cmd import _create_llm

    server, handler = api_server
    handler.responses = [(200, _success())]

    _create_llm(_openai_config(server)).create_message(system="s", messages=[])

    _, _, path, body = handler.requests[0]
    assert path == "/v1/chat/completions"
    assert "reasoning_effort" not in body


def test_responses_config_round_trips_tool_call_with_reasoning(api_server):
    from mithai.cli.run_cmd import _create_llm

    server, handler = api_server
    handler.responses = [(200, _responses_success(tool=True)), (200, _responses_success())]
    provider = _create_llm(_openai_config(server, api="responses", reasoning_effort="medium"))
    tools = [{"name": "shell__run", "description": "Run", "input_schema": {"type": "object"}}]

    first = provider.create_message(system="s", messages=[], tools=tools, max_tokens=99)
    second = provider.create_message(
        system="s",
        messages=[
            {"role": "assistant", "content": first.content},
            {"role": "user", "content": [provider.format_tool_result("call_1", "ok")]},
        ],
        tools=tools,
    )

    paths = [request[2] for request in handler.requests]
    assert paths == ["/v1/responses", "/v1/responses"]
    first_body = handler.requests[0][3]
    assert first_body["max_output_tokens"] == 99
    assert first_body["reasoning"] == {"effort": "medium"}
    assert first.stop_reason == "tool_use"
    assert handler.requests[1][3]["input"] == [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "blob"},
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "shell__run",
            "arguments": '{"command":"uptime"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
    ]
    assert second.content == [{"type": "text", "text": "done"}]
    assert second.usage == {"input_tokens": 11, "output_tokens": 7}


def test_responses_reasoning_items_survive_into_next_user_turn(api_server):
    from mithai.adapters.base import IncomingMessage
    from mithai.cli.run_cmd import _create_llm
    from mithai.core.engine import Engine
    from mithai.state.memory import MemoryStateBackend

    server, handler = api_server
    final = _responses_success()
    final["output"].insert(
        0, {"type": "reasoning", "id": "rs_2", "summary": [], "encrypted_content": "blob2"}
    )
    handler.responses = [
        (200, _responses_success(tool=True)),
        (200, final),
        (200, _responses_success()),
    ]
    config = _openai_config(server, api="responses")
    engine = Engine(config, _create_llm(config), MemoryStateBackend(), skills={})
    adapter = Mock()
    adapter.fetch_thread_context.return_value = None

    for text in ("first", "second"):
        engine.handle(
            IncomingMessage(
                text=text, channel_id="C1", user_id="alice", platform="slack",
                thread_id="1.0", message_id="1.0",
            ),
            adapter,
        )

    replayed = handler.requests[2][3]["input"]
    assert [item.get("type") or item["role"] for item in replayed] == [
        "user",
        "reasoning",
        "function_call",
        "function_call_output",
        "reasoning",
        "assistant",
        "user",
    ]
    assert replayed[1] == {
        "type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "blob",
    }
    assert replayed[2]["call_id"] == replayed[3]["call_id"] == "call_1"
    assert replayed[4]["encrypted_content"] == "blob2"
    assert replayed[5] == {"role": "assistant", "content": "done"}
    assert replayed[6] == {"role": "user", "content": "second"}


def test_replayable_exchange_only_kept_for_provider_blocks():
    from mithai.core.engine import _replayable_exchange

    call = {"type": "tool_use", "id": "call_1", "name": "shell__run", "input": {}}
    result = {"type": "tool_result", "tool_use_id": "call_1", "content": "x" * 600}
    plain = [
        {"role": "assistant", "content": [call]},
        {"role": "user", "content": [result]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    assert _replayable_exchange(plain) is None

    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "b"}
    leaked = '<function_calls><invoke name="x"></invoke></function_calls>'
    persisted = _replayable_exchange([
        {"role": "assistant", "content": [reasoning, {"type": "text", "text": leaked}, call]},
        *plain[1:],
    ])

    assert persisted[0]["content"] == [reasoning, call]
    assert persisted[1]["content"][0]["content"] == "x" * 500
    assert result["content"] == "x" * 600


def test_chat_completions_passes_reasoning_effort_when_set(api_server):
    server, handler = api_server
    handler.responses = [(200, _success())]

    _provider(server, reasoning_effort="low").create_message(system="s", messages=[])

    assert handler.requests[0][3]["reasoning_effort"] == "low"


def test_chat_completions_maps_cached_and_reasoning_usage(api_server):
    server, handler = api_server
    body = _success()
    body["usage"].update({
        "prompt_tokens_details": {"cached_tokens": 4},
        "completion_tokens_details": {"reasoning_tokens": 5},
    })
    handler.responses = [(200, body)]

    response = _provider(server).create_message(system="s", messages=[])

    assert response.usage == {
        "input_tokens": 11,
        "output_tokens": 7,
        "cached_input_tokens": 4,
        "reasoning_tokens": 5,
    }


def test_unknown_api_is_rejected():
    with pytest.raises(ValueError, match="api must be one of"):
        OpenAICompatibleProvider(api_key="k", model="m", api="completions")


def test_create_llm_reports_unknown_api():
    import click

    from mithai.cli.run_cmd import _create_llm

    config = {"llm": {"provider": "openai", "model": "m", "openai": {"api_key": "k", "api": "x"}}}
    with pytest.raises(click.ClickException, match="llm.openai.api"):
        _create_llm(config)


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


def test_tool_results_precede_text_from_the_same_user_message(api_server):
    server, handler = api_server
    handler.responses = [(200, _success())]
    provider = _provider(server)

    provider.create_message(system="s", messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "both tools finished"},
            provider.format_tool_result("call_1", "one"),
            provider.format_tool_result("call_2", "two"),
        ],
    }])

    assert handler.requests[0][3]["messages"][1:] == [
        {"role": "tool", "tool_call_id": "call_1", "content": "one"},
        {"role": "tool", "tool_call_id": "call_2", "content": "two"},
        {"role": "user", "content": "both tools finished"},
    ]


def test_partial_arguments_preserve_truncation(api_server):
    server, handler = api_server
    body = _success(tool=True)
    choice = body["choices"][0]
    choice["finish_reason"] = "length"
    choice["message"]["tool_calls"][0]["function"]["arguments"] = '{"command":"uptime'
    handler.responses = [(200, body)]

    response = _provider(server).create_message(system="s", messages=[])

    assert response.stop_reason == "max_tokens"
    assert response.content[0]["id"] == "call_1"
    assert response.content[0]["input"] == {}


def test_malformed_arguments_on_completed_tool_call_are_rejected(api_server):
    server, handler = api_server
    body = _success(tool=True)
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{"
    handler.responses = [(200, body)]

    with pytest.raises(ProviderError, match="invalid response"):
        _provider(server).create_message(system="s", messages=[])


def test_engine_recovers_partial_tool_call_without_execution(api_server, tmp_skill_dir):
    from mithai.adapters.base import IncomingMessage
    from mithai.core.engine import Engine
    from mithai.core.skill_loader import load_skills
    from mithai.state.memory import MemoryStateBackend

    server, handler = api_server
    truncated = _success(tool=True)
    choice = truncated["choices"][0]
    choice["finish_reason"] = "length"
    function = choice["message"]["tool_calls"][0]["function"]
    function.update({"name": "test_skill__echo", "arguments": '{"message":"hello'})
    handler.responses = [(200, truncated), (200, _success())]
    provider = _provider(server)
    skills = load_skills([tmp_skill_dir])
    skills["test_skill"].handle = Mock()
    config = {
        "adapter": {"type": "cli"},
        "llm": {"provider": "openai", "model": "grok-test"},
        "skills": {"paths": [str(tmp_skill_dir)]},
    }
    engine = Engine(config, provider, MemoryStateBackend(), skills=skills)

    result = engine.handle(
        IncomingMessage(text="hello", channel_id="C1", user_id="U1", platform="cli"),
        Mock(),
    )

    assert result == "done"
    assert len(handler.requests) == 2
    skills["test_skill"].handle.assert_not_called()


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


def test_header_failure_hides_credentials(api_server):
    server, _ = api_server
    provider = OpenAICompatibleProvider(
        api_key="synthetic-secret\n",
        model="grok-test",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        max_retries=0,
    )

    with pytest.raises(ProviderError) as caught:
        provider.create_message(system="s", messages=[])

    assert "synthetic-secret" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("code", ["invalid_api_key", "token_expired"])
def test_refused_and_expired_authentication_are_classified(api_server, any_api, status, code):
    server, handler = api_server
    handler.responses = [(status, {"error": {"code": code, "message": "credential secret"}})]

    with pytest.raises(AvailabilityFailure) as caught:
        _provider(server, api=any_api).create_message(system="s", messages=[])

    assert caught.value.reason is UnavailableReason.AUTHENTICATION
    assert caught.value.status_code == status
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("status", [400, 429])
@pytest.mark.parametrize("code", ["insufficient_quota", "billing_hard_limit_reached", "credit_balance_too_low"])
def test_usage_limit_is_classified_without_retry(api_server, any_api, status, code):
    server, handler = api_server
    handler.responses = [(status, {"error": {"code": code, "message": "limit"}})]
    with pytest.raises(AvailabilityFailure) as caught:
        _provider(server, api=any_api, max_retries=2).create_message(system="s", messages=[])
    assert caught.value.reason is UnavailableReason.USAGE_LIMIT
    assert caught.value.status_code == status
    assert len(handler.requests) == 1


def test_default_timeout_allows_long_unstreamed_completions():
    from mithai.cli.run_cmd import _create_llm

    provider = _create_llm({
        "llm": {
            "provider": "openai",
            "model": "gpt-5.1",
            "openai": {"api_key": "synthetic-key"},
        }
    })

    assert provider._timeout == 600


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
def test_other_statuses_are_not_availability_failures(api_server, any_api, status, code):
    server, handler = api_server
    handler.responses = [(status, {"error": {"code": code, "message": "detail"}})] * 3
    with pytest.raises(ProviderError) as caught:
        _provider(server, api=any_api).create_message(system="s", messages=[])
    assert not isinstance(caught.value, AvailabilityFailure)
    assert caught.value.status_code == status


def test_http_529_is_classified_only_after_retry_exhaustion(api_server, any_api):
    server, handler = api_server
    handler.responses = [(529, {"error": {"message": "overloaded"}})] * 3

    with pytest.raises(AvailabilityFailure) as caught:
        _provider(server, api=any_api, max_retries=2).create_message(system="s", messages=[])

    assert caught.value.reason is UnavailableReason.OVERLOAD
    assert caught.value.status_code == 529
    assert len(handler.requests) == 3


def test_retry_can_recover_from_transient_failure(api_server, any_api):
    server, handler = api_server
    handler.responses = [(503, {"error": {"message": "busy"}}), (200, _ok(any_api))]
    response = _provider(server, api=any_api, max_retries=2).create_message(system="s", messages=[])
    assert response.stop_reason == "end_turn"
    assert len(handler.requests) == 2


def test_disconnect_retries(api_server, monkeypatch):
    server, handler = api_server
    original = handler.do_POST
    attempts = []

    def flaky(self):
        attempts.append(1)
        if len(attempts) == 1:
            self.close_connection = True
            return
        original(self)

    monkeypatch.setattr(handler, "do_POST", flaky)
    handler.responses = [(200, _success())]

    response = _provider(server, max_retries=2).create_message(system="s", messages=[])

    assert response.stop_reason == "end_turn"
    assert len(attempts) == 2


@pytest.mark.parametrize("status", [200, 503])
def test_body_disconnect_retries(api_server, monkeypatch, status):
    server, handler = api_server
    original = handler.do_POST
    attempts = []

    def interrupted(self):
        attempts.append(1)
        if len(attempts) > 1:
            return original(self)
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(status)
        self.send_header("Content-Length", "100")
        self.end_headers()
        self.wfile.write(b'{"error":')
        self.close_connection = True

    monkeypatch.setattr(handler, "do_POST", interrupted)
    handler.responses = [(200, _success())]

    response = _provider(server, max_retries=2).create_message(system="s", messages=[])

    assert response.stop_reason == "end_turn"
    assert len(attempts) == 2


def test_response_body_reset_retries(api_server, monkeypatch):
    server, handler = api_server
    original = handler.do_POST
    attempts = []

    def reset_during_body(self):
        attempts.append(1)
        if len(attempts) > 1:
            return original(self)
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Length", "100")
        self.end_headers()
        self.wfile.write(b'{"choices":')
        self.wfile.flush()
        self.connection.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_LINGER,
            struct.pack("ii", 1, 0),
        )
        self.connection.close()

    monkeypatch.setattr(handler, "do_POST", reset_during_body)
    handler.responses = [(200, _success())]

    response = _provider(server, max_retries=2).create_message(system="s", messages=[])

    assert response.stop_reason == "end_turn"
    assert len(attempts) == 2


@pytest.mark.parametrize("status", [200, 503])
def test_repeated_body_disconnects_exhaust_retries(api_server, monkeypatch, status):
    server, handler = api_server
    attempts = []

    def interrupted(self):
        attempts.append(1)
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(status)
        self.send_header("Content-Length", "100")
        self.end_headers()
        self.wfile.write(b'{"error":')
        self.close_connection = True

    monkeypatch.setattr(handler, "do_POST", interrupted)

    with pytest.raises(ProviderError, match="transport failed"):
        _provider(server, max_retries=2).create_message(system="s", messages=[])

    assert len(attempts) == 3


def test_repeated_disconnects_exhaust_retries(api_server, monkeypatch):
    server, handler = api_server
    attempts = []

    def disconnect(self):
        attempts.append(1)
        self.close_connection = True

    monkeypatch.setattr(handler, "do_POST", disconnect)

    with pytest.raises(ProviderError, match="transport failed"):
        _provider(server, max_retries=2).create_message(system="s", messages=[])

    assert len(attempts) == 3


def test_metrics_identify_provider(api_server, monkeypatch):
    from mithai.telemetry import metrics

    server, handler = api_server
    handler.responses = [(200, _success())]
    tokens, duration = Mock(), Mock()
    monkeypatch.setattr(metrics, "_token_usage", tokens)
    monkeypatch.setattr(metrics, "_op_duration", duration)

    _provider(server).create_message(system="s", messages=[])

    calls = tokens.record.call_args_list + duration.record.call_args_list
    assert len(calls) == 3
    assert {call.args[1]["gen_ai.system"] for call in calls} == {"openai_compatible"}


def test_telemetry_contains_metadata_but_no_content_or_credentials(
    api_server, monkeypatch, any_api
):
    from mithai.telemetry import tracer as tracer_module

    server, handler = api_server
    handler.responses = [(200, _ok(any_api))]
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracer_module, "_tracer", tracer_provider.get_tracer("test"))

    _provider(server, api=any_api).create_message(
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


def test_telemetry_exception_hides_credentials(api_server, monkeypatch):
    from mithai.telemetry import tracer as tracer_module

    server, _ = api_server
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracer_module, "_tracer", tracer_provider.get_tracer("test"))
    provider = OpenAICompatibleProvider(
        api_key="synthetic-secret\n",
        model="grok-test",
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        max_retries=0,
    )

    with pytest.raises(ProviderError):
        provider.create_message(system="s", messages=[])

    serialized = json.dumps([
        {"name": event.name, "attributes": dict(event.attributes)}
        for span in exporter.get_finished_spans()
        for event in span.events
    ])
    assert "synthetic-secret" not in serialized
