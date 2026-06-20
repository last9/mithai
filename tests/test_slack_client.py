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


def test_get_history_includes_bot_messages_without_user():
    """Bot/integration posts without a user field must be included in output."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {"user": "U1", "text": "thanks", "ts": "1718000001.000100"},
            {
                "bot_id": "B1",
                "username": "ClickUp",
                "text": "New Feedback Submitted",
                "ts": "1718000000.000100",
            },
        ],
        "response_metadata": {"next_cursor": ""},
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    messages, _ = client.get_history("C1", 10)
    assert len(messages) == 2
    assert messages[0] == "ClickUp: New Feedback Submitted"
    assert messages[1] == "alice: thanks"


def test_get_history_window_paginates():
    client = _make_client()
    client._client.conversations_history.side_effect = [
        {
            "ok": True,
            "messages": [{"user": "U2", "text": "second", "ts": "2.0"}],
            "response_metadata": {"next_cursor": "cur1"},
        },
        {
            "ok": True,
            "messages": [{"user": "U1", "text": "first", "ts": "1.0"}],
            "response_metadata": {"next_cursor": ""},
        },
    ]

    def users_info(user):
        names = {"U1": "alice", "U2": "bob"}
        return {"user": {"profile": {"display_name": names[user]}, "name": names[user]}}

    client._client.users_info.side_effect = users_info
    window = client.get_history_window("C1", limit=10, max_pages=5)

    assert window["coverage"]["pages_fetched"] == 2
    assert window["coverage"]["count"] == 2
    assert window["messages"][0]["text"] == "first"
    assert window["messages"][1]["text"] == "second"
    assert client._client.conversations_history.call_count == 2


def test_get_history_window_stops_at_total_limit():
    """limit applies across all pages, not per page."""
    client = _make_client()
    client._client.conversations_history.side_effect = [
        {
            "ok": True,
            "messages": [
                {"user": "U3", "text": "third", "ts": "3.0"},
                {"user": "U2", "text": "second", "ts": "2.0"},
                {"user": "U1", "text": "first", "ts": "1.0"},
            ],
            "response_metadata": {"next_cursor": "cur1"},
        },
        {
            "ok": True,
            "messages": [{"user": "U0", "text": "zeroth", "ts": "0.0"}],
            "response_metadata": {"next_cursor": ""},
        },
    ]

    def users_info(user):
        names = {"U1": "a", "U2": "b", "U3": "c", "U0": "z"}
        return {"user": {"profile": {"display_name": names[user]}, "name": names[user]}}

    client._client.users_info.side_effect = users_info
    window = client.get_history_window("C1", limit=3, max_pages=5)

    assert window["coverage"]["count"] == 3
    assert window["coverage"]["has_more"] is True
    assert window["coverage"]["truncated_reason"] == "limit"
    assert client._client.conversations_history.call_count == 1
    first_call_limit = client._client.conversations_history.call_args_list[0][1]["limit"]
    assert first_call_limit == 3


def test_get_history_window_shrinks_follow_up_page_limit():
    client = _make_client()
    client._client.conversations_history.side_effect = [
        {
            "ok": True,
            "messages": [
                {"user": "U3", "text": "third", "ts": "3.0"},
                {"user": "U2", "text": "second", "ts": "2.0"},
            ],
            "response_metadata": {"next_cursor": "cur1"},
        },
        {
            "ok": True,
            "messages": [
                {"user": "U1", "text": "first", "ts": "1.0"},
                {"user": "U0", "text": "zeroth", "ts": "0.0"},
                {"user": "U9", "text": "ninth", "ts": "0.5"},
            ],
            "response_metadata": {"next_cursor": ""},
        },
    ]

    def users_info(user):
        names = {"U1": "a", "U2": "b", "U3": "c", "U0": "z", "U9": "n"}
        return {"user": {"profile": {"display_name": names[user]}, "name": names[user]}}

    client._client.users_info.side_effect = users_info
    window = client.get_history_window("C1", limit=5, max_pages=5)

    assert window["coverage"]["count"] == 5
    assert window["coverage"]["has_more"] is False
    assert client._client.conversations_history.call_count == 2
    second_call_limit = client._client.conversations_history.call_args_list[1][1]["limit"]
    assert second_call_limit == 3


def test_get_history_window_extracts_clickup_links():
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {
                "username": "ClickUp",
                "text": "Feedback: game lag",
                "attachments": [{"title_link": "https://app.clickup.com/t/abc123"}],
                "ts": "1718000000.000100",
            },
        ],
        "response_metadata": {"next_cursor": ""},
    }

    window = client.get_history_window("C1", limit=10)
    assert window["messages"][0]["links"] == ["https://app.clickup.com/t/abc123"]


def _window_for_text(text: str) -> dict:
    """Build a window from a single bot message with the given raw text."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [{"username": "bot", "text": text, "ts": "1.0"}],
        "response_metadata": {"next_cursor": ""},
    }
    return client.get_history_window("C1", limit=10)


