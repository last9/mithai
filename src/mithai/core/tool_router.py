"""Prefix skill tools and route tool calls back to handlers."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from mithai.core.skill_loader import Skill, ToolDefinition

if TYPE_CHECKING:
    from mithai.core.mcp_manager import MCPManager

logger = logging.getLogger(__name__)

SEPARATOR = "__"


class ToolRouter:
    """
    Manages tool namespacing and routing.

    Tools are prefixed as skill_name__tool_name when sent to the LLM.
    Incoming tool calls are parsed and dispatched to the correct skill handler
    or to an MCP server for MCP-backed tools.
    """

    def __init__(self, skills: dict[str, Skill], mcp_manager: MCPManager | None = None):
        self._skills = skills
        self._mcp = mcp_manager
        # skill tools: prefixed_name -> (skill_name, ToolDefinition)
        self._tool_index: dict[str, tuple[str, ToolDefinition]] = {}
        # MCP tools: prefixed_name -> (server_name, mcp_tool_name, ToolDefinition)
        self._mcp_index: dict[str, tuple[str, str, ToolDefinition]] = {}
        self._build_index()

    def _build_index(self):
        """Build lookups for skill tools and MCP tools."""
        for skill_name, skill in self._skills.items():
            # Native skill tools
            for tool in skill.tools:
                prefixed = f"{skill_name}{SEPARATOR}{tool.name}"
                self._tool_index[prefixed] = (skill_name, tool)

            # MCP tools declared by this skill
            if self._mcp and skill.mcp_tools:
                self._register_mcp_tools(skill_name, skill.mcp_tools)

    def _register_mcp_tools(self, skill_name: str, mcp_tools: list[dict]) -> None:
        """Register MCP tools for a skill, namespaced under the skill."""
        for entry in mcp_tools:
            server_name = entry.get("server", "")
            requested_tools = entry.get("tools", [])
            default_human = entry.get("human")
            human_overrides = entry.get("human_overrides", {})

            discovered = self._mcp.discover_tools(server_name)
            if not discovered:
                logger.warning(
                    "Skill %s: MCP server '%s' has no tools (not connected?)",
                    skill_name, server_name,
                )
                continue

            for tool_def in discovered:
                # Filter to only requested tools
                if requested_tools != "*" and tool_def.name not in requested_tools:
                    continue

                # Apply skill's human level
                human = human_overrides.get(tool_def.name, default_human)
                effective_def = ToolDefinition(
                    name=tool_def.name,
                    description=tool_def.description,
                    input_schema=tool_def.input_schema,
                    human=human,
                )

                prefixed = f"{skill_name}{SEPARATOR}{tool_def.name}"
                if prefixed in self._tool_index:
                    logger.warning(
                        "MCP tool %s collides with native skill tool — skipping MCP tool",
                        prefixed,
                    )
                    continue

                self._mcp_index[prefixed] = (server_name, tool_def.name, effective_def)

    def collect_tools_for_llm(self) -> list[dict]:
        """
        Collect all tools from all skills + MCP, prefixed for the LLM.

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
        for prefixed, (server_name, _, tool_def) in self._mcp_index.items():
            skill_name = prefixed.split(SEPARATOR, 1)[0]
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
        """Get the ToolDefinition for a prefixed tool name (skill or MCP)."""
        entry = self._tool_index.get(prefixed_name)
        if entry is not None:
            return entry[1]
        mcp_entry = self._mcp_index.get(prefixed_name)
        if mcp_entry is not None:
            return mcp_entry[2]
        return None

    def is_mcp_tool(self, prefixed_name: str) -> bool:
        """Check if a prefixed tool name belongs to an MCP server."""
        return prefixed_name in self._mcp_index

    def route(self, prefixed_name: str, tool_input: dict, ctx: dict) -> str:
        """
        Route a tool call to the correct handler (skill or MCP).

        Returns the handler's result as a string.
        """
        # Try native skill tool first
        entry = self._tool_index.get(prefixed_name)
        if entry is not None:
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

        # Try MCP tool
        mcp_entry = self._mcp_index.get(prefixed_name)
        if mcp_entry is not None and self._mcp:
            server_name, mcp_tool_name, _ = mcp_entry
            return self._mcp.call_tool(server_name, mcp_tool_name, tool_input)

        logger.warning("Unknown tool: %s", prefixed_name)
        return json.dumps({"error": f"Unknown tool: {prefixed_name}"})
