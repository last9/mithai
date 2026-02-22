"""Abstract messaging adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

if TYPE_CHECKING:
    from mithai.human.mcp import HumanRequest

# Callback type: (message, adapter) -> response text
MessageHandler = Callable[["IncomingMessage", "Adapter"], str]


@dataclass
class IncomingMessage:
    """Platform-agnostic incoming message."""

    text: str
    channel_id: str
    user_id: str
    platform: str = ""
    message_id: str = field(default_factory=lambda: uuid4().hex[:12])
    thread_id: str | None = None  # Slack thread_ts, etc.


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
    def start(self, on_message: MessageHandler) -> None:
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
