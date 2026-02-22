"""
Engine — the central orchestrator.

Composes system prompt from skill prompts, runs the LLM tool-use loop
with Human MCP checks, and coordinates all components.
"""

import json
import logging
from pathlib import Path

from mithai.adapters.base import Adapter, IncomingMessage
from mithai.core.config import get_human_config, get_llm_config, get_skill_config, get_skill_paths
from mithai.core.context import build_context
from mithai.core.skill_loader import Skill, load_skills
from mithai.core.tool_router import ToolRouter
from mithai.human.mcp import HumanMCP
from mithai.llm.base import LLMProvider
from mithai.state.base import StateBackend

logger = logging.getLogger(__name__)


class Engine:
    """
    The brain of mithai.

    Takes an incoming message, builds context, calls the LLM with all
    skill tools, runs the tool-use loop (with Human MCP for approvals),
    and returns the final response text.

    The engine is adapter-agnostic — the adapter is passed per-call
    so the same engine can serve multiple adapters simultaneously.
    """

    def __init__(
        self,
        config: dict,
        llm: LLMProvider,
        state: StateBackend,
    ):
        self._config = config
        self._llm = llm
        self._state = state

        # Load skills
        skill_paths = get_skill_paths(config)
        self._skills = load_skills(skill_paths)
        self._router = ToolRouter(self._skills)
        self._human = HumanMCP(get_human_config(config))
        self._llm_config = get_llm_config(config)

    def handle(self, message: IncomingMessage, adapter: Adapter) -> str:
        """
        Process an incoming message and return the response text.

        Called by adapters for each message. The adapter is passed so
        Human MCP approvals route back to the correct platform.
        """
        system = self._compose_system_prompt()
        tools = self._router.collect_tools_for_llm()
        ctx = build_context(
            state=self._state,
            channel_id=message.channel_id,
            user_id=message.user_id,
            skill_config={
                name: get_skill_config(self._config, name) for name in self._skills
            },
        )

        messages = [{"role": "user", "content": message.text}]

        # Initial LLM call
        response = self._llm.create_message(
            system=system,
            messages=messages,
            tools=tools if tools else None,
            max_tokens=self._llm_config.get("max_tokens", 1024),
        )
        messages.append({"role": "assistant", "content": response.content})

        logger.debug(
            "LLM response: stop_reason=%s, blocks=%d",
            response.stop_reason,
            len(response.content),
        )

        # Tool-use loop
        while response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block["type"] != "tool_use":
                    continue

                prefixed_name = block["name"]
                tool_input = block["input"]
                tool_def = self._router.get_definition(prefixed_name)

                if tool_def is None:
                    result = json.dumps({"error": f"Unknown tool: {prefixed_name}"})
                else:
                    # Human MCP check — routes through the originating adapter
                    approved = self._human.request_approval(
                        prefixed_name, tool_input, tool_def, message.channel_id,
                        adapter=adapter,
                    )

                    if approved:
                        logger.info("Executing tool: %s", prefixed_name)
                        skill_name = prefixed_name.split("__")[0]
                        skill_ctx = build_context(
                            state=self._state,
                            channel_id=message.channel_id,
                            user_id=message.user_id,
                            skill_config=get_skill_config(self._config, skill_name),
                        )
                        # Let the skill know a human explicitly approved this call
                        if tool_def.human is not None:
                            skill_ctx["human_approved"] = True
                        result = self._router.route(prefixed_name, tool_input, skill_ctx)
                    else:
                        logger.info("Tool denied by human: %s", prefixed_name)
                        result = json.dumps({
                            "denied": True,
                            "reason": "Human denied this action",
                        })

                tool_results.append(
                    LLMProvider.format_tool_result(block["id"], result)
                )

            messages.append({"role": "user", "content": tool_results})

            response = self._llm.create_message(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=self._llm_config.get("max_tokens", 1024),
            )
            messages.append({"role": "assistant", "content": response.content})

        # Extract final text
        return self._extract_text(response)

    def _compose_system_prompt(self) -> str:
        """Build the full system prompt from config + skill prompts."""
        bot_config = self._config.get("bot", {})
        base = bot_config.get("system_prompt", "You are a helpful operations assistant.")

        parts = [base]

        parts.append("\n---\n\n## Important: Tool Execution\n")
        parts.append(
            "When you decide a tool is needed, call it directly. "
            "Do NOT ask the user for permission before calling a tool. "
            "Dangerous or sensitive tools have a built-in human approval step — "
            "the user will be prompted with Approve/Deny buttons automatically. "
            "Your job is to decide which tool to call and call it. "
            "Never say 'Would you like me to run this?' — just run it.\n"
        )

        parts.append("\n---\n\n## Your Skills\n")
        for name, skill in self._skills.items():
            parts.append(f"### {name}\n{skill.prompt}\n")

        return "\n".join(parts)

    @staticmethod
    def _extract_text(response) -> str:
        """Extract text content from LLM response."""
        parts = []
        for block in response.content:
            if block.get("type") == "text":
                parts.append(block["text"])
        return "\n".join(parts).strip() or "(no response)"
