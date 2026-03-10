"""Response formatters — translate LLM markdown to platform-native markup."""

import html as html_module
import json
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


_MD_FORMATTING = re.compile(r"\*\*|(?<!\*)\*(?!\*)|`|\[")
_LONG_CELL_THRESHOLD = 40


def _convert_md_tables(text: str) -> str:
    """Convert markdown tables to either aligned code blocks (short/plain cells)
    or mrkdwn bullet lists (long or markdown-formatted cells)."""
    table_pattern = re.compile(r"((?:^\|.+\|\s*\n)+)", re.MULTILINE)

    def render_table(match: re.Match) -> str:
        raw = match.group(1).strip()
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in raw.splitlines()
            if not re.match(r"^\|[-:| ]+\|$", line.strip())  # skip separator row
        ]
        if not rows:
            return match.group(0)

        max_cols = max(len(r) for r in rows)
        rows = [r + [""] * (max_cols - len(r)) for r in rows]

        # Detect whether any data cell is long or contains markdown formatting
        data_rows = rows[1:] if len(rows) > 1 else rows
        rich = any(
            len(cell) > _LONG_CELL_THRESHOLD or bool(_MD_FORMATTING.search(cell))
            for row in data_rows
            for cell in row
        )

        if rich:
            return _render_table_as_mrkdwn(rows)
        else:
            return _render_table_as_code_block(rows)

    return table_pattern.sub(render_table, text)


def _render_table_as_code_block(rows: list[list[str]]) -> str:
    """Render a table as an aligned monospace code block (good for numeric/short data)."""
    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(max_cols)]

    lines = []
    for idx, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
        if idx == 0:
            lines.append("  ".join("─" * widths[i] for i in range(max_cols)))

    return "```\n" + "\n".join(lines) + "\n```\n"


def _render_table_as_mrkdwn(rows: list[list[str]]) -> str:
    """Render a table as a mrkdwn list (good for long/formatted content).

    For 2-column tables: *col1* — col2
    For N-column tables: *col1* | col2 | col3
    """
    if not rows:
        return ""

    header, data_rows = rows[0], rows[1:]
    two_col = len(header) == 2

    lines = []

    # Header as bold labels
    if two_col:
        lines.append(f"*{header[0]}* — *{header[1]}*")
    else:
        lines.append("  |  ".join(f"*{h}*" for h in header))
    lines.append("")  # blank line after header

    # Data rows — pre-apply mrkdwn to cells so bold/links render correctly
    # and _apply_mrkdwn in flush() won't double-convert them
    for row in data_rows:
        if not any(cell.strip() for cell in row):
            continue
        converted = [_apply_mrkdwn(cell) for cell in row]
        if two_col:
            col1, col2 = converted[0], converted[1] if len(converted) > 1 else ""
            # Don't wrap col1 in *...* — it may already contain *bold* markers
            lines.append(f"• {col1} — {col2}" if col2 else f"• {col1}")
        else:
            lines.append("• " + "  |  ".join(converted))

    return "\n".join(lines) + "\n\n"


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
        # Convert markdown tables to aligned code blocks before stashing
        result = self._convert_tables(text)

        result, stash = _stash_code_blocks(result)

        # H1 → bold with divider line beneath for visual weight
        result = re.sub(r"^#\s+(.+)$", r"*\1*\n────────────────────────", result, flags=re.MULTILINE)

        # H2/H3 → bold
        result = re.sub(r"^#{2,6}\s+(.+)$", r"*\1*", result, flags=re.MULTILINE)

        # Horizontal rule → unicode divider
        result = re.sub(r"^---+$", "────────────────────────", result, flags=re.MULTILINE)

        # Bold: **text** → *text*
        result = re.sub(r"\*\*(.+?)\*\*", r"*\1*", result)

        # Links: [text](url) → <url|text>
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", result)

        # Bullets: - item → • item
        result = re.sub(r"^(\s*)[-*+]\s+", "\\1• ", result, flags=re.MULTILINE)

        # Strikethrough: ~~text~~ → ~text~
        result = re.sub(r"~~(.+?)~~", r"~\1~", result)

        # Collapse 3+ blank lines to 2
        result = re.sub(r"\n{3,}", "\n\n", result)

        return _restore_stash(result, stash)

    def _convert_tables(self, text: str) -> str:
        return _convert_md_tables(text)


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


