"""Abstract messaging adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

if TYPE_CHECKING:
    from mithai.human.mcp import HumanRequest


@dataclass
class IncomingMessage:
    """Platform-agnostic incoming message."""

    text: str
    channel_id: str
    user_id: str
    platform: str = ""
    message_id: str = field(default_factory=lambda: uuid4().hex[:12])


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
    def start(self, on_message: Callable[[IncomingMessage], str]) -> None:
        """
        Start listening for messages.

        on_message is called for each incoming message and should return
        the response text. The adapter handles sending the response.
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
