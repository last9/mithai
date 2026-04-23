"""Headless API adapter — process stays alive while /api/trigger handles all input."""

import logging
import threading

from mithai.adapters.base import Adapter, MessageHandler, OutgoingMessage
from mithai.human.mcp import HumanRequest

logger = logging.getLogger(__name__)


class APIAdapter(Adapter):
    """Blocks forever so the embedded API server (MITHAI_UI_PORT) can serve all traffic.

    No stdin, no Slack connection required. Responses are logged at INFO level.
    Human approval requests are auto-denied (no interactive terminal).
    """

    def __init__(self):
        self._stop_event = threading.Event()

    def start(self, on_message: MessageHandler, on_channel_join=None,
              on_observe=None, on_bot_reply=None) -> None:
        self._stop_event.wait()

    def stop(self) -> None:
        self._stop_event.set()

    def send(self, message: OutgoingMessage) -> None:
        text = message.text
        preview = text[:200] + ("…" if len(text) > 200 else "")
        logger.info("[api] → %s", preview)

    def request_human_approval(self, request: HumanRequest, channel_id: str) -> bool:
        logger.warning(
            "api adapter: auto-denying approval for '%s' (no interactive terminal)",
            request.tool_name,
        )
        return False
