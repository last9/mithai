"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""

    content: list[dict[str, Any]]  # [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]
    stop_reason: str  # "end_turn", "tool_use"
    model: str
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(ABC):
    """
    Abstract LLM provider.

    Ships with Anthropic (Claude) as default.
    The tool format follows Anthropic's convention — other providers
    translate internally.
    """

    @abstractmethod
    def create_message(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        call_type: str = "initial",
        after_tools: list[str] | None = None,
    ) -> LLMResponse:
        """
        Send a message to the LLM with optional tools.

        Tools format (Anthropic convention):
        [{"name": "...", "description": "...", "input_schema": {...}}]
        """
        ...

    @staticmethod
    def format_tool_result(tool_use_id: str, content: str) -> dict:
        """Format a tool result for the next message turn."""
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
