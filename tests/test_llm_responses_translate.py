"""Fixture-based translation tests for the OpenAI Responses API path."""

import json

import pytest

from mithai.llm._responses_translate import build_request, normalize_response

TOOLS = [{
    "name": "shell__run",
    "description": "Run a command",
    "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
}]

REASONING_ITEM = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [{"type": "summary_text", "text": "need uptime"}],
    "encrypted_content": "opaque-blob",
}

TOOL_CALL_RESPONSE = {
    "id": "resp_1",
    "model": "gpt-6-reasoning",
    "status": "completed",
    "output": [
        REASONING_ITEM,
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Checking.", "annotations": []}],
        },
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "shell__run",
            "arguments": '{"command":"uptime"}',
        },
    ],
    "usage": {
        "input_tokens": 40,
        "input_tokens_details": {"cached_tokens": 8},
        "output_tokens": 30,
        "output_tokens_details": {"reasoning_tokens": 20},
        "total_tokens": 70,
    },
}


def test_multi_turn_tool_conversation_round_trips_reasoning_items():
    first = normalize_response(TOOL_CALL_RESPONSE)

    assert first.content == [
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "need uptime"}],
            "encrypted_content": "opaque-blob",
        },
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "call_1", "name": "shell__run", "input": {"command": "uptime"}},
    ]
    assert first.stop_reason == "tool_use"
    assert first.model == "gpt-6-reasoning"
    assert first.usage == {
        "input_tokens": 40,
        "output_tokens": 30,
        "cached_input_tokens": 8,
        "reasoning_tokens": 20,
    }

    request = build_request(
        model="gpt-6-reasoning",
        system="system prompt",
        messages=[
            {"role": "user", "content": "how loaded is the host?"},
            {"role": "assistant", "content": first.content},
            {"role": "user", "content": [
                {"type": "text", "text": "tool finished"},
                {"type": "tool_result", "tool_use_id": "call_1", "content": "up 3 days"},
            ]},
        ],
        tools=TOOLS,
        max_tokens=512,
        reasoning_effort="high",
    )

    assert request == {
        "model": "gpt-6-reasoning",
        "instructions": "system prompt",
        "max_output_tokens": 512,
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "reasoning": {"effort": "high"},
        "input": [
            {"role": "user", "content": "how loaded is the host?"},
            REASONING_ITEM,
            {"role": "assistant", "content": "Checking."},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "shell__run",
                "arguments": '{"command":"uptime"}',
            },
            {"type": "function_call_output", "call_id": "call_1", "output": "up 3 days"},
            {"role": "user", "content": "tool finished"},
        ],
        "tools": [{
            "type": "function",
            "name": "shell__run",
            "description": "Run a command",
            "parameters": TOOLS[0]["input_schema"],
        }],
    }


def test_assistant_message_phase_round_trips_and_is_omitted_when_absent():
    response = normalize_response({
        "model": "m",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "Checking."}],
            },
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Up 3 days."}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "No phase."}],
            },
        ],
    })

    request = build_request(
        model="m",
        system="s",
        messages=[{"role": "assistant", "content": response.content}],
        tools=None,
        max_tokens=10,
        reasoning_effort=None,
    )

    assert request["input"] == [
        {"role": "assistant", "content": "Checking.", "phase": "commentary"},
        {"role": "assistant", "content": "Up 3 days.", "phase": "final_answer"},
        {"role": "assistant", "content": "No phase."},
    ]


def test_request_omits_optional_fields_when_unset():
    request = build_request(
        model="m", system="s", messages=[], tools=None, max_tokens=10, reasoning_effort=None
    )

    assert request == {
        "model": "m",
        "instructions": "s",
        "max_output_tokens": 10,
        "store": False,
        "include": ["reasoning.encrypted_content"],
        "input": [],
    }


def test_structured_tool_result_content_is_flattened_to_text():
    request = build_request(
        model="m",
        system="s",
        messages=[{"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": "call_1",
            "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        }]}],
        tools=None,
        max_tokens=10,
        reasoning_effort=None,
    )

    assert request["input"] == [{"type": "function_call_output", "call_id": "call_1", "output": "ab"}]


def test_user_images_become_input_images_in_block_order():
    request = build_request(
        model="m",
        system="s",
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
            {"type": "text", "text": "what is this graph?"},
        ]}],
        tools=None,
        max_tokens=10,
        reasoning_effort=None,
    )

    assert request["input"] == [{"role": "user", "content": [
        {"type": "input_image", "image_url": "data:image/png;base64,AAA"},
        {"type": "input_text", "text": "what is this graph?"},
    ]}]


def test_final_answer_maps_to_end_turn_without_optional_usage():
    response = normalize_response({
        "model": "m",
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "done"}],
        }],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    })

    assert response.content == [{"type": "text", "text": "done"}]
    assert response.stop_reason == "end_turn"
    assert response.usage == {"input_tokens": 3, "output_tokens": 2}


def test_refusal_is_returned_as_text():
    response = normalize_response({
        "model": "m",
        "status": "completed",
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "refusal", "refusal": "cannot help"}],
        }],
    })

    assert response.content == [{"type": "text", "text": "cannot help"}]
    assert response.usage == {"input_tokens": 0, "output_tokens": 0}


def test_output_token_truncation_keeps_call_ids_without_parsing_partial_arguments():
    body = json.loads(json.dumps(TOOL_CALL_RESPONSE))
    body["status"] = "incomplete"
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    body["output"][2]["arguments"] = '{"command":"upt'

    response = normalize_response(body)

    assert response.stop_reason == "max_tokens"
    assert response.content[-1] == {
        "type": "tool_use", "id": "call_1", "name": "shell__run", "input": {},
    }


def test_other_incomplete_reasons_pass_through():
    response = normalize_response({
        "model": "m",
        "status": "incomplete",
        "incomplete_details": {"reason": "content_filter"},
        "output": [],
    })

    assert response.stop_reason == "content_filter"


def test_failed_response_is_invalid():
    with pytest.raises(ValueError):
        normalize_response({"model": "m", "status": "failed", "output": []})


def test_malformed_arguments_on_completed_call_are_invalid():
    body = json.loads(json.dumps(TOOL_CALL_RESPONSE))
    body["output"][2]["arguments"] = "{"

    with pytest.raises(ValueError):
        normalize_response(body)