def _apply_mrkdwn(text: str) -> str:
    """Apply markdown → Slack mrkdwn conversions for use inside Block Kit sections."""
    # Numbered list items: strip ** wrapping FIRST — Slack won't bold "N." patterns
    # e.g. **1. prometheus-pod** → 1. prometheus-pod (before the main bold pass)
    text = re.sub(r"\*\*(\d+\..+?)\*\*", r"\1", text)
    # Bold: **text** → *text*  (loop handles adjacent spans like **a** — **b**)
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # Strip any unmatched ** left over from malformed LLM output
    text = re.sub(r"\*\*", "", text)
    # Links: [text](url) → <url|text>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", text)
    # Bullets: - item → • item
    text = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", text, flags=re.MULTILINE)
    # Strikethrough: ~~text~~ → ~text~
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


class SlackBlockFormatter(Formatter):
    """
    Convert markdown to Slack Block Kit JSON.

    format() returns JSON-encoded block arrays (one string per message).
    The SlackAdapter detects the JSON and calls say(blocks=...) instead of say(text=...).

    Block mapping:
      # H1         → header block  (plain_text, max 150 chars)
      ## H2–H6     → section block (*bold* mrkdwn)
      ---          → divider block
      ```...```    → section block with fenced mrkdwn
      table        → converted to aligned code block first
      body text    → section block (mrkdwn, max 3000 chars)
    """

    MAX_SECTION_LENGTH = 3000  # Slack section text element limit
    MAX_HEADER_LENGTH = 150    # Slack header block plain_text limit
    MAX_BLOCKS_PER_MESSAGE = 50

    def format(self, text: str) -> list[str]:
        all_blocks = self._markdown_to_blocks(text)
        if not all_blocks:
            return [json.dumps([])]
        messages = []
        for i in range(0, len(all_blocks), self.MAX_BLOCKS_PER_MESSAGE):
            messages.append(json.dumps(all_blocks[i : i + self.MAX_BLOCKS_PER_MESSAGE]))
        return messages

    def _markdown_to_blocks(self, text: str) -> list[dict]:
        # Tables → aligned code blocks before line-by-line parsing
        text = _convert_md_tables(text)

        blocks: list[dict] = []
        pending: list[str] = []

        def flush():
            if not pending:
                return
            section_text = "\n".join(pending).strip()
            pending.clear()
            if not section_text:
                return
            section_text = _apply_mrkdwn(section_text)
            # Split sections that exceed the character limit
            while len(section_text) > self.MAX_SECTION_LENGTH:
                split_at = section_text.rfind("\n", 0, self.MAX_SECTION_LENGTH)
                if split_at == -1:
                    split_at = self.MAX_SECTION_LENGTH
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section_text[:split_at].rstrip()}})
                section_text = section_text[split_at:].lstrip()
            if section_text:
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section_text}})

        lines = text.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            # H1 → header block
            m = re.match(r"^# (.+)$", line)
            if m:
                flush()
                blocks.append({
                    "type": "header",
                    "text": {"type": "plain_text", "text": m.group(1).strip()[: self.MAX_HEADER_LENGTH], "emoji": True},
                })
                i += 1
                continue

            # H2–H6 → bold section
            m = re.match(r"^#{2,6} (.+)$", line)
            if m:
                flush()
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{m.group(1).strip()}*"}})
                i += 1
                continue

            # Horizontal rule → divider
            if re.match(r"^---+$", line):
                flush()
                blocks.append({"type": "divider"})
                i += 1
                continue

            # Fenced code block
            if line.startswith("```"):
                flush()
                code_lines: list[str] = []
                i += 1
                while i < len(lines) and not lines[i].startswith("```"):
                    code_lines.append(lines[i])
                    i += 1
                code = "\n".join(code_lines)
                code_text = f"```{code}```"
                if len(code_text) > self.MAX_SECTION_LENGTH:
                    code_text = code_text[: self.MAX_SECTION_LENGTH - 4] + "\n```"
                blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": code_text}})
                i += 1  # skip closing ```
                continue

            pending.append(line)
            i += 1

        flush()
        return blocks


def _blocks_fallback(blocks: list[dict]) -> str:
    """Extract a short plain-text summary from Block Kit blocks for notification fallback."""
    parts = []
    for block in blocks:
        btype = block.get("type")
        if btype in ("header", "section"):
            parts.append(block.get("text", {}).get("text", ""))
        if len(" ".join(parts)) >= 300:
            break
    return " ".join(parts)[:300] or "New message from Mithai"
