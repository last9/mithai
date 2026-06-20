"""
Engine — the central orchestrator.

Composes system prompt from skill prompts, runs the LLM tool-use loop
with Human MCP checks, and coordinates all components.
"""

import json
import logging
import re
import time
import threading
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from mithai.adapters.base import Adapter, IncomingMessage
from mithai.core.config import get_human_config, get_llm_config, get_mcp_config, get_skill_config, get_skill_paths
from mithai.core.context import build_context
from mithai.core.reflection import reflect
from mithai.core.verifier import verify, verified_skills_called
from mithai.core.session import SessionManager
from mithai.core.skill_loader import Skill, load_skills
from mithai.core.tool_router import ToolRouter
from mithai.human.mcp import HumanMCP
from mithai.llm.base import LLMProvider
from mithai.memory.base import MemoryBackend
from mithai.state.base import StateBackend

logger = logging.getLogger(__name__)


# Tool-call scaffolding the model sometimes emits as *text* (e.g. after a tool
# denial) instead of a structured tool_use block. It must never reach a human or
# be replayed into history as if it were a valid reply. Markers are lowercase;
# detection lowercases the input, so casing and quote style do not matter.
_TOOL_CALL_MARKERS = ("<function_calls>", "<invoke name=", "<parameter name=")

# Realistic agent replies are bounded by max_tokens (~16k chars at 4096). Above
# this, the paired-tag regexes could backtrack toward O(n^2) on unclosed tags, so
# oversized/degenerate input falls back to a linear, backtracking-free strip.
_MAX_SANITIZE_LEN = 20_000

# The human-meant value lives in a message/text parameter — unwrap it in place.
_CONTENT_PARAM_RE = re.compile(
    r"<parameter\s+name=[\"']?(?:message|text)[\"']?\s*>(.*?)</parameter>", re.S | re.I
)
# Any remaining parameter block is a machine value (result, channel_id, …) — drop it.
_ANY_PARAM_RE = re.compile(r"<parameter\b[^>]*>.*?</parameter>", re.S | re.I)
# Linear, backtracking-free removal of any scaffolding tag, incl. orphaned/partial.
_ORPHAN_TAG_RE = re.compile(r"</?(?:function_calls|invoke|parameter)\b[^>]*>?", re.I)


