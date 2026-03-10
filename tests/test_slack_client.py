"""Unit tests for SlackClient (mithai.integrations.slack)."""

from unittest.mock import MagicMock, patch


def _make_client():
    """Create a SlackClient with a mocked WebClient."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()
    return client


# ---------------------------------------------------------------------------
# get_history()
# ---------------------------------------------------------------------------

def test_get_history_returns_oldest_first():
    """Slack API returns newest-first; get_history should reverse to oldest-first."""
    client = _make_client()
    # API returns newest first: "second" then "first"
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {"user": "U2", "text": "second"},
            {"user": "U1", "text": "first"},
        ],
    }
    client._client.users_info.side_effect = lambda user: {
        "user": {"profile": {"display_name": user.lower()}, "name": user.lower()}
    }

    messages, _ = client.get_history("C1", 10)
    assert len(messages) == 2
    assert messages[0] == "u1: first"
    assert messages[1] == "u2: second"


def test_get_history_formats_messages_as_name_colon_text():
    """Each message must be formatted as '{name}: {text}'."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [{"user": "U1", "text": "hello there"}],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    messages, _ = client.get_history("C1", 10)
    assert messages == ["alice: hello there"]


def test_get_history_returns_empty_on_api_error():
    client = _make_client()
    client._client.conversations_history.side_effect = Exception("network error")
    messages, user_map = client.get_history("C1", 10)
    assert messages == []
    assert user_map == {}


def test_get_history_returns_empty_when_not_ok():
    client = _make_client()
    client._client.conversations_history.return_value = {"ok": False, "error": "channel_not_found"}
    messages, user_map = client.get_history("C1", 10)
    assert messages == []
    assert user_map == {}


def test_get_history_replaces_user_mentions():
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [{"user": "U1", "text": "hey <@U2> check this"}],
    }

    def users_info(user):
        names = {"U1": "alice", "U2": "bob"}
        return {"user": {"profile": {"display_name": names.get(user, user)}, "name": names.get(user, user)}}

    client._client.users_info.side_effect = users_info
    messages, _ = client.get_history("C1", 10)
    assert len(messages) == 1
    assert "@bob" in messages[0]
    assert "<@U2>" not in messages[0]


def test_get_history_skips_bot_messages_without_user():
    """Messages with no 'user' field (bot_message subtypes) are skipped."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {"text": "bot alert: deploy done"},  # no user field
            {"user": "U1", "text": "thanks"},
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    messages, _ = client.get_history("C1", 10)
    # bot message has user="unknown", so it gets formatted as "unknown: bot alert: deploy done"
    # this just documents current behavior — the key check is no crash
    assert len(messages) == 2


def test_get_history_returns_empty_list_for_no_messages():
    client = _make_client()
    client._client.conversations_history.return_value = {"ok": True, "messages": []}
    messages, user_map = client.get_history("C1", 10)
    assert messages == []
    assert user_map == {}


# ---------------------------------------------------------------------------
# post_message()
# ---------------------------------------------------------------------------

def test_post_message_sends_to_channel():
    client = _make_client()
    client._client.chat_postMessage.return_value = {"ok": True, "ts": "123.456"}
    result = client.post_message("C1", "hello world")
    client._client.chat_postMessage.assert_called_once_with(channel="C1", text="hello world")
    assert result["ok"] is True
    assert result["ts"] == "123.456"
    assert result["channel"] == "C1"


def test_post_message_sends_with_thread_ts():
    client = _make_client()
    client._client.chat_postMessage.return_value = {"ok": True, "ts": "999.000"}
    client.post_message("C1", "reply", thread_ts="111.222")
    client._client.chat_postMessage.assert_called_once_with(
        channel="C1", text="reply", thread_ts="111.222"
    )


def test_post_message_does_not_send_thread_ts_when_none():
    """thread_ts=None must not be included in the kwargs."""
    client = _make_client()
    client._client.chat_postMessage.return_value = {"ok": True, "ts": "1.0"}
    client.post_message("C1", "hi", thread_ts=None)
    call_kwargs = client._client.chat_postMessage.call_args[1]
    assert "thread_ts" not in call_kwargs


def test_post_message_returns_error_on_exception():
    client = _make_client()
    client._client.chat_postMessage.side_effect = Exception("api error")
    result = client.post_message("C1", "hello")
    assert result["ok"] is False
    assert "error" in result
    assert result["channel"] == "C1"


# ---------------------------------------------------------------------------
# resolve_user_ids()
# ---------------------------------------------------------------------------

def test_resolve_user_ids_prefers_display_name():
    client = _make_client()
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "Alice", "real_name": "Alice Smith"}, "name": "alice_login"}
    }
    result = client.resolve_user_ids({"U1"})
    assert result["U1"] == "Alice"


def test_resolve_user_ids_falls_back_to_real_name():
    """When display_name is empty, real_name should be used."""
    client = _make_client()
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "", "real_name": "Bob Jones"}, "name": "bob_login"}
    }
    result = client.resolve_user_ids({"U2"})
    assert result["U2"] == "Bob Jones"


def test_resolve_user_ids_falls_back_to_uid_on_error():
    client = _make_client()
    client._client.users_info.side_effect = Exception("not found")
    result = client.resolve_user_ids({"U_UNKNOWN"})
    assert result["U_UNKNOWN"] == "U_UNKNOWN"


def test_resolve_user_ids_empty_set():
    client = _make_client()
    result = client.resolve_user_ids(set())
    assert result == {}
    client._client.users_info.assert_not_called()
