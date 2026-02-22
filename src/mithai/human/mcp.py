"""
Human MCP — Human-in-the-loop as a protocol.

Instead of a rigid approval gate, the human is treated as another
tool/protocol that skills can invoke. Skills declare which tools
need human consultation via the "human" field on tool definitions.

Levels:
  - None: auto-execute (default)
  - "approve": show what will happen, human approves or denies
  - "confirm": show what will happen, human types confirmation text
  - "dynamic": resolved at runtime by the skill's resolve_human() function.
    The engine calls resolve_human(tool_name, tool_input, ctx) which returns
    the actual level (None, "approve", or "confirm") based on the input.
    This allows skills to auto-execute safe operations while requiring
    approval for dangerous ones (e.g. allowlisted vs unknown shell commands).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from mithai.adapters.base import Adapter
    from mithai.core.skill_loader import ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class HumanRequest:
    """A request to consult the human."""

    tool_name: str  # Prefixed name (skill__tool)
    tool_input: dict
    level: str  # "approve" or "confirm"
    description: str
    request_id: str = field(default_factory=lambda: uuid4().hex[:8])


class HumanMCP:
    """
    Routes human-in-the-loop requests through the chat adapter.

    Checks tool definitions and config overrides to determine if
    a tool call needs human consultation before execution.
    """

    def __init__(self, config: dict | None = None):
        self._timeout = (config or {}).get("timeout_seconds", 300)
        self._overrides: dict[str, str | None] = (config or {}).get("overrides", {})

    def resolve_level(self, prefixed_name: str, tool_def: ToolDefinition) -> str | None:
        """
        Determine the human level for a tool call.

        Config overrides take precedence over tool definitions.
        A config override of null removes the human requirement.
        """
        if prefixed_name in self._overrides:
            override = self._overrides[prefixed_name]
            if override is None:
                return None  # Explicitly de-escalated
            return override
        return tool_def.human

    def request_approval(
        self,
        prefixed_name: str,
        tool_input: dict,
        tool_def: ToolDefinition,
        channel_id: str,
        adapter: Adapter,
    ) -> bool:
        """
        Check if human consultation is needed and request it.

        Routes the approval request through the adapter that received
        the original message.

        Returns True if the tool should be executed.
        """
        level = self.resolve_level(prefixed_name, tool_def)

        if level is None:
            return True  # Auto-execute

        description = self._describe_action(prefixed_name, tool_input, tool_def)
        request = HumanRequest(
            tool_name=prefixed_name,
            tool_input=tool_input,
            level=level,
            description=description,
        )

        logger.info("Human MCP: requesting %s for %s", level, prefixed_name)
        return adapter.request_human_approval(request, channel_id)

    def _describe_action(
        self, prefixed_name: str, tool_input: dict, tool_def: ToolDefinition
    ) -> str:
        """Generate human-readable description of the action."""
        parts = [
            f"Tool: {prefixed_name}",
            f"Action: {tool_def.description}",
        ]
        if tool_input:
            parts.append(f"Input: {json.dumps(tool_input, indent=2)}")
        return "\n".join(parts)