def _strip_tool_call_syntax(text):
    """Remove leaked tool-call scaffolding from a reply, in place.

    The model sometimes narrates a tool call as *text* (e.g. after a denial)
    instead of a structured tool_use block; that scaffolding must never reach a
    human or be replayed into history. Act whenever a marker appears ANYWHERE — a
    leading prose preamble ("Posting now:") must not be a bypass — and strip the
    tag spans IN PLACE: unwrap the human-meant ``message``/``text`` value, drop
    machine params (``result``, ``channel_id``, …) and the call wrappers, and keep
    all surrounding prose. So a narrated call is cleaned to its message while a
    reply that merely quotes tool-call syntax keeps its sentences (only the literal
    tags go). Case-insensitive and quote-agnostic.
    """
    if not isinstance(text, str):
        return ""
    if not text or not any(m in text.lower() for m in _TOOL_CALL_MARKERS):
        return text
    # A real incident (usually post-denial). Log it so operators see narrate-as-text
    # without diffing what the user saw against the session store.
    logger.warning("stripped leaked tool-call scaffolding from model output (%d chars)", len(text))
    if len(text) > _MAX_SANITIZE_LEN:
        # Avoid backtracking on huge/degenerate input.
        return _ORPHAN_TAG_RE.sub("", text).strip()
    # Unwrap the intended message/text value(s); drop other (machine) params; remove
    # the call wrappers — all in place so surrounding prose survives.
    cleaned = _CONTENT_PARAM_RE.sub(lambda m: m.group(1).strip(), text)
    cleaned = _ANY_PARAM_RE.sub("", cleaned)
    cleaned = re.sub(r"</?function_calls\s*>", "", cleaned, flags=re.I)
    cleaned = re.sub(r"</?invoke\b[^>]*>", "", cleaned, flags=re.I)
    # Terminal guarantee: clear any orphaned/partial scaffolding tags (linear,
    # bounded against pathological nesting).
    for _ in range(3):
        if not any(m in cleaned.lower() for m in _TOOL_CALL_MARKERS):
            break
        cleaned = _ORPHAN_TAG_RE.sub("", cleaned)
    return cleaned.strip()


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

        # MCP servers — start every configured server so agent-level MCP
        # bindings work even when no skill declares MCP_TOOLS. Skills can still
        # expose scoped aliases via MCP_TOOLS; direct tools are exposed as
        # mcp__<server>__<tool>.
        self._mcp_manager = None
        mcp_config = get_mcp_config(config)
        if mcp_config:
            from mithai.core.mcp_manager import MCPManager

            self._mcp_manager = MCPManager(mcp_config)
            self._mcp_manager.start()

        # The router owns tool indexing, including MCP_TOOLS filtering and
        # direct MCP names. Derive the hard allowlist from its actual indexes
        # so the boundary cannot drift from dispatchable tools.
        self._router = ToolRouter(self._skills, mcp_manager=self._mcp_manager)
        self._router.lock_allowlist()
        self._human = HumanMCP(get_human_config(config))

        # Run startup hooks for skills that need background work (e.g. polling loops)
        for skill_name, skill in self._skills.items():
            if skill.startup:
                try:
                    skill.startup(get_skill_config(config, skill_name))
                except Exception:
                    logger.warning("Skill %s startup() failed", skill_name, exc_info=True)
        self._llm_config = get_llm_config(config)

        # Per-channel onboarding locks — prevent concurrent handle_channel_join calls
        # for the same channel (startup check + member_joined events racing).
        self._onboarding_locks: dict[str, threading.Lock] = {}
        self._onboarding_locks_mu = threading.Lock()

        # Learning / memory
        learning_config = config.get("learning", {})
        self._learning_config = learning_config

        # Verifier — collect skills that opted in via VERIFY = True
        self._verified_skills: set[str] = {
            name for name, skill in self._skills.items() if skill.verify
        }

        # Separate cheap LLM for verification; falls back to main LLM if not configured
        verifier_config = config.get("verifier", {})
        verifier_model = verifier_config.get("model")
        if verifier_model and isinstance(self._llm, self._llm.__class__):
            try:
                self._verifier_llm = self._llm.__class__(
                    api_key=get_llm_config(config).get("api_key", ""),
                    model=verifier_model,
                )
            except Exception:
                logger.warning("Could not create verifier LLM, falling back to main LLM")
                self._verifier_llm = self._llm
        else:
            self._verifier_llm = self._llm

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

        self._inject_slack_roster(adapters)

    _ROSTER_PATH = "team/roster.md"
    _ROSTER_ID_RE = r"U[A-Z0-9]{6,}"

    @classmethod
    def _parse_roster_pairs(cls, text: str | None) -> dict[str, str]:
        """Extract {name: slack_id} pairs from a roster file, tolerant of format.

        Handles the three shapes seen in agent rosters: `Name (Uxxxx)` (headings /
        bold prose), `Name - Uxxxx` (dash/colon separated), and `| Uxxxx | Name |`
        (table rows). First match for a name wins. Returns {} on empty/None.
        """
        pairs: dict[str, str] = {}
        if not text:
            return pairs

        def clean(s: str) -> str:
            return re.sub(r"[*`_>#]", "", s).strip(" -•|\t").strip()

        uid = cls._ROSTER_ID_RE
        # 1) Name (Uxxxx)
        for m in re.finditer(rf"([A-Za-z][\w .'\-/]{{0,60}}?)[\s*`_]*\(({uid})\)", text):
            name = clean(m.group(1))
            if name:
                pairs.setdefault(name, m.group(2))
        # 2) Name - Uxxxx  (dash/colon separated, no parens)
        for m in re.finditer(rf"([A-Za-z][\w .'\-/]{{0,60}}?)[\s*`_]*[-–:]\s*({uid})\b", text):
            name = clean(m.group(1))
            if name:
                pairs.setdefault(name, m.group(2))
        # 3) Table row | Uxxxx | Name |
        for m in re.finditer(rf"\|\s*({uid})\s*\|\s*([^|\n]+?)\s*\|", text):
            name = clean(m.group(2))
            if name:
                pairs.setdefault(name, m.group(1))
        return pairs

    def _inject_slack_roster(self, adapters: list[tuple[str, "Adapter"]]) -> None:
        """Push roster name->id pairs to every Slack adapter's SlackClient.

        Framework-owned injection point (not a skill bind() hook, which agents
        override). Reads the roster from memory and hands the integration layer a
        plain dict, so integrations stays memory-ignorant. Never raises.
        """
        slack_clients = [
            client
            for _, adapter in adapters
            if (client := getattr(adapter, "slack_client", None)) is not None
            and hasattr(client, "set_roster_fallback")
        ]
        if not slack_clients:
            return
        roster_text = None
        if self._memory is not None:
            try:
                roster_text = self._memory.read(self._ROSTER_PATH)
            except Exception:
                logger.warning("Failed to read roster for mention fallback", exc_info=True)
        pairs = self._parse_roster_pairs(roster_text)
        for client in slack_clients:
            try:
                client.set_roster_fallback(pairs)
            except Exception:
                logger.warning("set_roster_fallback failed", exc_info=True)

    def handle(self, message: IncomingMessage, adapter: Adapter) -> str:
        """
        Process an incoming message and return the response text.

        Called by adapters for each message. The adapter is passed so
        Human MCP approvals route back to the correct platform.
        """
        from mithai.telemetry import get_tracer
        tracer = get_tracer()

        if tracer is not None:
            from opentelemetry.trace import SpanKind
            span_ctx = tracer.start_as_current_span("mithai.request", kind=SpanKind.SERVER)
        else:
            span_ctx = nullcontext()

        # Last9 conversation context — groups all spans under a conversation ID
        try:
            from last9_genai import conversation_context
            conversation_id = message.thread_id or message.channel_id or ""
            conv_ctx = conversation_context(
                conversation_id=conversation_id,
                user_id=message.user_id or "",
            )
        except ImportError:
            conv_ctx = nullcontext()

        with span_ctx as req_span, conv_ctx:
            if req_span is not None:
                req_span.set_attribute("mithai.platform", message.platform)
                req_span.set_attribute("mithai.channel_id", message.channel_id or "")
                req_span.set_attribute("mithai.thread_id", message.thread_id or "")
                req_span.set_attribute("mithai.user_id", message.user_id or "")
                if self._agent_id:
                    req_span.set_attribute("mithai.agent_id", self._agent_id)
                if message.text:
                    req_span.set_attribute("mithai.message.text", message.text[:500])

            from mithai.telemetry.metrics import record_request
            record_request(message.platform)

            return self._handle_inner(message, adapter, tracer)

    def _handle_inner(self, message: IncomingMessage, adapter: Adapter, tracer) -> str:
        system = self._compose_system_prompt()
        if message.extra_system_prompt:
            system += "\n\n---\n\n## Task Instructions\n" + message.extra_system_prompt.strip()
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
        observation_images: list[dict] = []
        if pending:
            context_lines = "\n".join(f"  {o['user_id']}: {o['text']}" for o in pending)
            text_content = (
                f"{backfill_prefix}"
                f"[Thread context — messages since your last response:]\n{context_lines}\n\n"
                f"{message.text}"
            )
            # Collect images from observations so the LLM can see what was shared
            for o in pending:
                observation_images.extend(o.get("images", []))
        else:
            text_content = f"{backfill_prefix}{message.text}"

        # Build user content — plain string or multi-modal blocks if images are attached
        all_images = observation_images + [
            {"data": img.data, "media_type": img.media_type}
            for img in message.images
        ]
        if all_images:
            logger.info("Building multi-modal content with %d image(s)", len(all_images))
            user_content: str | list = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data"],
                    },
                }
                for img in all_images
            ] + [{"type": "text", "text": text_content}]
        else:
            user_content = text_content

        messages = history + [{"role": "user", "content": user_content}]

        # Initial LLM call
        adapter.on_thinking_start()
        t0 = time.monotonic()
        response = self._llm.create_message(
            system=system,
            messages=messages,
            tools=tools if tools else None,
            max_tokens=self._llm_config.get("max_tokens", 4096),
            call_type="initial",
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
        _wf_active = False

        # Last9 workflow context — groups tool-use rounds as a workflow
        if response.stop_reason == "tool_use":
            try:
                from last9_genai import workflow_context
                _wf_ctx = workflow_context(
                    workflow_id=f"{session_key}:{len(session.get('turns', []))}",
                    workflow_type="tool_use_loop",
                )
                _wf_ctx.__enter__()
                _wf_active = True
            except ImportError:
                pass

        # Recovery budget for max_tokens truncation mid-tool_use (see below).
        max_truncation_recoveries = 2
        truncation_recoveries = 0

        while response.stop_reason == "tool_use" or any(
            b["type"] == "tool_use" for b in response.content
        ):
            if response.stop_reason != "tool_use":
                # max_tokens cut generation mid-tool_use: the response carries
                # tool_use blocks that were never executed. Each one must be
                # answered before the next API call, or the request fails with
                # "tool_use ids were found without tool_result blocks".
                orphans = [b for b in response.content if b["type"] == "tool_use"]
                orphan_ids = [b["id"] for b in orphans]
                truncation_recoveries += 1
                logger.warning(
                    "Response ended with %d unanswered tool_use block(s) "
                    "(stop_reason=%s) — recovery %d/%d",
                    len(orphan_ids), response.stop_reason,
                    truncation_recoveries, max_truncation_recoveries,
                )
                if truncation_recoveries > max_truncation_recoveries:
                    # Stop retrying. Strip the orphan blocks from the last
                    # assistant message so the conversation stays valid for
                    # any follow-up call (e.g. the silent-response nudge).
                    cleaned = [b for b in messages[-1]["content"] if b["type"] != "tool_use"]
                    # Placeholder keeps the assistant message non-empty for API
                    # validity only — it is never returned to the user (the turn
                    # falls through to the nudge / "(no response)" handling).
                    messages[-1]["content"] = cleaned or [
                        {"type": "text", "text": "(response truncated by output token limit)"}
                    ]
                    break
                messages.append({"role": "user", "content": [
                    LLMProvider.format_tool_result(oid, json.dumps({
                        "error": "This tool call did not execute: the response was "
                                 f"cut off before it completed (stop_reason="
                                 f"{response.stop_reason}). Retry with a smaller "
                                 "input, e.g. split the work into chunks.",
                    }))
                    for oid in orphan_ids
                ]})
                adapter.on_synthesizing()
                t2 = time.monotonic()
                response = self._llm.create_message(
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=self._llm_config.get("max_tokens", 4096),
                    call_type="synthesis",
                    after_tools=[b["name"] for b in orphans],
                )
                adapter.on_thinking_end(time.monotonic() - t2)
                messages.append({"role": "assistant", "content": response.content})
                continue

            tool_results = []
            round_tool_names = []

            for block in response.content:
                if block["type"] != "tool_use":
                    continue

                prefixed_name = block["name"]
                tool_input = block["input"]
                round_tool_names.append(prefixed_name)
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

                    before_tool_call = getattr(type(adapter), "before_tool_call", None)
                    preflight_result = (
                        before_tool_call(adapter, prefixed_name, tool_input)
                        if before_tool_call
                        else None
                    )

                    # Human MCP check — routes through the originating adapter. Adapter
                    # preflight runs first so suppressed tools cannot leak approval prompts.
                    approved = True
                    if preflight_result is None:
                        approved = self._human.request_approval(
                            prefixed_name, tool_input, effective_def, message.channel_id,
                            adapter=adapter,
                        )

                    if tracer is not None:
                        from opentelemetry.trace import SpanKind
                        tool_span_ctx = tracer.start_as_current_span(
                            prefixed_name, kind=SpanKind.INTERNAL
                        )
                    else:
                        tool_span_ctx = nullcontext()

                    with tool_span_ctx as tool_span:
                        if tool_span is not None:
                            tool_span.set_attribute("mithai.tool.name", prefixed_name)
                            tool_span.set_attribute("mithai.tool.approved", approved)
                            if effective_def.human is not None:
                                tool_span.set_attribute(
                                    "mithai.tool.human_level", str(effective_def.human)
                                )
                            try:
                                tool_span.set_attribute("mithai.tool.input", json.dumps(tool_input)[:500])
                            except Exception:
                                logger.debug("Failed to serialize tool_input for span", exc_info=True)

                        if approved:
                            t1 = time.monotonic()
                            if preflight_result is not None:
                                logger.info("Tool suppressed by adapter preflight: %s", prefixed_name)
                                result = preflight_result
                            else:
                                logger.info("Executing tool: %s", prefixed_name)
                                adapter.on_tool_start(prefixed_name, tool_input)
                                result = self._router.route(prefixed_name, tool_input, skill_ctx)
                                adapter.on_tool_end(prefixed_name, time.monotonic() - t1, True)
                            tool_elapsed = time.monotonic() - t1
                        else:
                            tool_elapsed = 0.0
                            logger.info("Tool denied by human: %s", prefixed_name)
                            result = json.dumps({
                                "denied": True,
                                "reason": "Human denied this action",
                            })
                            adapter.on_tool_end(prefixed_name, 0.0, False)

                        from mithai.telemetry.metrics import record_tool_call
                        record_tool_call(prefixed_name, approved, tool_elapsed)
                        on_tool_result = getattr(adapter, "on_tool_result", None)
                        if on_tool_result:
                            on_tool_result(prefixed_name, tool_input, result)

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
                call_type="synthesis",
                after_tools=round_tool_names,
            )
            adapter.on_thinking_end(time.monotonic() - t2)
            messages.append({"role": "assistant", "content": response.content})

        # Close Last9 workflow context after tool-use loop
        if _wf_active:
            _wf_ctx.__exit__(None, None, None)

        # Extract raw text first (no fallback yet) so the nudge check sees "" not "(no response)"
        response_text = self._extract_raw_text(response)
        # Re-sanitize after the prefix strip: removing a leading "[Tools called:]"
        # marker could re-expose a scaffolding wrapper that _extract_raw_text's
        # lead-with guard had skipped. If this strips to empty, the
        # silent-after-tools nudge below re-asks the model for prose.
        response_text = _strip_tool_call_syntax(
            re.sub(r"^\[Tools called:.*?\]\n?", "", response_text).strip()
        )

        # If the model went silent after a tool chain, nudge it to produce a reply.
        # Pass tools so it can take corrective action (e.g. memory_write after reading a gap)
        # rather than narrating intent it cannot execute.
        if not response_text and turn_tool_calls:
            logger.debug("LLM produced no text after tool chain — nudging for summary")
            messages.append({"role": "user", "content": "Please reply to the user now."})
            nudge_response = self._llm.create_message(
                system=system,
                messages=messages,
                tools=tools if tools else None,
                max_tokens=self._llm_config.get("max_tokens", 4096),
                call_type="synthesis",
            )
            response_text = self._extract_raw_text(nudge_response).strip()

        response_text = response_text or "(no response)"

        # Post-turn fact-check — only for turns that used a verified skill
        if self._verified_skills and verified_skills_called(turn_tool_calls, self._verified_skills):
            failure = verify(response_text, turn_tool_calls, self._verifier_llm)
            if failure:
                response_text = f"{response_text}\n\n⚠️ *Fact-check flagged:* {failure}"

        # Record turn to session (include images so history can replay them)
        # Persist text_content (not message.text) so that backfill and thread
        # observations are part of the session history. If message.text were saved
        # instead, the enriched context would only exist in the live LLM call and
        # would be lost from Turn 2 onwards when history is replayed via _build_history.
        turn = SessionManager.build_turn(
            user_id=message.user_id,
            user_message=text_content,
            tool_calls=turn_tool_calls,
            assistant_response=response_text,
            images=all_images if all_images else None,
        )
        self._sessions.append_turn(session_key, turn)

        # Post-turn reflection — extract learnings in background
        if self._learning_config.get("reflection") and turn_tool_calls and self._memory:
            logger.info(
                "reflection: spawning background task (%d tool calls)",
                len(turn_tool_calls),
            )
            threading.Thread(
                target=reflect,
                args=(turn, self._llm, self._memory),
                daemon=True,
            ).start()
        elif not self._learning_config.get("reflection"):
            logger.debug("reflection: skipped — disabled in config")
        elif not turn_tool_calls:
            logger.debug("reflection: skipped — no tool calls this turn")
        else:
            logger.debug("reflection: skipped — no memory backend configured")

        return response_text

    def _onboarding_lock(self, channel_id: str) -> threading.Lock:
        with self._onboarding_locks_mu:
            if channel_id not in self._onboarding_locks:
                self._onboarding_locks[channel_id] = threading.Lock()
            return self._onboarding_locks[channel_id]

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

        # Acquire per-channel lock to prevent concurrent runs (e.g. multiple
        # member_joined events + startup check all firing before done: is written).
        lock = self._onboarding_lock(channel_id)
        if not lock.acquire(blocking=False):
            logger.info("Onboarding for %s already in progress — skipping", channel_id)
            return None
        try:
            # Double-check after acquiring: another process may have completed between
            # the caller's is_channel_onboarded check and this acquire.
            if self.is_channel_onboarded(channel_id):
                logger.info("Channel %s already started onboarding — skipping", channel_id)
                return None
            # Mark started immediately so the startup check (which reads state, not the
            # in-process lock) skips this channel on the next boot even if we crash.
            self._state.set("onboarding", f"started:{channel_id}", True)
            return self._run_onboarding(channel_id, channel_name)
        finally:
            lock.release()

    def _run_onboarding(self, channel_id: str, channel_name: str) -> str | None:
        """Execute the two-phase onboarding flow for a channel.

        Phase 1: gather channel context via tools (members, history, memory).
        Phase 2: generate the intro message with a minimal no-tools prompt.
        Called only after the per-channel lock is held and started: is written.
        """
        # Clear any stale session from a previous join so old history doesn't
        # contaminate the new onboarding LLM call.
        session_key = SessionManager.session_key("slack", f"onboard:{channel_id}", agent_id=self._agent_id)
        self._sessions.delete(session_key)

        # Phase 1 — gather info via tools (steps 1–5 only, no text output requested)
        onboarding_md = Path.cwd() / "onboarding.md"
        if onboarding_md.exists():
            template = onboarding_md.read_text(encoding="utf-8")
            gather_text = template.replace("{channel_id}", channel_id).replace("{channel_name}", channel_name)
        else:
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
                f"facts, correct stale entries, remove duplicates. Keep it concise.\n"
                f"6. Write a short intro message that reflects what this channel is for and shows "
                f"you already know the team.\n\n"
                f"Execute steps 1–5 using your tools without narrating them. "
                f"When all tool calls are complete, your final text response must be exactly the intro message — "
                f"3–5 sentences, no bullet points, no emojis, no preamble, no reasoning. "
                f"Do not explain what you did. Do not list what is new. Just write the intro."
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
        # System prompt already contains MEMORY.md (written by phase 1); no need to replay
        # phase-1 history here — that could be huge for active channels and hit context limits.
        bot_name = self._config.get("bot", {}).get("name") or self._agent_id or "I"
        name_clause = f"Your name is {bot_name}. " if bot_name != "I" else ""
        intro_prompt = (
            f"{name_clause}"
            f"Write a short intro message (3-5 sentences, no bullet points, no emojis) "
            f"for the Slack channel #{channel_name}. "
            f"Introduce yourself by name, reflect what this channel is for, and show you already know the team. "
            f"Output only the intro message — no preamble, no explanation, nothing else."
        )
        system = self._compose_system_prompt()
        intro_response = self._llm.create_message(
            system=system,
            messages=[{"role": "user", "content": intro_prompt}],
            tools=None,
            max_tokens=512,
            call_type="synthesis",
        )
        intro = self._extract_text(intro_response)
        # Mark channel as onboarded so startup check skips it on next boot.
        self._state.set("onboarding", f"done:{channel_id}", True)
        return intro

    def is_channel_onboarded(self, channel_id: str) -> bool:
        """Return True if onboarding has started (or completed) for this channel.

        Checking started: (not just done:) means the startup check on restart
        skips channels that began onboarding in a previous process lifetime,
        preventing a second intro post if the bot is restarted mid-onboarding.
        """
        return bool(
            self._state.get("onboarding", f"started:{channel_id}")
            or self._state.get("onboarding", f"done:{channel_id}")
        )

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
                    obs: dict = {
                        "user_id": message.user_id,
                        "text": message.text,
                    }
                    if message.images:
                        obs["images"] = [
                            {"data": img.data, "media_type": img.media_type}
                            for img in message.images
                        ]
                    self._sessions.append_observation(key, obs)

    # How many of the most recent turns can include images in history replay.
    # Older turns get text-only to bound context size.
    _MAX_IMAGE_HISTORY_TURNS = 2

    def _build_history(self, session: dict) -> list[dict]:
        """Convert recent session turns into LLM message pairs.

        Uses the native Anthropic tool_use/tool_result format so the LLM
        sees its own calling convention rather than a text summary it might mimic.

        Images are included for the most recent ``_MAX_IMAGE_HISTORY_TURNS``
        turns that have them, keeping older turns text-only to avoid context bloat.
        """
        turns = session.get("turns", [])
        recent = turns[-self._max_history:] if turns else []

        # Find which turns may carry images (only the last N)
        image_budget = self._MAX_IMAGE_HISTORY_TURNS
        image_turn_indices: set[int] = set()
        for idx in range(len(recent) - 1, -1, -1):
            if recent[idx].get("images") and image_budget > 0:
                image_turn_indices.add(idx)
                image_budget -= 1

        messages = []
        for turn_idx, turn in enumerate(recent):
            # Reconstruct multi-modal content if this turn had images
            images = turn.get("images", [])
            if images and turn_idx in image_turn_indices:
                user_content: str | list = [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img["media_type"],
                            "data": img["data"],
                        },
                    }
                    for img in images
                ] + [{"type": "text", "text": turn["user_message"]}]
            else:
                user_content = turn["user_message"]

            messages.append({"role": "user", "content": user_content})

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

            # Final assistant text response. Sanitize so a past turn that leaked
            # tool-call scaffolding can't be replayed as a valid example.
            # _strip_tool_call_syntax coerces None/non-string to ""; the sentinel
            # then avoids an empty content block (the API rejects those).
            messages.append({
                "role": "assistant",
                "content": _strip_tool_call_syntax(turn.get("assistant_response")) or "(no response)",
            })

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
    def _extract_raw_text(response) -> str:
        """Extract text content from an LLM response, sanitized, '' when empty.

        Sanitization runs at this chokepoint so every text-extraction path —
        live reply, post-tool nudge, and the onboarding intro (via
        ``_extract_text``) — is protected against leaked tool-call scaffolding
        without each caller having to remember. See ``_strip_tool_call_syntax``.
        """
        parts = []
        for block in response.content:
            if block.get("type") == "text":
                parts.append(block["text"])
        return _strip_tool_call_syntax("\n".join(parts).strip())

    @staticmethod
    def _extract_text(response) -> str:
        """Extract text content from LLM response."""
        return Engine._extract_raw_text(response) or "(no response)"

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
