"""Prefix skill tools and route tool calls back to handlers."""

import json
import logging
from mithai.core.skill_loader import Skill, ToolDefinition

logger = logging.getLogger(__name__)

SEPARATOR = "__"


class ToolRouter:
    """
    Manages tool namespacing and routing.

    Tools are prefixed as skill_name__tool_name when sent to the LLM.
    Incoming tool calls are parsed and dispatched to the correct skill handler.
    """

    def __init__(self, skills: dict[str, Skill]):
        self._skills = skills
        self._tool_index: dict[str, tuple[str, ToolDefinition]] = {}
        self._build_index()

    def _build_index(self):
        """Build a lookup from prefixed tool name to (skill_name, tool_def)."""
        for skill_name, skill in self._skills.items():
            for tool in skill.tools:
                prefixed = f"{skill_name}{SEPARATOR}{tool.name}"
                self._tool_index[prefixed] = (skill_name, tool)

    def collect_tools_for_llm(self) -> list[dict]:
        """
        Collect all tools from all skills, prefixed for the LLM.

        Returns Anthropic tool format:
        [{"name": "skill__tool", "description": "...", "input_schema": {...}}]
        """
        tools = []
        for prefixed, (skill_name, tool_def) in self._tool_index.items():
            tools.append({
                "name": prefixed,
                "description": f"[{skill_name}] {tool_def.description}",
                "input_schema": tool_def.input_schema,
            })
        return tools

    def parse(self, prefixed_name: str) -> tuple[str, str]:
        """Parse a prefixed tool name into (skill_name, tool_name)."""
        parts = prefixed_name.split(SEPARATOR, 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid tool name format: {prefixed_name}")
        return parts[0], parts[1]

    def get_definition(self, prefixed_name: str) -> ToolDefinition | None:
        """Get the ToolDefinition for a prefixed tool name."""
        entry = self._tool_index.get(prefixed_name)
        if entry is None:
            return None
        return entry[1]

    def route(self, prefixed_name: str, tool_input: dict, ctx: dict) -> str:
        """
        Route a tool call to the correct skill handler.

        Returns the handler's result as a string.
        """
        entry = self._tool_index.get(prefixed_name)
        if entry is None:
            logger.warning("Unknown tool: %s", prefixed_name)
            return json.dumps({"error": f"Unknown tool: {prefixed_name}"})

        skill_name, tool_def = entry
        skill = self._skills[skill_name]

        try:
            result = skill.handle(tool_def.name, tool_input, ctx)
            if not isinstance(result, str):
                result = json.dumps(result)
            return result
        except Exception as e:
            logger.exception("Tool %s failed", prefixed_name)
            return json.dumps({"error": str(e)})
