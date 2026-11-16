from unittest.mock import Mock
import traceback

import pytest
import requests

from mithai.adapters.telegram import TelegramAdapter
from mithai.human.mcp import HumanRequest


def message(chat=111, user=222, text="hello"):
    return {"message": {"chat": {"id": chat}, "from": {"id": user},
                        "message_id": 1, "text": text}}


@pytest.mark.parametrize("allowed,dispatched", [([], False), (["111"], True), (["222"], False), (None, True)])
def test_chat_allowlist(allowed, dispatched):
    adapter = TelegramAdapter("token", allowed)
    handler = Mock(return_value="ok")

    def api(method, **kwargs):
        if method == "getUpdates":
            adapter.stop()
            return [{"update_id": 1, **message()}]
        return {}

    adapter._api = api
    adapter.start(handler)
    assert handler.called is dispatched


@pytest.mark.parametrize("level", ["approve", "confirm"])
@pytest.mark.parametrize("approved", [False, True])
def test_approval_requires_requesting_user_and_chat(level, approved):
    adapter = TelegramAdapter("token", ["111"])
    request = HumanRequest("shell__run", {"name": "resource"}, level, "test", "req")
    decisions = []
    updates = [
        {"update_id": 1, **message()},
        {"update_id": 2, **(
            {"callback_query": {"id": "wrong-chat", "from": {"id": 222}, "message": {"chat": {"id": 999}}, "data": "approve:req"}}
            if level == "approve" else message(999, 222, "resource")
        )},
        {"update_id": 3, **(
            {"callback_query": {"id": "wrong-user", "from": {"id": 333}, "message": {"chat": {"id": 111}}, "data": "approve:req"}}
            if level == "approve" else message(111, 333, "resource")
        )},
        {"update_id": 4, **(
            {"callback_query": {"id": "right", "from": {"id": 222}, "message": {"chat": {"id": 111}}, "data": f"{'approve' if approved else 'deny'}:req"}}
            if level == "approve" else message(111, 222, "resource" if approved else "deny")
        )},
    ]

    def api(method, **kwargs):
        if method == "getUpdates":
            update = updates.pop(0)
            if update["update_id"] == 1:
                adapter.stop()
            return [update]
        return {}

    adapter._api = api
    adapter.start(lambda msg, source: decisions.append(source.request_human_approval(request, msg.channel_id)) or "done")
    assert decisions == [approved]


def test_approval_without_originating_user_is_denied():
    adapter = TelegramAdapter("token", ["111"])
    adapter._api = Mock()
    request = HumanRequest("shell__run", {}, "approve", "test")
    assert adapter.request_human_approval(request, "111") is False
    adapter._api.assert_not_called()


def test_approval_prompt_escapes_description():
    adapter = TelegramAdapter("token", ["111"])
    adapter._active_sender = ("111", "222")
    adapter._api = Mock()
    adapter._wait_for_callback = Mock(return_value=True)
    request = HumanRequest("shell__run", {}, "approve", "test <danger> & `quoted`", "req")
    assert adapter.request_human_approval(request, "111")
    kwargs = adapter._api.call_args.kwargs
    assert kwargs["parse_mode"] == "HTML"
    assert "test &lt;danger&gt; &amp; `quoted`" in kwargs["text"]


@pytest.mark.parametrize("action,expected", [("approve", True), ("deny", False)])
def test_valid_callback_removes_buttons(action, expected):
    adapter = TelegramAdapter("token", ["111"])
    adapter._active_sender = ("111", "222")
    request = HumanRequest("shell__run", {}, "approve", "test <danger> & `quoted`", "req")
    updates = [{"update_id": 1, "callback_query": {
        "id": "wrong", "from": {"id": 333}, "message": {"chat": {"id": 111}, "message_id": 9}, "data": "approve:req"
    }}, {"update_id": 2, "callback_query": {
        "id": "right", "from": {"id": 222}, "message": {"chat": {"id": 111}, "message_id": 9}, "data": f"{action}:req"
    }}]
    adapter._api = Mock(side_effect=lambda method, **kwargs: [updates.pop(0)] if method == "getUpdates" else {})
    assert adapter._wait_for_callback("111", request) is expected
    from unittest.mock import call
    assert adapter._api.call_args_list[-2:] == [
        call("answerCallbackQuery", callback_query_id="right"),
        call("editMessageText", chat_id="111", message_id=9,
             text=f"<b>{'Approved' if expected else 'Denied'}</b>\n\n<pre>test &lt;danger&gt; &amp; `quoted`</pre>",
             parse_mode="HTML", reply_markup={"inline_keyboard": []}),
    ]


def test_callback_decision_survives_keyboard_cleanup_failure():
    adapter = TelegramAdapter("token", ["111"])
    adapter._active_sender = ("111", "222")
    request = HumanRequest("shell__run", {}, "approve", "test", "req")
    def api(method, **kwargs):
        if method == "getUpdates":
            return [{"update_id": 1, "callback_query": {"id": "right", "from": {"id": 222},
                    "message": {"chat": {"id": 111}, "message_id": 9}, "data": "approve:req"}}]
        if method == "editMessageText":
            raise RuntimeError("Telegram API failed")
        return {}
    adapter._api = api
    assert adapter._wait_for_callback("111", request) is True


