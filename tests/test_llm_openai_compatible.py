"""Contract tests for OpenAI-compatible chat-completions providers."""

import copy
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from mithai.llm.base import ProviderResponseError, ProviderUnavailableError
from mithai.llm.openai_compatible import OpenAICompatibleProvider


OPENAI_RESPONSE = {
    "id": "chatcmpl-openai",
    "model": "gpt-5.1",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
}

XAI_TOOL_RESPONSE = {
    "id": "chatcmpl-xai",
    "model": "grok-4-fast",
    "choices": [{
        "index": 0,
        "message": {
            "role": "assistant",
            "content": None,
            "reasoning_content": "provider-specific optional field",
            "tool_calls": [{
                "id": "call_7",
                "type": "function",
                "function": {"name": "weather", "arguments": "{\"city\":\"Paris\"}"},
            }],
        },
        "finish_reason": "tool_calls",
    }],
    "usage": {"prompt_tokens": 8, "completion_tokens": 5, "prompt_tokens_details": None},
}


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.server.requests.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(self.rfile.read(length)),
        })
        status, payload = self.server.responses.pop(0)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, format, *args):
        pass


@pytest.fixture
def api_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.responses = []
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()


def _provider(api_server, *, api_key="secret-key", retries=2):
    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=f"http://127.0.0.1:{api_server.server_port}/v1",
        model="contract-model",
        max_retries=retries,
        retry_backoff=0,
    )


def test_openai_text_response_and_deployed_auth_transport(api_server):
    api_server.responses.append((200, OPENAI_RESPONSE))
    response = _provider(api_server).create_message(
        system="You help.", messages=[{"role": "user", "content": "go"}], max_tokens=42
    )

    assert response.content == [{"type": "text", "text": "done"}]
    assert response.stop_reason == "end_turn"
    assert response.model == "gpt-5.1"
    assert response.usage == {"input_tokens": 11, "output_tokens": 3}
    request = api_server.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["authorization"] == "Bearer secret-key"
    assert request["body"]["messages"][0] == {"role": "system", "content": "You help."}
    assert request["body"]["max_tokens"] == 42


def test_xai_tool_response_and_tool_result_translation(api_server):
    api_server.responses.append((200, XAI_TOOL_RESPONSE))
    provider = _provider(api_server)
    response = provider.create_message(
        system="",
        messages=[
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "call_7", "name": "weather", "input": {"city": "Paris"}
            }]},
            {"role": "user", "content": [provider.format_tool_result("call_7", "sunny")]},
        ],
        tools=[{"name": "weather", "description": "Forecast", "input_schema": {"type": "object"}}],
    )

    assert response.content == [{
        "type": "tool_use", "id": "call_7", "name": "weather", "input": {"city": "Paris"}
    }]
    assert response.stop_reason == "tool_use"
    assert response.usage == {"input_tokens": 8, "output_tokens": 5}
    body = api_server.requests[0]["body"]
    assert body["tools"][0]["function"]["parameters"] == {"type": "object"}
    assert body["messages"] == [
        {"role": "assistant", "tool_calls": [{
            "id": "call_7", "type": "function",
            "function": {"name": "weather", "arguments": "{\"city\": \"Paris\"}"},
        }]},
        {"role": "tool", "tool_call_id": "call_7", "content": "sunny"},
    ]


def test_image_attachment_is_translated_to_data_url(api_server):
    api_server.responses.append((200, OPENAI_RESPONSE))

    _provider(api_server).create_message(system="", messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": "aW1n"
            }},
            {"type": "text", "text": "describe this"},
        ],
    }])

    assert api_server.requests[0]["body"]["messages"][-1]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}},
        {"type": "text", "text": "describe this"},
    ]


@pytest.mark.parametrize("missing", ["content", "usage"])
def test_missing_or_null_optional_response_fields(api_server, missing):
    payload = {"model": "grok", "choices": [{"message": {"content": None}, "finish_reason": None}]}
    if missing == "content":
        del payload["choices"][0]["message"]["content"]
    else:
        payload["usage"] = None
    api_server.responses.append((200, payload))

    response = _provider(api_server).create_message(system="", messages=[])
    assert response.content == []
    assert response.stop_reason == "end_turn"
    assert response.usage == {"input_tokens": 0, "output_tokens": 0}


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_and_usage_refusals_are_unavailable_and_redacted(api_server, status):
    secret = "secret-key"
    api_server.responses.append((status, {"error": {"message": "private response text " + secret}}))
    with pytest.raises(ProviderUnavailableError) as exc_info:
        _provider(api_server, api_key=secret).create_message(system="", messages=[])
    assert str(status) in str(exc_info.value)
    assert secret not in str(exc_info.value)
    assert "private response text" not in str(exc_info.value)


def test_overload_retries_then_raises_redacted_unavailable(api_server):
    api_server.responses.extend([(503, {"error": {"message": "sensitive"}})] * 3)
    with pytest.raises(ProviderUnavailableError, match="503"):
        _provider(api_server).create_message(system="", messages=[])
    assert len(api_server.requests) == 3


def test_other_400_is_response_error_without_response_text(api_server):
    api_server.responses.append((400, {"error": {"message": "invalid private payload"}}))
    with pytest.raises(ProviderResponseError) as exc_info:
        _provider(api_server).create_message(system="", messages=[])
    assert "400" in str(exc_info.value)
    assert "invalid private payload" not in str(exc_info.value)


def test_invalid_tool_arguments_is_response_error(api_server):
    payload = dict(XAI_TOOL_RESPONSE)
    payload["choices"] = [dict(XAI_TOOL_RESPONSE["choices"][0])]
    payload["choices"][0]["message"] = dict(XAI_TOOL_RESPONSE["choices"][0]["message"])
    payload["choices"][0]["message"]["tool_calls"] = [{
        "id": "call_bad", "type": "function", "function": {"name": "weather", "arguments": "{"}
    }]
    api_server.responses.append((200, payload))
    with pytest.raises(ProviderResponseError, match="invalid tool arguments"):
        _provider(api_server).create_message(system="", messages=[])


def test_truncated_tool_arguments_are_returned_for_engine_recovery(api_server):
    payload = copy.deepcopy(XAI_TOOL_RESPONSE)
    payload["choices"][0]["finish_reason"] = "length"
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"city":'
    api_server.responses.append((200, payload))

    response = _provider(api_server).create_message(system="", messages=[])

    assert response.stop_reason == "max_tokens"
    assert response.content == [{
        "type": "tool_use", "id": "call_7", "name": "weather", "input": {}
    }]
