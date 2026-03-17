"""Abstract messaging adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional
from uuid import uuid4

if TYPE_CHECKING:
    from mithai.human.mcp import HumanRequest

# Callback type: (message, adapter) -> response text
MessageHandler = Callable[["IncomingMessage", "Adapter"], str]

# Callback type: (channel_id, channel_name) -> response text to post (or None)
ChannelJoinHandler = Callable[[str, str], Optional[str]]

# Callback type: (message,) -> None  — called for observed messages (no reply)
ChannelObserveHandler = Callable[["IncomingMessage"], None]

# Callback type: (channel_id, user_id, text, message_id) -> None — log bot reply to channel_context
BotReplyHandler = Callable[[str, str, str, str], None]


@dataclass
class ImageAttachment:
    """A base64-encoded image attached to a message."""
    data: str          # base64-encoded bytes
    media_type: str    # e.g. "image/png", "image/jpeg"


@dataclass
class IncomingMessage:
    """Platform-agnostic incoming message."""

    text: str
    channel_id: str
    user_id: str
    platform: str = ""
    message_id: str = field(default_factory=lambda: uuid4().hex[:12])
    thread_id: str | None = None  # Slack thread_ts, etc.
    images: list[ImageAttachment] = field(default_factory=list)
    non_image_files: list[str] = field(default_factory=list)  # filenames of skipped non-image attachments


@dataclass
class OutgoingMessage:
    """Platform-agnostic response to send."""

    text: str
    channel_id: str


class Adapter(ABC):
    """
    Abstract messaging adapter.

    Each platform (Slack, Telegram, CLI) implements this.
    Adapters handle receiving messages, sending responses,
    and presenting Human MCP requests.
    """

    @abstractmethod
    def start(self, on_message: MessageHandler, on_channel_join: "ChannelJoinHandler | None" = None,
              on_observe: "ChannelObserveHandler | None" = None) -> None:
        """
        Start listening for messages.

        on_message(message, adapter) is called for each incoming message.
        The adapter passes itself so the engine can route Human MCP
        approvals back through the correct platform.
        """
        ...

    @abstractmethod
    def stop(self) -> None:
        """Clean shutdown."""
        ...

    @abstractmethod
    def send(self, message: OutgoingMessage) -> None:
        """Send a message to the platform."""
        ...

    @abstractmethod
    def request_human_approval(self, request: HumanRequest, channel_id: str) -> bool:
        """
        Present a Human MCP request and wait for response.

        For "approve": show action details, wait for yes/no.
        For "confirm": show action details, wait for confirmation text.

        Returns True if approved, False if denied or timed out.
        """
        ...

    # ── Optional status callbacks (no-op by default) ──
    # Engine calls these to let the adapter show progress feedback.

    def on_thinking_start(self) -> None:
        """Called before an LLM call starts."""

    def on_thinking_end(self, elapsed_s: float) -> None:
        """Called when an LLM call completes."""

    def on_tool_start(self, tool_name: str, tool_input: dict) -> None:
        """Called before a tool is executed."""

    def on_tool_end(self, tool_name: str, elapsed_s: float, approved: bool) -> None:
        """Called after a tool finishes executing."""

    def on_synthesizing(self) -> None:
        """Called before the follow-up LLM call after tool results."""

    def fetch_thread_context(self, channel_id: str, thread_ts: str) -> list[str] | None:
        """Fetch historical messages from a thread for backfill context.

        Called when the agent is @mentioned in a thread for the first time and
        needs to see what was discussed above. Returns formatted 'name: text'
        lines oldest-first, or None if not supported by this adapter.
        """
        return None
