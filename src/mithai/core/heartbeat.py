"""
HeartbeatScheduler — periodic engine ticks driven by heartbeat.md in memory.

The agent (or operator) writes `heartbeat.md` to the memory backend with
plain-text instructions for what to do on each tick.  The scheduler re-reads
the file on every tick so instructions can be updated without a restart.

Config (config.yaml):
    heartbeat:
      enabled: true
      interval: 3600   # seconds between ticks (default 3600)
"""

import logging
import threading

from mithai.adapters.base import Adapter, IncomingMessage

logger = logging.getLogger(__name__)

_HEARTBEAT_FILE = "heartbeat.md"
_DEFAULT_AUTO_APPROVE = ["memory__"]
_DEFAULT_INTERVAL = 3600


class _HeartbeatAdapter(Adapter):
    """Minimal adapter for heartbeat ticks — auto-approves configured tool prefixes."""

    def __init__(self, auto_approve: list[str] | None = None):
        self._auto_approve = auto_approve if auto_approve is not None else _DEFAULT_AUTO_APPROVE

    def request_human_approval(self, request, channel_id):
        tool_name = getattr(request, "tool_name", "") or ""
        return any(tool_name.startswith(prefix) for prefix in self._auto_approve)

    def on_thinking_start(self): pass
    def on_thinking_end(self, elapsed_s): pass
    def on_tool_start(self, tool_name, tool_input): pass
    def on_tool_end(self, tool_name, elapsed_s, approved): pass
    def on_synthesizing(self): pass

    def send(self, message): pass
    def start(self, on_message=None, on_channel_join=None, on_observe=None): pass
    def stop(self): pass


class HeartbeatScheduler:
    """
    Runs periodic ticks against an engine.

    Each tick:
      1. Reads `heartbeat.md` from the memory backend.
      2. If absent or empty, skips silently.
      3. Otherwise builds a synthetic IncomingMessage with the file contents
         and runs it through `engine.handle()`.
    """

    def __init__(self, engine, memory, interval: int = _DEFAULT_INTERVAL, auto_approve: list[str] | None = None):
        self._engine = engine
        self._memory = memory
        self._interval = interval
        self._auto_approve = auto_approve if auto_approve is not None else _DEFAULT_AUTO_APPROVE
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)
        self._thread.start()
        logger.info("HeartbeatScheduler started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("HeartbeatScheduler stopped")

    def _loop(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self._tick()
            except Exception:
                logger.warning("Heartbeat tick failed", exc_info=True)

    def _tick(self) -> None:
        instructions = self._memory.read(_HEARTBEAT_FILE)
        if not instructions or not instructions.strip():
            logger.debug("Heartbeat tick: %s absent or empty, skipping", _HEARTBEAT_FILE)
            return

        # Prepend recent channel activity from all channel_context/ files
        context_parts = []
        for path in self._memory.list_files("channel_context", glob="*.md"):
            content = self._memory.read(path)
            if not content or not content.strip():
                continue
            channel_id = path.removeprefix("channel_context/").removesuffix(".md")
            context_parts.append(
                f"Recent channel activity in {channel_id} since last tick:\n{content.strip()}"
            )

        if context_parts:
            full_text = "\n\n---\n\n".join(context_parts) + "\n\n---\n\n" + instructions.strip()
        else:
            full_text = instructions.strip()

        logger.info("Heartbeat tick: running instructions from %s", _HEARTBEAT_FILE)
        message = IncomingMessage(
            text=full_text,
            channel_id="heartbeat",
            user_id="system",
            platform="system",
            thread_id="heartbeat",
        )
        adapter = _HeartbeatAdapter(auto_approve=self._auto_approve)
        try:
            self._engine.handle(message, adapter)
        except Exception:
            logger.warning("Heartbeat engine.handle() failed", exc_info=True)
            return

        # Truncate channel context files after injection — keep last 50 lines as a buffer
        # so the file always contains activity since the last tick, not unbounded history.
        for path in self._memory.list_files("channel_context", glob="*.md"):
            self._truncate_channel_context(path)

    def _truncate_channel_context(self, path: str, keep_lines: int = 50) -> None:
        content = self._memory.read(path)
        if not content:
            return
        lines = content.splitlines(keepends=True)
        if len(lines) > keep_lines:
            self._memory.write(path, "".join(lines[-keep_lines:]))