def test_extract_links_bare_urls_from_any_tool():
    """Bare URLs (unwrapped by Slack, e.g. in bot/attachment text) are extracted
    regardless of which tool they point at."""
    window = _window_for_text(
        "see https://github.com/last9/mithai/pull/99 and "
        "https://linear.app/example/issue/ABC-1 and "
        "https://app.clickup.com/t/abc123"
    )
    assert window["messages"][0]["links"] == [
        "https://github.com/last9/mithai/pull/99",
        "https://linear.app/example/issue/ABC-1",
        "https://app.clickup.com/t/abc123",
    ]


def test_extract_links_trims_trailing_punctuation():
    window = _window_for_text(
        "done: https://github.com/last9/mithai/pull/99. also https://linear.app/x/FDE-1, ok?"
    )
    assert window["messages"][0]["links"] == [
        "https://github.com/last9/mithai/pull/99",
        "https://linear.app/x/FDE-1",
    ]


def test_extract_links_keeps_balanced_parens_in_url():
    """Wikipedia-style URLs with balanced parens keep them; a closing paren that
    just wraps the URL is trimmed."""
    window = _window_for_text(
        "ref https://en.wikipedia.org/wiki/Foo_(bar) and "
        "(see https://github.com/last9/mithai)"
    )
    assert window["messages"][0]["links"] == [
        "https://en.wikipedia.org/wiki/Foo_(bar)",
        "https://github.com/last9/mithai",
    ]


def test_extract_links_slack_formatted_not_duplicated_by_bare_scan():
    """Slack-formatted <url|label> links must not also be picked up (or mangled)
    by the bare-URL scan."""
    window = _window_for_text(
        "<https://github.com/last9/mithai/pull/99|PR 99> and bare https://linear.app/x/FDE-1"
    )
    assert window["messages"][0]["links"] == [
        "https://github.com/last9/mithai/pull/99",
        "https://linear.app/x/FDE-1",
    ]


