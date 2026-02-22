"""Tests for per-adapter response formatters."""

from mithai.adapters.formatters import CLIFormatter, SlackFormatter, TelegramFormatter


class TestSlackFormatter:
    def setup_method(self):
        self.fmt = SlackFormatter()

    def test_bold(self):
        assert self.fmt.format("**bold text**") == ["*bold text*"]

    def test_heading_to_bold(self):
        assert self.fmt.format("# My Heading") == ["*My Heading*"]

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
