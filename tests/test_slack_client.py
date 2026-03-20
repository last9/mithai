"""Unit tests for SlackClient (mithai.integrations.slack)."""

from unittest.mock import MagicMock


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
    """Messages with no 'user' field (bot/system messages) must be excluded from output."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {"text": "bot alert: deploy done"},  # no user field — should be skipped
            {"user": "U1", "text": "thanks"},
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    messages, _ = client.get_history("C1", 10)
    assert len(messages) == 1
    assert messages[0] == "alice: thanks"


def test_get_history_returns_empty_list_for_no_messages():
    client = _make_client()
    client._client.conversations_history.return_value = {"ok": True, "messages": []}
    messages, user_map = client.get_history("C1", 10)
    assert messages == []
    assert user_map == {}


def test_get_history_uses_block_text_for_messages_with_section_blocks():
    """A message with Block Kit section blocks must use block text, not truncated msg['text']."""
    client = _make_client()
    full_text = "Deployment complete:\n- service-a: ok\n- service-b: ok\n- All health checks passed."
    truncated_text = "Deployment complete:\n- service-a: ok..."
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {
                "user": "U1",
                "text": truncated_text,
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": full_text}},
                ],
            }
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    messages, _ = client.get_history("C1", 10)
    assert len(messages) == 1
    assert full_text in messages[0]
    assert truncated_text not in messages[0]


def test_get_history_falls_back_to_text_when_no_blocks():
    """Messages without blocks must still use msg['text'] as before."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [{"user": "U1", "text": "plain message, no blocks"}],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    messages, _ = client.get_history("C1", 10)
    assert messages == ["alice: plain message, no blocks"]