def test_extract_links_dedupes_across_sources():
    """Same URL appearing as attachment link, Slack-formatted, and bare appears once."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {
                "username": "bot",
                "text": "<https://app.clickup.com/t/abc123|task> https://app.clickup.com/t/abc123",
                "attachments": [{"title_link": "https://app.clickup.com/t/abc123"}],
                "ts": "1.0",
            },
        ],
        "response_metadata": {"next_cursor": ""},
    }
    window = client.get_history_window("C1", limit=10)
    assert window["messages"][0]["links"] == ["https://app.clickup.com/t/abc123"]


def test_extract_links_ignores_non_http_schemes():
    window = _window_for_text("mail me mailto:a@b.c or ftp://x.y/z but https://ok.io/a works")
    assert window["messages"][0]["links"] == ["https://ok.io/a"]


def test_extract_links_unescapes_html_entities_in_urls():
    """Slack escapes & as &amp; in message text; extracted URLs must be usable."""
    window = _window_for_text(
        "<https://a.io/q?x=1&amp;y=2|res> and bare https://b.io/q?a=1&amp;b=2"
    )
    assert window["messages"][0]["links"] == [
        "https://a.io/q?x=1&y=2",
        "https://b.io/q?a=1&b=2",
    ]


def test_extract_links_trims_mrkdwn_emphasis_and_brackets():
    window = _window_for_text("see *https://a.io/x* or [https://b.io/y] or _https://c.io/z_")
    assert window["messages"][0]["links"] == [
        "https://a.io/x",
        "https://b.io/y",
        "https://c.io/z",
    ]


def test_get_history_window_orders_by_ts_with_forward_pagination():
    """With `oldest` set, Slack paginates forward (page 2 is NEWER than page 1)
    while each page is internally newest-first. Output must still be oldest-first
    by ts, not page-concatenation order."""
    client = _make_client()
    client._client.conversations_history.side_effect = [
        {
            "ok": True,
            "messages": [
                {"user": "U1", "text": "second", "ts": "2.0"},
                {"user": "U1", "text": "first", "ts": "1.0"},
            ],
            "response_metadata": {"next_cursor": "cur1"},
        },
        {
            "ok": True,
            "messages": [
                {"user": "U1", "text": "fourth", "ts": "4.0"},
                {"user": "U1", "text": "third", "ts": "3.0"},
            ],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    window = client.get_history_window("C1", limit=10, oldest="0.5", max_pages=5)

    assert [m["text"] for m in window["messages"]] == ["first", "second", "third", "fourth"]
    assert window["coverage"]["oldest_ts"] == "1.0"
    assert window["coverage"]["newest_ts"] == "4.0"


def test_get_history_window_stops_at_max_pages():
    """Exhausting max_pages with a live cursor must set truncated_reason='max_pages'."""
    client = _make_client()
    client._client.conversations_history.side_effect = [
        {
            "ok": True,
            "messages": [{"user": "U1", "text": f"msg{i}", "ts": f"{i}.0"}],
            "response_metadata": {"next_cursor": f"cur{i}"},
        }
        for i in range(3)
    ]
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    window = client.get_history_window("C1", limit=100, max_pages=3)

    assert window["coverage"]["pages_fetched"] == 3
    assert window["coverage"]["has_more"] is True
    assert window["coverage"]["truncated_reason"] == "max_pages"
    assert client._client.conversations_history.call_count == 3


def test_get_history_window_returns_partial_results_on_mid_pagination_error():
    """An API error after a successful page keeps fetched messages and records the error."""
    client = _make_client()
    client._client.conversations_history.side_effect = [
        {
            "ok": True,
            "messages": [{"user": "U1", "text": "kept", "ts": "1.0"}],
            "response_metadata": {"next_cursor": "cur1"},
        },
        {"ok": False, "error": "ratelimited"},
    ]
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    window = client.get_history_window("C1", limit=100, max_pages=5)

    assert window["coverage"]["count"] == 1
    assert window["messages"][0]["text"] == "kept"
    assert window["coverage"]["truncated_reason"] == "ratelimited"
    # Current contract: errors set truncated_reason but not has_more; callers
    # must check both fields to detect an incomplete window.
    assert window["coverage"]["has_more"] is False


def test_get_history_window_keeps_partial_results_on_mid_pagination_exception():
    """An exception after a successful page keeps fetched messages and records it."""
    client = _make_client()
    client._client.conversations_history.side_effect = [
        {
            "ok": True,
            "messages": [{"user": "U1", "text": "kept", "ts": "1.0"}],
            "response_metadata": {"next_cursor": "cur1"},
        },
        Exception("network down"),
    ]
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    window = client.get_history_window("C1", limit=100, max_pages=5)

    assert window["coverage"]["count"] == 1
    assert window["messages"][0]["text"] == "kept"
    assert window["coverage"]["truncated_reason"] == "network down"


def test_get_history_window_returns_empty_structure_on_first_page_error():
    client = _make_client()
    client._client.conversations_history.return_value = {"ok": False, "error": "channel_not_found"}

    window = client.get_history_window("C1", limit=100)

    assert window["messages"] == []
    assert window["user_map"] == {}
    assert window["coverage"]["count"] == 0
    assert window["coverage"]["oldest_ts"] is None
    assert window["coverage"]["truncated_reason"] == "channel_not_found"


def test_get_history_window_returns_empty_structure_on_exception():
    client = _make_client()
    client._client.conversations_history.side_effect = Exception("network down")

    window = client.get_history_window("C1", limit=100)

    assert window["messages"] == []
    assert window["coverage"]["count"] == 0
    assert window["coverage"]["truncated_reason"] == "network down"


def test_get_history_window_passes_time_bounds_to_api():
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [],
        "response_metadata": {"next_cursor": ""},
    }

    client.get_history_window("C1", limit=10, oldest="100.0", latest="200.0")

    kwargs = client._client.conversations_history.call_args[1]
    assert kwargs["oldest"] == "100.0"
    assert kwargs["latest"] == "200.0"


def test_get_history_window_clamps_limit():
    """limit is clamped to [1, 1000]; per-page API limit never exceeds 1000."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [],
        "response_metadata": {"next_cursor": ""},
    }

    client.get_history_window("C1", limit=5000)
    assert client._client.conversations_history.call_args[1]["limit"] == 1000

    client.get_history_window("C1", limit=0)
    assert client._client.conversations_history.call_args[1]["limit"] == 1


