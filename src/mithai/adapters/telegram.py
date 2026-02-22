"""Telegram adapter using long-polling."""

import json
import logging
import time
import requests

from mithai.adapters.base import Adapter, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.adapters.formatters import TelegramFormatter
from mithai.human.mcp import HumanRequest

logger = logging.getLogger(__name__)


class TelegramAdapter(Adapter):
    """
    Telegram adapter using the Bot API with long-polling.

    Uses raw HTTP requests (no extra dependency beyond requests).
    """

    def __init__(
        self,
        bot_token: str,
        allowed_chat_ids: list[str] | None = None,
        poll_interval: int = 2,
    ):
        self._token = bot_token
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._allowed_chats = set(str(c) for c in allowed_chat_ids) if allowed_chat_ids else None
        self._poll_interval = poll_interval
        self._offset = 0
        self._running = False
        self._formatter = TelegramFormatter()

    def _api(self, method: str, **kwargs) -> dict:
        resp = requests.post(f"{self._base_url}/{method}", json=kwargs, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data.get("result", {})

    def start(self, on_message: MessageHandler) -> None:
        self._running = True
        logger.info("Starting Telegram adapter (long-polling)")

        while self._running:
            try:
                updates = self._api(
                    "getUpdates",
                    offset=self._offset,
                    timeout=20,
                )

                for update in updates:
                    self._offset = update["update_id"] + 1
                    msg = update.get("message")
                    if not msg or "text" not in msg:
                        continue

                    chat_id = str(msg["chat"]["id"])
                    if self._allowed_chats and chat_id not in self._allowed_chats:
                        continue

                    incoming = IncomingMessage(
                        text=msg["text"],
                        channel_id=chat_id,
                        user_id=str(msg["from"]["id"]),
                        platform="telegram",
                        message_id=str(msg["message_id"]),
                    )

                    response = on_message(incoming, self)
                    self.send(OutgoingMessage(text=response, channel_id=chat_id))

            except KeyboardInterrupt:
                break
            except Exception:
                logger.exception("Telegram polling error")
                time.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False

    def send(self, message: OutgoingMessage) -> None:
        for chunk in self._formatter.format(message.text):
            self._api(
                "sendMessage",
                chat_id=message.channel_id,
                text=chunk,
                parse_mode="HTML",
            )

    def request_human_approval(self, request: HumanRequest, channel_id: str) -> bool:
        """Send approval request with inline keyboard buttons."""
        text = (
            f"*Approval Required* [{request.level}]\n\n"
            f"```\n{request.description}\n```\n\n"
        )

        if request.level == "confirm":
            text += f"Reply with the resource name to confirm, or 'deny' to reject."
            self._api(
                "sendMessage",
                chat_id=channel_id,
                text=text,
                parse_mode="Markdown",
            )
            # Poll for the confirmation reply
            return self._wait_for_confirmation(channel_id, request)
        else:
            # "approve" — use inline keyboard
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "Approve", "callback_data": f"approve:{request.request_id}"},
                        {"text": "Deny", "callback_data": f"deny:{request.request_id}"},
                    ]
                ]
            }
            self._api(
                "sendMessage",
                chat_id=channel_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return self._wait_for_callback(channel_id, request)

    def _wait_for_callback(self, channel_id: str, request: HumanRequest) -> bool:
        """Poll for inline keyboard callback."""
        import time as _time

        deadline = _time.time() + 300  # 5 min timeout
        while _time.time() < deadline:
            updates = self._api("getUpdates", offset=self._offset, timeout=10)
            for update in updates:
                self._offset = update["update_id"] + 1
                cb = update.get("callback_query")
                if cb and cb.get("data", "").startswith(("approve:", "deny:")):
                    action, req_id = cb["data"].split(":", 1)
                    if req_id == request.request_id:
                        # Acknowledge the callback
                        self._api("answerCallbackQuery", callback_query_id=cb["id"])
                        return action == "approve"
        logger.warning("Approval timed out for %s", request.tool_name)
        return False

    def _wait_for_confirmation(self, channel_id: str, request: HumanRequest) -> bool:
        """Poll for a text confirmation reply."""
        import time as _time

        deadline = _time.time() + 300
        while _time.time() < deadline:
            updates = self._api("getUpdates", offset=self._offset, timeout=10)
            for update in updates:
                self._offset = update["update_id"] + 1
                msg = update.get("message")
                if msg and str(msg["chat"]["id"]) == channel_id:
                    text = msg.get("text", "").strip().lower()
                    if text == "deny":
                        return False
                    # Check if any input value matches the reply
                    for v in request.tool_input.values():
                        if isinstance(v, str) and text == v.lower():
                            return True
        logger.warning("Confirmation timed out for %s", request.tool_name)
        return False
