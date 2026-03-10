"""Slack adapters: SlackAdapterBase (shared logic), SlackAdapter (Socket Mode)."""

import json
import logging
import threading

from mithai.adapters.base import Adapter, ChannelJoinHandler, ChannelObserveHandler, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.adapters.formatters import SlackBlockFormatter, _blocks_fallback
from mithai.human.mcp import HumanRequest

logger = logging.getLogger(__name__)


class SlackAdapterBase(Adapter):
    """
    Base class with all shared Slack logic.

    Subclasses provide transport (Socket Mode vs HTTP) via start()/stop().
    """

    def __init__(self, bot_token: str, allowed_channels: list[str] | None = None,
                 approval_timeout: int = 300, signing_secret: str | None = None,
                 respond: str = "all"):
        try:
            from slack_bolt import App
        except ImportError:
            raise ImportError(
                "Slack adapter requires slack-bolt. Install with: pip install mithai[slack]"
            )

        app_kwargs: dict = {"token": bot_token}
        if signing_secret:
            app_kwargs["signing_secret"] = signing_secret
        self._app = App(**app_kwargs)

        self._allowed_channels = set(allowed_channels) if allowed_channels else None
        self._bot_token = bot_token
        self._approval_timeout = approval_timeout

        self._respond = respond
        self._formatter = SlackBlockFormatter()
        # Per-thread storage for the current message ts — prevents concurrent
        # messages from overwriting each other's thread context.
        self._local = threading.local()

        # Bot's own user ID — resolved on start via auth.test
        self._bot_user_id: str | None = None

        # Pending approval requests: request_id -> threading.Event + result
        self._pending_approvals: dict[str, dict] = {}
        self._register_action_handlers()

    def _register_action_handlers(self):
        """Register Slack action handlers for approve/deny buttons."""

        @self._app.action("mithai_approve")
        def handle_approve(ack, body):
            try:
                ack()
            except Exception:
                logger.exception("ack() failed in handle_approve")
            self._handle_approval_action(body, approved=True)

        @self._app.action("mithai_deny")
        def handle_deny(ack, body):
            try:
                ack()
            except Exception:
                logger.exception("ack() failed in handle_deny")
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

            updated_text = f"{original_text}\n\n*{action_text}* by {user_mention}"
            if len(updated_text) > 3000:
                updated_text = updated_text[:2950] + f"\n...\n\n*{action_text}* by {user_mention}"
            self._app.client.chat_update(
                channel=channel,
                ts=ts,
                text=f"{action_text} by {user_mention}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": updated_text,
                        },
                    },
                ],
            )

    def _register_message_handlers(self, on_message: MessageHandler,
                                    on_channel_join: ChannelJoinHandler | None = None,
                                    on_observe: ChannelObserveHandler | None = None):
        """
        Resolve bot user ID and register all message/event handlers.

        Called by subclass start() before launching the transport.
        """
        import re

        # Resolve the bot's own user ID so we can detect self-join events
        try:
            auth = self._app.client.auth_test()
            self._bot_user_id = auth["user_id"]
            logger.info("Bot user ID: %s", self._bot_user_id)
        except Exception:
            logger.warning("Could not resolve bot user ID", exc_info=True)

        if on_channel_join:
            @self._app.event("member_joined_channel")
            def handle_member_joined(event, say):
                # Only act when the bot itself joins
                if event.get("user") != self._bot_user_id:
                    return

                channel_id = event.get("channel", "")
                if self._allowed_channels and channel_id not in self._allowed_channels:
                    return

                # Resolve channel name
                try:
                    info = self._app.client.conversations_info(channel=channel_id)
                    channel_name = info["channel"].get("name", channel_id)
                except Exception:
                    channel_name = channel_id

                logger.info("Bot joined channel #%s (%s) — running onboarding in background", channel_name, channel_id)

                def _run():
                    try:
                        intro = on_channel_join(channel_id, channel_name)
                        if intro:
                            self._send_formatted(say, intro, thread_ts=None)
                    except Exception:
                        logger.exception("Onboarding failed for #%s", channel_name)

                threading.Thread(target=_run, daemon=True).start()

        @self._app.message("")
        def handle_message(message, say):
            channel = message.get("channel", "")
            if self._allowed_channels and channel not in self._allowed_channels:
                return

            raw_text = message.get("text", "")

            # Skip messages with @mentions — app_mention handler covers those
            if re.search(r"<@[A-Z0-9]+>", raw_text):
                return

            incoming = IncomingMessage(
                text=raw_text.strip(),
                channel_id=channel,
                user_id=message.get("user", "unknown"),
                platform="slack",
                message_id=message.get("ts", ""),
                thread_id=message.get("thread_ts"),
            )

            if self._respond == "mentions":
                if on_observe:
                    on_observe(incoming)
                return

            ts = message.get("ts", "")
            self._local.thread_ts = ts
            self._react(channel, ts, "thinking_face")
            try:
                response = on_message(incoming, self)
                self._send_formatted(say, response, thread_ts=ts)
            finally:
                self._unreact(channel, ts, "thinking_face")

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
                thread_id=event.get("thread_ts"),
            )

            ts = event.get("ts", "")
            self._local.thread_ts = ts
            self._react(channel, ts, "thinking_face")
            try:
                response = on_message(incoming, self)
                self._send_formatted(say, response, thread_ts=ts)
            finally:
                self._unreact(channel, ts, "thinking_face")

        @self._app.event("message")
        def handle_message_subtype_events(body):
            # Silently acknowledge message subtypes (channel_join, message_changed,
            # bot_message, etc.) that @app.message("") does not match.
            pass

    def _resolve_user_ids(self, user_ids: set[str]) -> dict[str, str]:
        """Return a map of user_id -> display_name for the given set of IDs."""
        result = {}
        for uid in user_ids:
            try:
                resp = self._app.client.users_info(user=uid)
                profile = resp["user"].get("profile", {})
                name = (
                    profile.get("display_name")
                    or profile.get("real_name")
                    or resp["user"].get("name")
                    or uid
                )
                result[uid] = name
            except Exception:
                result[uid] = uid
        return result

    def _fetch_channel_history(self, channel_id: str, limit: int) -> tuple[list[str], dict[str, str]]:
        """
        Fetch recent messages from a channel.

        Returns (formatted_messages, user_id_to_name_map).
        User IDs in messages are replaced with real display names.
        """
        import re

        try:
            resp = self._app.client.conversations_history(channel=channel_id, limit=limit)
        except Exception:
            logger.warning("Failed to fetch history for channel %s", channel_id, exc_info=True)
            return [], {}

        if not resp.get("ok"):
            logger.warning("conversations_history error for %s: %s", channel_id, resp.get("error"))
            return [], {}
        raw_messages = resp.get("messages", [])

        all_user_ids: set[str] = set()
        for msg in raw_messages:
            if uid := msg.get("user"):
                all_user_ids.add(uid)
            for mentioned in re.findall(r"<@([A-Z0-9]+)>", msg.get("text", "")):
                all_user_ids.add(mentioned)

        user_map = self._resolve_user_ids(all_user_ids)

        def _replace_mentions(text: str) -> str:
            return re.sub(
                r"<@([A-Z0-9]+)>",
                lambda m: f"@{user_map.get(m.group(1), m.group(1))}",
                text,
            )

        formatted = []
        for msg in reversed(raw_messages):  # oldest first
            uid = msg.get("user", "unknown")
            name = user_map.get(uid, uid)
            text = _replace_mentions(msg.get("text", "")).strip()
            if text:
                formatted.append(f"{name}: {text}")

        return formatted, user_map

    def _send_formatted(self, say, response: str, thread_ts: str | None) -> None:
        """Format a response and send via say(), using Block Kit when available."""
        for chunk in self._formatter.format(response):
            try:
                blocks = json.loads(chunk)
                if isinstance(blocks, list) and blocks:
                    say(blocks=blocks, text=_blocks_fallback(blocks), thread_ts=thread_ts)
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
            say(text=chunk, thread_ts=thread_ts)

    def _react(self, channel: str, ts: str, emoji: str) -> None:
        """Add a reaction emoji to a message, ignoring errors (e.g. missing scope)."""
        try:
            self._app.client.reactions_add(channel=channel, timestamp=ts, name=emoji)
        except Exception:
            pass

    def _unreact(self, channel: str, ts: str, emoji: str) -> None:
        """Remove a reaction emoji from a message, ignoring errors."""
        try:
            self._app.client.reactions_remove(channel=channel, timestamp=ts, name=emoji)
        except Exception:
            pass

    def send(self, message: OutgoingMessage) -> None:
        for chunk in self._formatter.format(message.text):
            try:
                blocks = json.loads(chunk)
                if isinstance(blocks, list) and blocks:
                    self._app.client.chat_postMessage(
                        channel=message.channel_id,
                        blocks=blocks,
                        text=_blocks_fallback(blocks),
                    )
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
            self._app.client.chat_postMessage(channel=message.channel_id, text=chunk)

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

            post_kwargs = {
                "channel": channel_id,
                "text": f"Approval required for {request.tool_name}",
                "blocks": blocks,
            }
            current_thread_ts = getattr(self._local, "thread_ts", None)
            if current_thread_ts:
                post_kwargs["thread_ts"] = current_thread_ts
            self._app.client.chat_postMessage(**post_kwargs)

            logger.info(
                "Waiting for approval on %s (timeout: %ds)",
                request.tool_name,
                self._approval_timeout,
            )

            # Block until button clicked or timeout
            clicked = event.wait(timeout=self._approval_timeout)

            if not clicked:
                logger.warning("Approval timed out for %s", request.tool_name)
                timeout_kwargs = {
                    "channel": channel_id,
                    "text": f"Approval timed out for `{request.tool_name}` — auto-denied.",
                }
                if current_thread_ts:
                    timeout_kwargs["thread_ts"] = current_thread_ts
                self._app.client.chat_postMessage(**timeout_kwargs)
                return False

            return self._pending_approvals[request.request_id]["approved"]

        finally:
            self._pending_approvals.pop(request.request_id, None)


class SlackAdapter(SlackAdapterBase):
    """
    Slack adapter using the Bolt SDK with Socket Mode.

    Requires slack-bolt: pip install mithai[slack]
    """

    def __init__(self, bot_token: str, app_token: str, allowed_channels: list[str] | None = None,
                 approval_timeout: int = 300, respond: str = "all"):
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError:
            raise ImportError(
                "Slack adapter requires slack-bolt. Install with: pip install mithai[slack]"
            )

        super().__init__(bot_token, allowed_channels, approval_timeout, respond=respond)
        self._handler = SocketModeHandler(self._app, app_token)

    def start(self, on_message: MessageHandler, on_channel_join: ChannelJoinHandler | None = None,
              on_observe: ChannelObserveHandler | None = None) -> None:
        self._register_message_handlers(on_message, on_channel_join, on_observe)
        logger.info("Starting Slack adapter (Socket Mode)")
        self._handler.start()

    def stop(self) -> None:
        self._handler.close()
