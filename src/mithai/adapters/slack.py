"""Slack adapter using Socket Mode."""

import json
import logging
from typing import Callable

from mithai.adapters.base import Adapter, IncomingMessage, OutgoingMessage
from mithai.human.mcp import HumanRequest

logger = logging.getLogger(__name__)


class SlackAdapter(Adapter):
    """
    Slack adapter using the Bolt SDK with Socket Mode.

    Requires slack-bolt: pip install mithai[slack]
    """

    def __init__(self, bot_token: str, app_token: str, allowed_channels: list[str] | None = None):
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError:
            raise ImportError(
                "Slack adapter requires slack-bolt. Install with: pip install mithai[slack]"
            )

        self._app = App(token=bot_token)
        self._handler = SocketModeHandler(self._app, app_token)
        self._allowed_channels = set(allowed_channels) if allowed_channels else None
        self._bot_token = bot_token

    def start(self, on_message: Callable[[IncomingMessage], str]) -> None:
        @self._app.message("")
        def handle_message(message, say):
            channel = message.get("channel", "")
            if self._allowed_channels and channel not in self._allowed_channels:
                return

            incoming = IncomingMessage(
                text=message.get("text", ""),
                channel_id=channel,
                user_id=message.get("user", "unknown"),
                platform="slack",
                message_id=message.get("ts", ""),
            )

            response = on_message(incoming)
            say(response)

        logger.info("Starting Slack adapter (Socket Mode)")
        self._handler.start()

    def stop(self) -> None:
        self._handler.close()

    def send(self, message: OutgoingMessage) -> None:
        self._app.client.chat_postMessage(
            channel=message.channel_id,
            text=message.text,
        )

    def request_human_approval(self, request: HumanRequest, channel_id: str) -> bool:
        """Post an approval request as a Slack message with action buttons."""
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Approval Required* [{request.level}]\n```{request.description}```",
                },
            },
            {
                "type": "actions",
                "block_id": f"approval_{request.request_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": f"approve_{request.request_id}",
                        "value": "approve",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Deny"},
                        "style": "danger",
                        "action_id": f"deny_{request.request_id}",
                        "value": "deny",
                    },
                ],
            },
        ]

        self._app.client.chat_postMessage(
            channel=channel_id,
            text=f"Approval required for {request.tool_name}",
            blocks=blocks,
        )

        # For now, Slack approval is fire-and-forget.
        # A full implementation would register an action handler and
        # block until the button is clicked or timeout.
        # TODO: implement blocking approval with action handlers
        logger.warning(
            "Slack approval posted for %s but non-blocking approval not yet implemented. "
            "Auto-denying for safety.",
            request.tool_name,
        )
        return False
