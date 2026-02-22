"""Slack adapter using Socket Mode."""

import logging
import threading

from mithai.adapters.base import Adapter, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.adapters.formatters import SlackFormatter
from mithai.human.mcp import HumanRequest

logger = logging.getLogger(__name__)


class SlackAdapter(Adapter):
    """
    Slack adapter using the Bolt SDK with Socket Mode.

    Requires slack-bolt: pip install mithai[slack]
    """

    def __init__(self, bot_token: str, app_token: str, allowed_channels: list[str] | None = None,
                 approval_timeout: int = 300):
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
        self._approval_timeout = approval_timeout

        self._formatter = SlackFormatter()

        # Pending approval requests: request_id -> threading.Event + result
        self._pending_approvals: dict[str, dict] = {}
        self._register_action_handlers()

    def _register_action_handlers(self):
        """Register Slack action handlers for approve/deny buttons."""

        @self._app.action({"action_id": "mithai_approve"})
        def handle_approve(ack, body):
            ack()
            self._handle_approval_action(body, approved=True)

        @self._app.action({"action_id": "mithai_deny"})
        def handle_deny(ack, body):
            ack()
            self._handle_approval_action(body, approved=False)

    def _handle_approval_action(self, body: dict, approved: bool):
        """Process an approve/deny button click."""
        actions = body.get("actions", [])
        if not actions:
            return

        request_id = actions[0].get("value", "")
        pending = self._pending_approvals.get(request_id)
        if not pending:
            logger.warning("Received approval for unknown request: %s", request_id)
            return

        user_id = body.get("user", {}).get("id", "")
        user_name = body.get("user", {}).get("name", "unknown")
        user_mention = f"<@{user_id}>" if user_id else user_name
        action_text = "Approved" if approved else "Denied"
        logger.info("Approval %s by %s for request %s", action_text.lower(), user_name, request_id)

        pending["approved"] = approved
        pending["user"] = user_name
        pending["event"].set()

        # Update the original message to show the decision
        channel = body.get("channel", {}).get("id", "")
        ts = body.get("message", {}).get("ts", "")
        if channel and ts:
            original_text = ""
            for block in body.get("message", {}).get("blocks", []):
                if block.get("type") == "section":
                    original_text = block.get("text", {}).get("text", "")
                    break

            self._app.client.chat_update(
                channel=channel,
                ts=ts,
                text=f"{action_text} by {user_mention}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{original_text}\n\n*{action_text}* by {user_mention}",
                        },
                    },
                ],
            )

    def start(self, on_message: MessageHandler) -> None:
        import re

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

            response = on_message(incoming, self)
            for chunk in self._formatter.format(response):
                say(text=chunk, thread_ts=message.get("ts"))

        @self._app.event("app_mention")
        def handle_app_mention(event, say):
            channel = event.get("channel", "")
            if self._allowed_channels and channel not in self._allowed_channels:
                return

            # Strip the @mention from the text
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", event.get("text", "")).strip()
            if not text:
                say("How can I help?")
                return

            incoming = IncomingMessage(
                text=text,
                channel_id=channel,
                user_id=event.get("user", "unknown"),
                platform="slack",
                message_id=event.get("ts", ""),
            )

            response = on_message(incoming, self)
            for chunk in self._formatter.format(response):
                say(text=chunk, thread_ts=event.get("ts"))

        logger.info("Starting Slack adapter (Socket Mode)")
        self._handler.start()

    def stop(self) -> None:
        self._handler.close()

    def send(self, message: OutgoingMessage) -> None:
        for chunk in self._formatter.format(message.text):
            self._app.client.chat_postMessage(
                channel=message.channel_id,
                text=chunk,
            )

    def request_human_approval(self, request: HumanRequest, channel_id: str) -> bool:
        """
        Post approval buttons in Slack and block until clicked or timeout.

        Uses a threading.Event to synchronize between the action handler
        (called by Bolt on button click) and this blocking call.
        """
        event = threading.Event()
        self._pending_approvals[request.request_id] = {
            "event": event,
            "approved": False,
            "user": None,
        }

        try:
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
                            "action_id": "mithai_approve",
                            "value": request.request_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Deny"},
                            "style": "danger",
                            "action_id": "mithai_deny",
                            "value": request.request_id,
                        },
                    ],
                },
            ]

            self._app.client.chat_postMessage(
                channel=channel_id,
                text=f"Approval required for {request.tool_name}",
                blocks=blocks,
            )

            logger.info(
                "Waiting for approval on %s (timeout: %ds)",
                request.tool_name,
                self._approval_timeout,
            )

            # Block until button clicked or timeout
            clicked = event.wait(timeout=self._approval_timeout)

            if not clicked:
                logger.warning("Approval timed out for %s", request.tool_name)
                self._app.client.chat_postMessage(
                    channel=channel_id,
                    text=f"Approval timed out for `{request.tool_name}` — auto-denied.",
                )
                return False

            return self._pending_approvals[request.request_id]["approved"]

        finally:
            self._pending_approvals.pop(request.request_id, None)