def test_get_history_window_includes_bot_messages_without_user():
    """Window output must keep bot/integration posts that lack a user field."""
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {"user": "U1", "text": "thanks", "ts": "2.0"},
            {"bot_id": "B1", "username": "ClickUp", "text": "New Feedback", "ts": "1.0"},
        ],
        "response_metadata": {"next_cursor": ""},
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    window = client.get_history_window("C1", limit=10)

    assert window["coverage"]["count"] == 2
    assert window["messages"][0]["user"] == "ClickUp"
    assert window["messages"][1]["user"] == "alice"


def test_get_history_window_coverage_timestamps():
    client = _make_client()
    client._client.conversations_history.return_value = {
        "ok": True,
        "messages": [
            {"user": "U1", "text": "newest", "ts": "3.0"},
            {"user": "U1", "text": "oldest", "ts": "1.0"},
        ],
        "response_metadata": {"next_cursor": ""},
    }
    client._client.users_info.return_value = {
        "user": {"profile": {"display_name": "alice"}, "name": "alice"}
    }

    window = client.get_history_window("C1", limit=10)

    assert window["coverage"]["oldest_ts"] == "1.0"
    assert window["coverage"]["newest_ts"] == "3.0"


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


# ---------------------------------------------------------------------------
# resolve_mention_name() — outbound @name -> user id reverse resolution (U1)
# ---------------------------------------------------------------------------

def _members(*entries):
    """Build a users.list payload from (id, display, real, name) tuples."""
    members = []
    for uid, display, real, name in entries:
        members.append({
            "id": uid,
            "name": name,
            "deleted": False,
            "profile": {"display_name": display, "real_name": real},
        })
    return {"ok": True, "members": members, "response_metadata": {"next_cursor": ""}}


def test_resolve_mention_name_exact_unique():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U012", "alice", "Alice Example", "alice"),
    )
    assert client.resolve_mention_name("alice") == "U012"


def test_resolve_mention_name_full_multiword_unique():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U012", "Alice Example", "Alice Example", "aexample"),
    )
    assert client.resolve_mention_name("alice example") == "U012"


def test_resolve_mention_name_unique_first_name():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U012", "Alice Example", "Alice Example", "aexample"),
    )
    # single-token @alice resolves via unique first name
    assert client.resolve_mention_name("alice") == "U012"


def test_resolve_mention_name_collision_returns_none():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U1", "alex", "Alex", "alex"),
        ("U2", "alex", "Alex", "alex2"),
    )
    assert client.resolve_mention_name("alex") is None


def test_resolve_mention_name_first_name_collision_returns_none():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U1", "Alex Carter", "Alex Carter", "ksharma"),
        ("U2", "Alex Rivera", "Alex Rivera", "kranjan"),
    )
    assert client.resolve_mention_name("alex") is None


def test_resolve_mention_name_unknown_returns_none():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U012", "alice", "Alice Example", "alice"),
    )
    assert client.resolve_mention_name("stranger") is None


def test_resolve_mention_name_roster_fallback():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U012", "alice", "Alice Example", "alice"),
    )
    client.set_roster_fallback({"fallback": "U999"})
    # name absent from users.list resolves via injected roster
    assert client.resolve_mention_name("fallback") == "U999"
    # users.list names still resolve
    assert client.resolve_mention_name("alice") == "U012"


def test_resolve_mention_name_users_list_error_returns_none():
    client = _make_client()
    client._client.users_list.side_effect = Exception("rate limited")
    assert client.resolve_mention_name("alice") is None


def test_resolve_mention_name_caches_users_list():
    client = _make_client()
    client._client.users_list.return_value = _members(
        ("U012", "alice", "Alice Example", "alice"),
        ("U096", "bob", "Bob", "bob"),
    )
    assert client.resolve_mention_name("alice") == "U012"
    assert client.resolve_mention_name("bob") == "U096"
    # map built once, reused within TTL
    assert client._client.users_list.call_count == 1