def test_get_history_resolves_mentions_in_block_text():
    """User mentions (<@U123>) inside block text must be resolved to display names."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {
                "user": "U1",
                "text": "hey <@U2> truncated...",
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": "hey <@U2> can you review this?"}},
                ],
            }
        ],
    }
    client._client.users_info.side_effect = lambda user: {
        "user": {"profile": {"display_name": user.lower()}, "name": user.lower()}
    }

    messages, _ = client.get_history("C1", 10)
    assert len(messages) == 1
    assert "@u2" in messages[0]
    assert "<@U2>" not in messages[0]


def test_get_history_regular_rich_text_blocks_use_msg_text():
    """Modern Slack stores user messages with rich_text blocks (uses 'elements', not 'text').
    Must fall back to msg['text'] which is full and untruncated for human messages."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {
                "user": "U1",
                "text": "can you check the logs?",
                "blocks": [
                    {
                        "type": "rich_text",
                        "elements": [
                            {
                                "type": "rich_text_section",
                                "elements": [{"type": "text", "text": "can you check the logs?"}],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "bob"}, "name": "bob"}
    }

    messages, _ = client.get_history("C1", 10)
    assert messages == ["bob: can you check the logs?"]


def test_get_history_extracts_section_fields_when_no_text():
    """Section blocks may carry content in 'fields' instead of 'text' (e.g. status/approval cards).
    _extract_message_text must include fields text so get_history doesn't fall back to truncated fallback."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {
                "user": "U1",
                "text": "Approval request...",  # truncated fallback
                "blocks": [
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": "*Status:*\nPending"},
                            {"type": "mrkdwn", "text": "*Reviewer:*\nalice"},
                        ],
                        # no 'text' key — fields only
                    }
                ],
            }
        ],
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "bot"}, "name": "bot"}
    }

    messages, _ = client.get_history("C1", 10)
    assert len(messages) == 1
    assert "*Status:*" in messages[0]
    assert "*Reviewer:*" in messages[0]
    assert "Pending" in messages[0]


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


def test_post_message_ignores_invalid_thread_ts():
    """LLM may hallucinate a thread_ts like Long.MAX_VALUE — must not send it to Slack."""
    client = _make_client()
    client._client.chat_postMessage.return_value = {"ok": True, "ts": "1.0"}
    client.post_message("C1", "hello", thread_ts="9223372036854775807")
    call_kwargs = client._client.chat_postMessage.call_args[1]
    assert "thread_ts" not in call_kwargs


def test_post_message_accepts_valid_thread_ts():
    client = _make_client()
    client._client.chat_postMessage.return_value = {"ok": True, "ts": "1.0"}
    client.post_message("C1", "hello", thread_ts="1234567890.123456")
    call_kwargs = client._client.chat_postMessage.call_args[1]
    assert call_kwargs["thread_ts"] == "1234567890.123456"


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


# ---------------------------------------------------------------------------
# get_members()
# ---------------------------------------------------------------------------

def test_get_members_returns_sorted_list():
    """Members should be returned sorted alphabetically by name."""
    client = _make_client()
    client._client.conversations_members.return_value = {
        "ok": True,
        "members": ["U1", "U2", "U3"],
        "response_metadata": {"next_cursor": ""},
    }

    def users_info(user):
        names = {"U1": "charlie", "U2": "alice", "U3": "bob"}
        return {"user": {"profile": {"display_name": names[user]}, "name": names[user]}}

    client._client.users_info.side_effect = users_info
    members = client.get_members("C1")

    assert len(members) == 3
    assert members[0]["name"] == "alice"
    assert members[1]["name"] == "bob"
    assert members[2]["name"] == "charlie"
    assert all("id" in m and "name" in m for m in members)


def test_get_members_paginates():
    """get_members must follow next_cursor until exhausted."""
    client = _make_client()
    client._client.conversations_members.side_effect = [
        {"ok": True, "members": ["U1"], "response_metadata": {"next_cursor": "cur1"}},
        {"ok": True, "members": ["U2"], "response_metadata": {"next_cursor": ""}},
    ]

    def users_info(user):
        names = {"U1": "alice", "U2": "bob"}
        return {"user": {"profile": {"display_name": names[user]}, "name": names[user]}}

    client._client.users_info.side_effect = users_info
    members = client.get_members("C1")

    assert len(members) == 2
    assert client._client.conversations_members.call_count == 2
    # Second call must include the cursor
    second_call_kwargs = client._client.conversations_members.call_args_list[1][1]
    assert second_call_kwargs["cursor"] == "cur1"


def test_get_members_returns_empty_on_api_error():
    client = _make_client()
    client._client.conversations_members.side_effect = Exception("network error")
    members = client.get_members("C1")
    assert members == []


def test_get_members_returns_empty_on_not_ok():
    client = _make_client()
    client._client.conversations_members.return_value = {
        "ok": False,
        "error": "channel_not_found",
    }
    members = client.get_members("C1")
    assert members == []


def test_get_members_resolves_display_names():
    """Member dicts must use resolved display names, not raw user IDs."""
    client = _make_client()
    client._client.conversations_members.return_value = {
        "ok": True,
        "members": ["UABC"],
        "response_metadata": {"next_cursor": ""},
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "Dana"}, "name": "dana_login"}
    }
    members = client.get_members("C1")
    assert len(members) == 1
    assert members[0]["id"] == "UABC"
    assert members[0]["name"] == "Dana"


# ---------------------------------------------------------------------------
# download_images()
# ---------------------------------------------------------------------------

def _make_client_with_token():
    """Create a SlackClient with a mocked WebClient and a real token."""
    from mithai.integrations.slack import SlackClient
    client = SlackClient.__new__(SlackClient)
    client._client = MagicMock()
    client._token = "xoxb-test"
    return client


def test_download_images_returns_base64_for_supported_type(monkeypatch):
    """PNG files are downloaded and returned as base64."""
    import urllib.request
    import base64

    client = _make_client_with_token()
    fake_bytes = b"\x89PNG\r\n"

    class FakeResp:
        def read(self): return fake_bytes
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp())

    files = [{"mimetype": "image/png", "url_private": "https://files.slack.com/img.png"}]
    result = client.download_images(files)

    assert len(result) == 1
    assert result[0]["media_type"] == "image/png"
    assert result[0]["data"] == base64.b64encode(fake_bytes).decode("ascii")


def test_download_images_skips_non_image_files(monkeypatch):
    """PDF and other non-image files are silently skipped."""
    client = _make_client_with_token()
    files = [
        {"mimetype": "application/pdf", "url_private": "https://files.slack.com/doc.pdf"},
        {"mimetype": "text/plain", "url_private": "https://files.slack.com/notes.txt"},
    ]
    result = client.download_images(files)
    assert result == []


def test_download_images_skips_missing_url():
    """Files without url_private are skipped without error."""
    client = _make_client_with_token()
    files = [{"mimetype": "image/png"}]  # no url_private
    result = client.download_images(files)
    assert result == []


def test_download_images_handles_download_error(monkeypatch):
    """Network errors are swallowed — returns empty list, does not raise."""
    import urllib.request
    client = _make_client_with_token()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(OSError("timeout")))

    files = [{"mimetype": "image/jpeg", "url_private": "https://files.slack.com/img.jpg"}]
    result = client.download_images(files)
    assert result == []


def test_download_images_handles_multiple_images(monkeypatch):
    """Multiple image files are downloaded and base64-encoded independently."""
    import base64
    import urllib.request

    client = _make_client_with_token()

    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-data"
    jpg_bytes = b"\xff\xd8\xff\xe0fake-jpg-data"

    responses = iter([png_bytes, jpg_bytes])

    class FakeResp:
        def __init__(self, data): self._data = data
        def read(self): return self._data
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResp(next(responses)))

    files = [
        {"mimetype": "image/png", "url_private": "https://files.slack.com/a.png"},
        {"mimetype": "image/jpeg", "url_private": "https://files.slack.com/b.jpg"},
    ]
    result = client.download_images(files)
    assert len(result) == 2
    assert result[0] == {"media_type": "image/png", "data": base64.b64encode(png_bytes).decode("ascii")}
    assert result[1] == {"media_type": "image/jpeg", "data": base64.b64encode(jpg_bytes).decode("ascii")}
