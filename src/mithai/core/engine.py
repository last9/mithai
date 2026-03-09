"""
Engine — the central orchestrator.

Composes system prompt from skill prompts, runs the LLM tool-use loop
with Human MCP checks, and coordinates all components.
"""

import json
import logging
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import threading

from mithai.adapters.base import Adapter, IncomingMessage
from mithai.core.config import get_human_config, get_llm_config, get_mcp_config, get_skill_config, get_skill_paths
from mithai.core.context import build_context
from mithai.core.reflection import reflect
from mithai.core.session import SessionManager
from mithai.core.skill_loader import load_skills
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

        # MCP servers — start only the ones skills actually reference
        self._mcp_manager = None
        mcp_config = get_mcp_config(config)
        if mcp_config:
            from mithai.core.mcp_manager import MCPManager

            self._mcp_manager = MCPManager(mcp_config)
            needed = set()
            for skill in self._skills.values():
                for entry in skill.mcp_tools:
                    server = entry.get("server")
                    if server:
                        needed.add(server)
            if needed:
                self._mcp_manager.start(needed)

        self._router = ToolRouter(self._skills, mcp_manager=self._mcp_manager)
        self._human = HumanMCP(get_human_config(config))

        # Run startup hooks for skills that need background work (e.g. polling loops)
        for skill_name, skill in self._skills.items():
            if skill.startup:
                try:
                    skill.startup(get_skill_config(config, skill_name))
                except Exception:
                    logger.warning("Skill %s startup() failed", skill_name, exc_info=True)
        self._llm_config = get_llm_config(config)

        # Learning / memory
        learning_config = config.get("learning", {})
        self._learning_config = learning_config
        self._memory_dir = Path(learning_config.get("memory_dir", "./memory")).resolve()

        # Session memory
        session_config = config.get("sessions", {})
        self._sessions = SessionManager(
            state,
            max_turns=session_config.get("max_stored", 50),
        )
        self._max_history = session_config.get("max_history", 10)

    def late_bind(self, adapters: list[tuple[str, "Adapter"]]) -> None:
        """Give skills access to engine + adapter after full initialization.

        Called from run_cmd after adapters are created but before they start.
        Skills that export bind(engine, adapter) get called here.
        """
        primary_adapter = adapters[0][1] if adapters else None
        for skill_name, skill in self._skills.items():
            if skill.bind:
                try:
                    skill.bind(self, primary_adapter)
                except Exception:
                    logger.warning("Skill %s bind() failed", skill_name, exc_info=True)

    def handle(self, message: IncomingMessage, adapter: Adapter) -> str:
        """
        Process an incoming message and return the response text.

        Called by adapters for each message. The adapter is passed so
        Human MCP approvals route back to the correct platform.
        """
        system = self._compose_system_prompt()
        tools = self._router.collect_tools_for_llm()

        # Load session and build conversation history
        # Use thread_id for Slack threads, fall back to channel_id
        scope = message.thread_id or message.channel_id
        session_key = SessionManager.session_key(message.platform, scope)
        session = self._sessions.load(session_key)
        history = self._build_history(session)

        messages = history + [{"role": "user", "content": message.text}]

        # Initial LLM call
        response = self._llm.create_message(
            system=system,
            messages=messages,
            tools=tools if tools else None,
            max_tokens=self._llm_config.get("max_tokens", 4096),
        )
        messages.append({"role": "assistant", "content": response.content})

        logger.debug(
            "LLM response: stop_reason=%s, blocks=%d",
            response.stop_reason,
            len(response.content),
        )

        # Tool-use loop — track tool calls for session logging
        turn_tool_calls = []

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
                    turn_tool_calls.append({
                        "tool": prefixed_name,
                        "input": tool_input,
                        "error": f"Unknown tool: {prefixed_name}",
                    })
                else:
                    # Build context early — needed for dynamic human resolution and execution
                    skill_name = prefixed_name.split("__")[0]
                    skill_ctx = build_context(
                        state=self._state,
                        channel_id=message.channel_id,
                        user_id=message.user_id,
                        skill_config=get_skill_config(self._config, skill_name),
                    )

                    # Resolve dynamic human level — let the skill decide
                    # MCP tools have static human levels from the skill's MCP_TOOLS declaration
                    effective_def = tool_def
                    if tool_def.human == "dynamic" and not self._router.is_mcp_tool(prefixed_name):
                        skill = self._skills.get(skill_name)
                        if skill and skill.resolve_human:
                            resolved = skill.resolve_human(tool_def.name, tool_input, skill_ctx)
                            effective_def = replace(tool_def, human=resolved)

                    # Human MCP check — routes through the originating adapter
                    approved = self._human.request_approval(
                        prefixed_name, tool_input, effective_def, message.channel_id,
                        adapter=adapter,
                    )

                    if approved:
                        logger.info("Executing tool: %s", prefixed_name)
                        result = self._router.route(prefixed_name, tool_input, skill_ctx)
                    else:
                        logger.info("Tool denied by human: %s", prefixed_name)
                        result = json.dumps({
                            "denied": True,
                            "reason": "Human denied this action",
                        })

                    turn_tool_calls.append({
                        "tool": prefixed_name,
                        "input": tool_input,
                        "approved": approved,
                        "result_summary": result[:500],
                    })

                    # Record approval pattern for learning
                    if effective_def.human is not None:
                        self._record_approval(prefixed_name, tool_input, approved)

                tool_results.append(
                    LLMProvider.format_tool_result(block["id"], result)
                )

            messages.append({"role": "user", "content": tool_results})

            response = self._llm.create_message(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=self._llm_config.get("max_tokens", 4096),
            )
            messages.append({"role": "assistant", "content": response.content})

        # Extract final text, strip any leaked history-format prefix
        response_text = self._extract_text(response)
        response_text = re.sub(r"^\[Tools called:.*?\]\n?", "", response_text).strip()

        # Record turn to session
        turn = SessionManager.build_turn(
            user_id=message.user_id,
            user_message=message.text,
            tool_calls=turn_tool_calls,
            assistant_response=response_text,
        )
        self._sessions.append_turn(session_key, turn)

        # Post-turn reflection — extract learnings in background
        if self._learning_config.get("reflection") and turn_tool_calls:
            threading.Thread(
                target=reflect,
                args=(turn, self._llm, self._memory_dir),
                daemon=True,
            ).start()

        return response_text

    def _build_history(self, session: dict) -> list[dict]:
        """Convert recent session turns into LLM message pairs.

        Uses the native Anthropic tool_use/tool_result format so the LLM
        sees its own calling convention rather than a text summary it might mimic.
        """
        turns = session.get("turns", [])
        recent = turns[-self._max_history:] if turns else []

        messages = []
        for turn_idx, turn in enumerate(recent):
            messages.append({"role": "user", "content": turn["user_message"]})

            tool_calls = turn.get("tool_calls", [])
            if tool_calls:
                # Assistant message with tool_use blocks
                tool_use_blocks = []
                tool_result_blocks = []
                for tc_idx, tc in enumerate(tool_calls):
                    tool_id = f"hist_{turn_idx}_{tc_idx}"
                    tool_use_blocks.append({
                        "type": "tool_use",
                        "id": tool_id,
                        "name": tc["tool"],
                        "input": tc.get("input", {}),
                    })
                    result = tc.get("result_summary", "")
                    if tc.get("error"):
                        result = json.dumps({"error": tc["error"]})
                    elif not tc.get("approved", True):
                        result = json.dumps({"denied": True})
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result,
                    })

                messages.append({"role": "assistant", "content": tool_use_blocks})
                messages.append({"role": "user", "content": tool_result_blocks})

            # Final assistant text response
            messages.append({"role": "assistant", "content": turn["assistant_response"]})

        return messages

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

        # Inject persistent memory
        memory_file = self._memory_dir / "MEMORY.md"
        if memory_file.exists():
            content = memory_file.read_text().strip()
            if content:
                parts.append("\n---\n\n## Your Memory\n")
                parts.append(content)

        today = datetime.now().strftime("%Y-%m-%d")
        daily_file = self._memory_dir / "daily" / f"{today}.md"
        if daily_file.exists():
            content = daily_file.read_text().strip()
            if content:
                parts.append("\n---\n\n## Today's Observations\n")
                parts.append(content)

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

    def stop(self) -> None:
        """Clean up resources (MCP server connections, etc.)."""
        if self._mcp_manager:
            self._mcp_manager.stop()

    def _record_approval(self, prefixed_name: str, tool_input: dict, approved: bool) -> None:
        """Record an approval decision for learning."""
        approvals_file = self._memory_dir / "approvals.json"
        try:
            if approvals_file.exists():
                data = json.loads(approvals_file.read_text())
            else:
                data = {}

            # Key by tool name, sub-key by a normalized input string
            if prefixed_name not in data:
                data[prefixed_name] = {}

            # For shell commands, use the command string as key
            if "command" in tool_input:
                input_key = tool_input["command"]
            else:
                input_key = json.dumps(tool_input, sort_keys=True, separators=(",", ":"))

            if input_key not in data[prefixed_name]:
                data[prefixed_name][input_key] = {"approved": 0, "denied": 0}

            if approved:
                data[prefixed_name][input_key]["approved"] += 1
            else:
                data[prefixed_name][input_key]["denied"] += 1

            approvals_file.parent.mkdir(parents=True, exist_ok=True)
            approvals_file.write_text(json.dumps(data, indent=2))
        except Exception:
            logger.debug("Failed to record approval", exc_info=True)
