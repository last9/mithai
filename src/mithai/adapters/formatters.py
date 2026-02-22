"""Response formatters — translate LLM markdown to platform-native markup."""

import html as html_module
import logging
import re
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class Formatter(ABC):
    """
    Base response formatter.

    Translates LLM output (markdown-like text) to the native markup
    of each platform, and applies platform-specific constraints
    (length limits, unsupported syntax stripping, etc.).
    """

    @abstractmethod
    def format(self, text: str) -> list[str]:
        """
        Format engine output for the target platform.

        Returns a list of strings — usually one, but multiple if the
        response must be split for length limits.
        """
        ...

    def _split_by_limit(self, text: str, limit: int) -> list[str]:
        """Split text into chunks respecting the character limit."""
        if len(text) <= limit:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            # Try paragraph boundary, then line, then space, then hard cut
            split_pos = remaining.rfind("\n\n", 0, limit)
            if split_pos == -1:
                split_pos = remaining.rfind("\n", 0, limit)
            if split_pos == -1:
                split_pos = remaining.rfind(" ", 0, limit)
            if split_pos == -1:
                split_pos = limit

            chunks.append(remaining[:split_pos].rstrip())
            remaining = remaining[split_pos:].lstrip()

        return chunks


def _stash_code_blocks(text: str) -> tuple[str, dict[str, str]]:
    """Replace code blocks and inline code with placeholders."""
    stash: dict[str, str] = {}
    counter = 0

    def replace(match):
        nonlocal counter
        key = f"\x00CODE{counter}\x00"
        stash[key] = match.group(0)
        counter += 1
        return key

    # Fenced code blocks first, then inline code
    result = re.sub(r"```[\s\S]*?```", replace, text)
    result = re.sub(r"`[^`]+`", replace, result)
    return result, stash


def _restore_stash(text: str, stash: dict[str, str]) -> str:
    """Restore stashed code blocks."""
    for key, original in stash.items():
        text = text.replace(key, original)
    return text


class SlackFormatter(Formatter):
    """Translate markdown to Slack mrkdwn."""

    MAX_LENGTH = 3900

    def format(self, text: str) -> list[str]:
        converted = self._markdown_to_mrkdwn(text)
        return self._split_by_limit(converted, self.MAX_LENGTH)

    def _markdown_to_mrkdwn(self, text: str) -> str:
        result, stash = _stash_code_blocks(text)

        # Headings → bold (Slack has no heading syntax)
        result = re.sub(r"^#{1,6}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)

        # Bold: **text** → *text*
        result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)

        # Links: [text](url) → <url|text>
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", result)

        # Bullets: - item → • item
        result = re.sub(r"^(\s*)[-*+]\s+", "\\1\u2022 ", result, flags=re.MULTILINE)

        # Strikethrough: ~~text~~ → ~text~
        result = re.sub(r"~~(.+?)~~", r"~\1~", result)

        return _restore_stash(result, stash)


class CLIFormatter(Formatter):
    """Minimal formatter for terminal output."""

    WARN_LENGTH = 5000

    def format(self, text: str) -> list[str]:
        if len(text) > self.WARN_LENGTH:
            text = text + f"\n\n[Response truncated — full length: {len(text)} chars]"
        return [text]


class TelegramFormatter(Formatter):
    """Translate markdown to Telegram-compatible HTML."""

    MAX_LENGTH = 4096

    def format(self, text: str) -> list[str]:
        converted = self._markdown_to_html(text)
        return self._split_by_limit(converted, self.MAX_LENGTH)

    def _markdown_to_html(self, text: str) -> str:
        # Stash code blocks with HTML-escaped content
        stash: dict[str, str] = {}
        counter = 0

        def stash_fenced(match):
            nonlocal counter
            key = f"\x00CODE{counter}\x00"
            code = html_module.escape(match.group(2))
            stash[key] = f"<pre><code>{code}</code></pre>"
            counter += 1
            return key

        def stash_inline(match):
            nonlocal counter
            key = f"\x00CODE{counter}\x00"
            code = html_module.escape(match.group(1))
            stash[key] = f"<code>{code}</code>"
            counter += 1
            return key

        result = re.sub(r"```(\w*)\n?([\s\S]*?)```", stash_fenced, text)
        result = re.sub(r"`([^`]+)`", stash_inline, result)

        # HTML-escape the remaining plain text
        result = html_module.escape(result)

        # Bold: **text** → <b>text</b>  (must come before italic)
        result = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", result)

        # Italic: *text* or _text_
        result = re.sub(r"\*(.+?)\*", r"<i>\1</i>", result)
        result = re.sub(r"_(.+?)_", r"<i>\1</i>", result)

        # Strikethrough: ~~text~~ → <s>text</s>
        result = re.sub(r"~~(.+?)~~", r"<s>\1</s>", result)

        # Links: [text](url) → <a href="url">text</a>
        result = re.sub(
            r"\[([^\]]+)\]\(([^)]+)\)",
            r'<a href="\2">\1</a>',
            result,
        )

        # Headings → bold
        result = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", result, flags=re.MULTILINE)

        # Bullets
        result = re.sub(r"^(\s*)[-*+]\s+", "\\1\u2022 ", result, flags=re.MULTILINE)

        # Restore stashed code blocks
        for key, original in stash.items():
            result = result.replace(key, original)

        return result
