"""
Engine — the central orchestrator.

Composes system prompt from skill prompts, runs the LLM tool-use loop
with Human MCP checks, and coordinates all components.
"""

import json
import logging
import re
import time
from dataclasses import replace
from datetime import datetime

import threading

from mithai.adapters.base import Adapter, IncomingMessage
from mithai.core.config import get_human_config, get_llm_config, get_mcp_config, get_skill_config, get_skill_paths
from mithai.core.context import build_context
from mithai.core.reflection import reflect
from mithai.core.session import SessionManager
from mithai.core.skill_loader import Skill, load_skills
from mithai.core.tool_router import ToolRouter
from mithai.human.mcp import HumanMCP
from mithai.llm.base import LLMProvider
from mithai.memory.base import MemoryBackend
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
        memory: MemoryBackend | None = None,
        *,
        agent_id: str | None = None,
        skills: dict[str, Skill] | None = None,
    ):
        self._config = config
        self._llm = llm
        self._state = state
        self._memory = memory
        self._agent_id = agent_id

        # Load skills — accept pre-filtered skills for multi-agent, otherwise load all
        if skills is not None:
            self._skills = skills
        else:
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

        # Build allowed tools set from native + MCP tools for hard rejection
        allowed_tools = {f"{sname}__{t.name}" for sname, s in self._skills.items() for t in s.tools}
        # Include MCP tools discovered from servers so they aren't rejected
        if self._mcp_manager:
            for sname, s in self._skills.items():
                for entry in s.mcp_tools:
                    server = entry.get("server")
                    if not server:
                        continue
                    for mcp_tool in self._mcp_manager.discover_tools(server):
                        allowed_tools.add(f"{sname}__{mcp_tool.name}")
        self._router = ToolRouter(self._skills, mcp_manager=self._mcp_manager, allowed_tools=allowed_tools)
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

        Each adapter is offered to each skill in order so skills that look for
        a specific interface (e.g. slack_client) find it regardless of adapter
        list position.
        """
        for skill_name, skill in self._skills.items():
            if not skill.bind:
                continue
            for _, adapter in adapters:
                try:
                    skill.bind(self, adapter)
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
        session_key = SessionManager.session_key(message.platform, scope, agent_id=self._agent_id)
        session = self._sessions.load(session_key)
        history = self._build_history(session)

        # Thread backfill — first @mention in an existing thread (no agent turns yet)
        # Fetch prior messages so the agent has full context from the start.
        backfill_prefix = ""
        is_thread_reply = message.thread_id and message.thread_id != message.message_id
        if is_thread_reply and not session.get("turns"):
            thread_history = adapter.fetch_thread_context(message.channel_id, message.thread_id)
            if thread_history:
                backfill_prefix = (
                    "[Thread history — messages before you were mentioned:]\n"
                    + "\n".join(f"  {line}" for line in thread_history)
                    + "\n\n"
                )

        # Drain any thread observations accumulated since the last agent response
        pending = self._sessions.pop_observations(session_key)
        if pending:
            context_lines = "\n".join(f"  {o['user_id']}: {o['text']}" for o in pending)
            user_content = (
                f"{backfill_prefix}"
                f"[Thread context — messages since your last response:]\n{context_lines}\n\n"
                f"{message.text}"
            )
        else:
            user_content = f"{backfill_prefix}{message.text}"

        messages = history + [{"role": "user", "content": user_content}]

        # Initial LLM call
        adapter.on_thinking_start()
        t0 = time.monotonic()
        response = self._llm.create_message(
            system=system,
            messages=messages,
            tools=tools if tools else None,
            max_tokens=self._llm_config.get("max_tokens", 4096),
        )
        adapter.on_thinking_end(time.monotonic() - t0)
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
                        memory=self._memory,
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
                        adapter.on_tool_start(prefixed_name, tool_input)
                        t1 = time.monotonic()
                        result = self._router.route(prefixed_name, tool_input, skill_ctx)
                        tool_elapsed = time.monotonic() - t1
                        adapter.on_tool_end(prefixed_name, tool_elapsed, True)
                    else:
                        logger.info("Tool denied by human: %s", prefixed_name)
                        result = json.dumps({
                            "denied": True,
                            "reason": "Human denied this action",
                        })
                        adapter.on_tool_end(prefixed_name, 0.0, False)

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

            adapter.on_synthesizing()
            t2 = time.monotonic()
            response = self._llm.create_message(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=self._llm_config.get("max_tokens", 4096),
            )
            adapter.on_thinking_end(time.monotonic() - t2)
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
        if self._learning_config.get("reflection") and turn_tool_calls and self._memory:
            threading.Thread(
                target=reflect,
                args=(turn, self._llm, self._memory),
                daemon=True,
            ).start()

        return response_text

    def handle_channel_join(self, channel_id: str, channel_name: str) -> str | None:
        """
        Called when the bot is added to a new Slack channel.

        Fetches channel history (via the callback set on the adapter), feeds a
        synthetic onboarding message through the normal engine loop, and returns
        the intro text to post.  Uses an isolated session so onboarding tool calls
        don't pollute the channel's regular conversation history.
        """
        onboarding_config = self._config.get("onboarding", {})
        if not onboarding_config.get("enabled", False):
            return None

        # Clear any stale session from a previous join so old history doesn't
        # contaminate the new onboarding LLM call.
        session_key = SessionManager.session_key("slack", f"onboard:{channel_id}", agent_id=self._agent_id)
        self._sessions.delete(session_key)

        # Phase 1 — gather info via tools (steps 1–5 only, no text output requested)
        gather_text = (
            f"You were just added to the Slack channel #{channel_name} (ID: {channel_id}).\n\n"
            f"You serve this organisation across several Slack channels. Each channel is a different "
            f"facet of the same team — people, projects, and context overlap across channels. "
            f"All knowledge is shared and cumulative.\n\n"
            f"Execute these steps using tools. Output no text — only call tools:\n"
            f"1. Read your existing MEMORY.md to recall what you already know about this org.\n"
            f"2. Call slack_get_members with channel_id={channel_id} to get the full member roster.\n"
            f"3. Call slack_get_history with channel_id={channel_id} to read recent messages.\n"
            f"4. Take a nuanced view: most people will already be in your memory from other channels. "
            f"Identify what is genuinely new — new members, new projects, new patterns.\n"
            f"5. Update MEMORY.md using overwrite mode with a clean merged version: fold in new "
            f"facts, correct stale entries, remove duplicates. Keep it concise."
        )

        fake_message = IncomingMessage(
            text=gather_text,
            channel_id=channel_id,
            user_id="system",
            platform="slack",
            thread_id=f"onboard:{channel_id}",
        )

        _ONBOARD_ALLOWED = ("memory__", "slack__slack_get_history", "slack__slack_get_members")

        class _NoOpAdapter:
            """Minimal adapter stub for onboarding — approves memory and read-only Slack tools."""
            def request_human_approval(self, request, channel_id):
                tool_name = getattr(request, "tool_name", "") or ""
                return any(tool_name.startswith(prefix) for prefix in _ONBOARD_ALLOWED)

            def on_thinking_start(self): pass
            def on_thinking_end(self, elapsed_s): pass
            def on_tool_start(self, tool_name, tool_input): pass
            def on_tool_end(self, tool_name, elapsed_s, approved): pass
            def on_synthesizing(self): pass
            def fetch_thread_context(self, channel_id, thread_ts): return None

        self.handle(fake_message, _NoOpAdapter())

        # Phase 2 — write the intro in a clean no-tools call so there is nothing to narrate.
        # Load phase-1 session history so the intro call has full context of what was gathered
        # (member roster, channel history, tool results) even when memory is absent or incomplete.
        session = self._sessions.load(session_key)
        history = self._build_history(session)
        intro_prompt = (
            f"Write a short intro message (3-5 sentences, no bullet points, no emojis) "
            f"for the Slack channel #{channel_name}. "
            f"Reflect what this channel is for and show you already know the team. "
            f"Output only the intro message — no preamble, no explanation, nothing else."
        )
        system = self._compose_system_prompt()
        intro_response = self._llm.create_message(
            system=system,
            messages=history + [{"role": "user", "content": intro_prompt}],
            tools=None,
            max_tokens=512,
        )
        return self._extract_text(intro_response)

    def _log_to_channel_context(self, message: IncomingMessage) -> None:
        """Append a single message line to channel_context/{channel_id}.md.

        Uses message.message_id as the timestamp — for Slack messages this is
        the actual Slack ts (e.g. '1736553600.123456') which the LLM can use
        directly as thread_ts. ISO wall-clock time would cause the LLM to
        hallucinate invalid thread_ts values when trying to reply in threads.
        """
        if self._memory is None:
            return
        self._memory.write(
            f"channel_context/{message.channel_id}.md",
            f"{message.message_id} | {message.user_id} | {message.text}\n",
            append=True,
        )

    def log_outgoing(self, channel_id: str, user_id: str, text: str, message_id: str = "") -> None:
        """Log a bot outgoing response to channel_context only.

        Unlike observe(), this never touches pending_observations — it purely
        writes a channel_context entry so the heartbeat can see the bot already
        replied in this channel/thread.
        """
        if self._memory is None:
            return
        self._memory.write(
            f"channel_context/{channel_id}.md",
            f"{message_id} | {user_id} | {text}\n",
            append=True,
        )

    def observe(self, message: IncomingMessage) -> None:
        """Silently record an observed message to channel memory. No LLM call.

        If the message is a thread reply and the agent has an active session for
        that thread (at least one completed turn), also store it as a pending
        observation so the agent sees it as context on the next @mention.
        """
        self._log_to_channel_context(message)

        if message.thread_id:
            key = SessionManager.session_key(
                message.platform, message.thread_id, agent_id=self._agent_id
            )
            session = self._sessions.get_session(key)
            if session and session.get("turns"):
                # Skip if this message was just processed by handle() — it's already
                # in the session history and doesn't need to be shown again as context.
                last_turn = session["turns"][-1]
                if last_turn.get("user_message") != message.text:
                    self._sessions.append_observation(key, {
                        "user_id": message.user_id,
                        "text": message.text,
                    })

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
        if self._memory:
            content = self._memory.read("MEMORY.md")
            if content and content.strip():
                parts.append("\n---\n\n## Your Memory\n")
                parts.append(content.strip())

            today = datetime.now().strftime("%Y-%m-%d")
            content = self._memory.read(f"daily/{today}.md")
            if content and content.strip():
                parts.append("\n---\n\n## Today's Observations\n")
                parts.append(content.strip())

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
        if not self._memory:
            return
        try:
            data = self._memory.read_json("approvals.json") or {}

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

            self._memory.write_json("approvals.json", data)
        except Exception:
            logger.debug("Failed to record approval", exc_info=True)