@pytest.mark.parametrize("level", ["approve", "confirm"])
def test_approval_poll_retries_transient_network_failure(monkeypatch, level):
    adapter = TelegramAdapter("token", ["111"])
    adapter._active_sender = ("111", "222")
    request = HumanRequest("shell__run", {"name": "resource"}, level, "test", "req")
    update = ({"callback_query": {"id": "right", "from": {"id": 222},
               "message": {"chat": {"id": 111}}, "data": "approve:req"}}
              if level == "approve" else message(111, 222, "resource"))
    response = Mock()
    response.json.return_value = {"ok": True, "result": [{"update_id": 1, **update}]}
    monkeypatch.setattr("mithai.adapters.telegram.requests.post", Mock(side_effect=[requests.Timeout("sensitive-token"), response, response]))
    sleep = Mock()
    monkeypatch.setattr("mithai.adapters.telegram.time.sleep", sleep)
    wait = adapter._wait_for_callback if level == "approve" else adapter._wait_for_confirmation
    assert wait("111", request) is True
    sleep.assert_called_once()


@pytest.mark.parametrize("level", ["approve", "confirm"])
def test_approval_poll_does_not_retry_permanent_http_error(monkeypatch, level):
    adapter = TelegramAdapter("token", ["111"])
    adapter._active_sender = ("111", "222")
    request = HumanRequest("shell__run", {}, level, "test", "req")
    response = Mock()
    response.status_code = 401
    response.raise_for_status.side_effect = requests.HTTPError("bad auth", response=response)
    post = Mock(return_value=response)
    monkeypatch.setattr("mithai.adapters.telegram.requests.post", post)
    wait = adapter._wait_for_callback if level == "approve" else adapter._wait_for_confirmation
    with pytest.raises(RuntimeError):
        wait("111", request)
    assert post.call_count == 1


@pytest.mark.parametrize("level", ["approve", "confirm"])
def test_approval_poll_stops_retrying_at_deadline(monkeypatch, level):
    adapter = TelegramAdapter("token", ["111"], poll_interval=300)
    request = HumanRequest("shell__run", {}, level, "test", "req")
    now = [0]
    monkeypatch.setattr("mithai.adapters.telegram.time.time", lambda: now[0])
    monkeypatch.setattr("mithai.adapters.telegram.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    post = Mock(side_effect=requests.Timeout("sensitive-token"))
    monkeypatch.setattr("mithai.adapters.telegram.requests.post", post)
    wait = adapter._wait_for_callback if level == "approve" else adapter._wait_for_confirmation
    assert wait("111", request) is False
    assert now[0] == 300
    assert post.call_count == 1


@pytest.mark.parametrize("action,expected", [("approve", True), ("deny", False)])
def test_valid_callback_decision_survives_acknowledgement_failure(action, expected):
    adapter = TelegramAdapter("token", ["111"])
    adapter._active_sender = ("111", "222")
    request = HumanRequest("shell__run", {}, "approve", "test", "req")

    def api(method, **kwargs):
        if method == "getUpdates":
            return [{"update_id": 1, "callback_query": {"id": "callback", "from": {"id": 222},
                     "message": {"chat": {"id": 111}}, "data": f"{action}:req"}}]
        raise RuntimeError("Telegram API failed")

    adapter._api = api
    assert adapter._wait_for_callback("111", request) is expected


@pytest.mark.parametrize("failure", ["connection", "http", "api"])
def test_api_errors_never_expose_bot_token(monkeypatch, failure):
    token = "sensitive-token"
    adapter = TelegramAdapter(token, ["111"])
    response = Mock()
    response.json.return_value = {"ok": False, "description": f"bad {token}"}
    if failure == "connection":
        post = Mock(side_effect=requests.ConnectionError(f"connection failed for {token}"))
    else:
        post = Mock(return_value=response)
        if failure == "http":
            response.raise_for_status.side_effect = requests.HTTPError(f"bad url {token}")
    monkeypatch.setattr("mithai.adapters.telegram.requests.post", post)
    with pytest.raises(RuntimeError) as error:
        adapter._api("getUpdates")
    assert token not in "".join(traceback.format_exception(error.value))


def test_outer_batch_does_not_rewind_offset_after_approval_poll():
    adapter = TelegramAdapter("token", ["111"])
    request = HumanRequest("shell__run", {}, "approve", "test", "req")
    calls = 0

    def api(method, **kwargs):
        nonlocal calls
        if method != "getUpdates":
            return {}
        calls += 1
        if calls == 1:
            adapter.stop()
            return [{"update_id": 1, **message()}, {"update_id": 2, **message()}]
        return [{"update_id": 3, "callback_query": {"id": "callback", "from": {"id": 222},
                 "message": {"chat": {"id": 111}}, "data": "approve:req"}}]

    adapter._api = api
    handled = []
    def on_message(msg, source):
        handled.append(msg)
        if len(handled) == 1:
            assert source.request_human_approval(request, msg.channel_id)
        return "ok"
    adapter.start(on_message)
    assert len(handled) == 2
    assert adapter._offset == 4
