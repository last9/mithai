"""Tests for per-adapter response formatters."""

from mithai.adapters.formatters import (
    CLIFormatter,
    SlackFormatter,
    TelegramFormatter,
    encode_mentions,
)


_RESOLVER_MAP = {"alice": "U012", "bob": "U096", "carol": "UQ0"}


def _resolver(token):
    return _RESOLVER_MAP.get(token.lower())


class TestEncodeMentions:
    def test_cc_line_encodes_all(self):
        assert encode_mentions("cc: @alice @bob @carol", _resolver) == (
            "cc: <@U012> <@U096> <@UQ0>"
        )

    def test_unknown_name_left_plain(self):
        assert encode_mentions("ping @stranger", _resolver) == "ping @stranger"

    def test_inside_inline_code_untouched(self):
        assert encode_mentions("use `@alice` here", _resolver) == "use `@alice` here"

    def test_inside_fence_untouched(self):
        text = "```\ncc @alice\n```"
        assert encode_mentions(text, _resolver) == text

    def test_email_untouched(self):
        assert encode_mentions("mail foo@alice.example", _resolver) == "mail foo@alice.example"

    def test_already_encoded_untouched(self):
        assert encode_mentions("hi <@U012> there", _resolver) == "hi <@U012> there"

    def test_broadcast_tokens_untouched(self):
        text = "@here @channel @everyone please look"
        assert encode_mentions(text, _resolver) == text

    def test_collision_resolver_none_left_plain(self):
        # resolver returns None (e.g. name collision) -> token left verbatim
        assert encode_mentions("@alex review", lambda t: None) == "@alex review"

    def test_none_resolver_passthrough(self):
        assert encode_mentions("cc: @alice", None) == "cc: @alice"

    def test_no_at_sign_passthrough(self):
        assert encode_mentions("plain text only", _resolver) == "plain text only"


class TestSlackBlockFormatterMentions:
    def test_format_encodes_with_resolver(self):
        from mithai.adapters.formatters import SlackBlockFormatter
        fmt = SlackBlockFormatter(mention_resolver=_resolver)
        out = "".join(fmt.format("cc: @alice @bob"))
        assert "<@U012>" in out
        assert "<@U096>" in out

    def test_format_default_no_resolver_leaves_plain(self):
        # Default (unconfigured) formatter must not touch @name — regression guard.
        from mithai.adapters.formatters import SlackBlockFormatter
        fmt = SlackBlockFormatter()
        out = "".join(fmt.format("cc: @alice"))
        assert "@alice" in out
        assert "<@U012>" not in out


class TestSlackFormatter:
    def setup_method(self):
        self.fmt = SlackFormatter()

    def test_bold(self):
        assert self.fmt.format("**bold text**") == ["*bold text*"]

    def test_heading_to_bold(self):
        result = self.fmt.format("# My Heading")
        assert result[0].startswith("*My Heading*")

    def test_h2_heading(self):
        assert self.fmt.format("## Sub Heading") == ["*Sub Heading*"]

    def test_link(self):
        assert self.fmt.format("[click](https://example.com)") == [
            "<https://example.com|click>"
        ]

    def test_bullets(self):
        result = self.fmt.format("- one\n- two")
        assert result == ["\u2022 one\n\u2022 two"]

    def test_strikethrough(self):
        assert self.fmt.format("~~deleted~~") == ["~deleted~"]

    def test_code_block_preserved(self):
        text = "```\nx = **not bold**\n```"
        result = self.fmt.format(text)
        assert "**not bold**" in result[0]

    def test_inline_code_preserved(self):
        result = self.fmt.format("use `**bold**` syntax")
        assert "`**bold**`" in result[0]

    def test_split_long_message(self):
        text = ("a" * 100 + "\n\n") * 50  # ~5100 chars with paragraph breaks
        result = self.fmt.format(text)
        assert len(result) > 1
        assert all(len(chunk) <= 3900 for chunk in result)

    def test_passthrough_plain(self):
        assert self.fmt.format("hello world") == ["hello world"]

    def test_mixed(self):
        text = "# Title\n\n**bold** and [link](https://x.com)\n\n- item"
        result = self.fmt.format(text)
        assert "*Title*" in result[0]
        assert "*bold*" in result[0]
        assert "<https://x.com|link>" in result[0]
        assert "\u2022 item" in result[0]


class TestCLIFormatter:
    def setup_method(self):
        self.fmt = CLIFormatter()

    def test_passthrough(self):
        assert self.fmt.format("hello world") == ["hello world"]

    def test_long_response_warning(self):
        text = "x" * 6000
        result = self.fmt.format(text)
        assert len(result) == 1
        assert "[Response truncated" in result[0]

    def test_short_no_warning(self):
        result = self.fmt.format("short")
        assert "[Response truncated" not in result[0]


class TestTelegramFormatter:
    def setup_method(self):
        self.fmt = TelegramFormatter()

    def test_bold(self):
        result = self.fmt.format("**bold**")
        assert "<b>bold</b>" in result[0]

    def test_italic(self):
        result = self.fmt.format("*italic*")
        assert "<i>italic</i>" in result[0]

    def test_code_block(self):
        result = self.fmt.format("```\ncode\n```")
        assert "<pre><code>" in result[0]

    def test_inline_code(self):
        result = self.fmt.format("use `cmd`")
        assert "<code>cmd</code>" in result[0]

    def test_link(self):
        result = self.fmt.format("[text](https://example.com)")
        assert '<a href="https://example.com">text</a>' in result[0]

    def test_html_escaping(self):
        result = self.fmt.format("use <angle> & stuff")
        assert "&lt;angle&gt;" in result[0]
        assert "&amp;" in result[0]

    def test_code_block_html_escaped(self):
        result = self.fmt.format("```\nx < 5 && y > 3\n```")
        assert "&lt;" in result[0]
        assert "&amp;&amp;" in result[0]

    def test_split_long_message(self):
        text = ("word " * 200 + "\n\n") * 10  # ~10000 chars
        result = self.fmt.format(text)
        assert len(result) > 1
        assert all(len(chunk) <= 4096 for chunk in result)

    def test_heading(self):
        result = self.fmt.format("# Title")
        assert "<b>Title</b>" in result[0]
