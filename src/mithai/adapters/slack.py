"""Slack adapters: SlackAdapterBase (shared logic), SlackAdapter (Socket Mode)."""

import fnmatch
import json
import logging
import re
import threading
import time

from mithai.adapters.base import Adapter, BotReplyHandler, ChannelJoinHandler, ChannelObserveHandler, ImageAttachment, IncomingMessage, MessageHandler, OutgoingMessage
from mithai.adapters.formatters import SlackBlockFormatter, _blocks_fallback
from mithai.human.mcp import HumanRequest
from mithai.integrations.slack import SlackClient

logger = logging.getLogger(__name__)

# A message older than this when it reaches the adapter is "stale" — it sat in
# the control plane's delivery queue (agent outage, retry backlog) rather than
# arriving live. Slack's event `ts` is the original send time, so age is
# computable here with no extra protocol plumbing.
STALE_EVENT_THRESHOLD_SECONDS = 600

# Injected via IncomingMessage.extra_system_prompt so the response posture is
# prompt-tunable, not hardcoded: acknowledge the delay when a reply is still
# useful, absorb quietly when the moment has passed.
STALE_EVENT_NOTE = (
    "Delivery notice: this message was sent {age_minutes} minutes ago and is only "
    "reaching you now (delayed delivery — e.g. an outage or a retry backlog). "
    "If a reply is still useful, answer it and briefly acknowledge you are "
    "catching up on an older message. If the conversation has clearly moved on "
    "or the question was already handled, do not re-answer it — note the "
    "content for your own context and keep any reply minimal."
)


def _staleness_note(event_ts: str) -> str:
    """Staleness guidance for an event, or "" when fresh or ts is unusable.

    The Slack listeners are the sole writers of extra_system_prompt for live
    Slack turns (scheduling injects on the CLI adapter, a separate transport).
    If another Slack-side writer ever appears, this assignment must become a
    merge — today it would silently overwrite.
    """
    try:
        age = time.time() - float(event_ts)
    except (TypeError, ValueError):
        return ""
    if age <= STALE_EVENT_THRESHOLD_SECONDS:
        return ""
    return STALE_EVENT_NOTE.format(age_minutes=int(age // 60))


class SlackAdapterBase(Adapter):
    """
    Base class with all shared Slack logic.

    Subclasses provide transport (Socket Mode vs HTTP) via start()/stop().
    """

    def __init__(self, bot_token: str, allowed_channels: list[str] | None = None,
                 approval_timeout: int = 300, signing_secret: str | None = None,
                 respond: str = "all", response_policy: dict | None = None):
        try:
            from slack_bolt import App
        except ImportError:
            raise ImportError(
                "Slack adapter requires slack-bolt. Install with: pip install mithai[slack]"
            )

        if not bot_token or bot_token.startswith("${"):
            raise RuntimeError(
                "SLACK_BOT_TOKEN is missing or not set.\n"
                "  1. Go to https://api.slack.com/apps → select your app\n"
                "  2. OAuth & Permissions → copy the Bot User OAuth Token\n"
                "  3. Add SLACK_BOT_TOKEN=xoxb-... to your .env file"
            )

        app_kwargs: dict = {"token": bot_token}
        if signing_secret:
            app_kwargs["signing_secret"] = signing_secret
        # NOTE: we deliberately do NOT set process_before_response=True. Running the
        # listener inline (before the HTTP ack) would block Bolt's event loop for
        # the whole turn — including a human approval wait — so the approve/deny
        # CLICK (a new POST to the same /slack/events) could not be served, and the
        # approval would deadlock/time out. Fast-ack (the default) runs the turn in
        # a background thread, keeping the endpoint responsive for the click. The
        # control-plane durable queue guarantees DELIVERY (retry until a running
        # agent 2xx-acks receipt); processing reliability matches Socket Mode.
        try:
            self._app = App(**app_kwargs)
        except Exception as exc:
            msg = str(exc)
            if "invalid_auth" in msg or "token" in msg.lower():
                raise RuntimeError(
                    "Slack authentication failed — your SLACK_BOT_TOKEN is invalid or expired.\n"
                    "  1. Go to https://api.slack.com/apps → select your app\n"
                    "  2. OAuth & Permissions → copy the Bot User OAuth Token\n"
                    "  3. Update SLACK_BOT_TOKEN in your .env file"
                ) from None
            raise

        self._allowed_channels = set(allowed_channels) if allowed_channels is not None else set()
        self._bot_token = bot_token
        self._approval_timeout = approval_timeout
        self._leaving_channels: set[str] = set()
        self._leaving_lock = threading.Lock()

        self._respond = respond
        self._formatter = SlackBlockFormatter()
        self._slack_client = SlackClient(bot_token)
        # Per-thread storage for the current message ts — prevents concurrent
        # messages from overwriting each other's thread context.
        self._local = threading.local()
        self._channel_name_cache: dict[str, str | None] = {}
        self._configure_response_policy(response_policy)

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
                                    on_observe: ChannelObserveHandler | None = None,
                                    on_bot_reply: BotReplyHandler | None = None):
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
                if channel_id not in self._allowed_channels:
                    self._decline_and_leave(channel_id)
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
            if channel not in self._allowed_channels:
                return

            raw_text = message.get("text", "")

            # Skip messages that @mention the bot — app_mention handler covers those.
            # Messages mentioning only other users must still be observed so the agent
            # sees team replies (e.g. "@alice can you check this?") in pending_observations.
            bot_id = self._bot_user_id or ""
            if bot_id and re.search(rf"<@{re.escape(bot_id)}>", raw_text):
                return
            # If bot ID is unknown, fall back to skipping any @mention to avoid
            # double-processing until the bot ID is resolved at startup.
            if not bot_id and re.search(r"<@[A-Z0-9]+>", raw_text):
                return

            if self._respond == "mentions":
                if on_observe:
                    # Download images for thread replies so the bot has visual
                    # context when it is next @mentioned in that thread.
                    thread_ts = message.get("thread_ts")
                    images, _skipped = self._extract_images(message) if thread_ts else ([], [])
                    on_observe(IncomingMessage(
                        text=raw_text.strip(),
                        channel_id=channel,
                        user_id=message.get("user", "unknown"),
                        platform="slack",
                        message_id=message.get("ts", ""),
                        thread_id=thread_ts or message.get("ts", ""),
                        images=images,
                    ))
                return

            incoming = IncomingMessage(
                text=raw_text.strip(),
                channel_id=channel,
                user_id=message.get("user", "unknown"),
                platform="slack",
                message_id=message.get("ts", ""),
                thread_id=message.get("thread_ts") or message.get("ts", ""),
                images=self._extract_images(message)[0],
                extra_system_prompt=_staleness_note(message.get("ts", "")),
            )

            ts = message.get("ts", "")
            self._local.thread_ts = ts
            self._reset_response_policy(channel)
            self._react(channel, ts, "thinking_face")
            try:
                response = on_message(incoming, self)
                if not self._should_suppress_final_response():
                    self._send_formatted(say, response, thread_ts=ts)
                if on_bot_reply and self._bot_user_id and response and not self._should_suppress_final_response():
                    on_bot_reply(channel, self._bot_user_id, response, ts)
            finally:
                self._unreact(channel, ts, "thinking_face")
                self._clear_response_policy()

        @self._app.event("app_mention")
        def handle_app_mention(event, say):
            channel = event.get("channel", "")
            if channel not in self._allowed_channels:
                self._decline_and_leave(channel)
                return

            # Strip only the bot's own @mention; resolve other user mentions to display names
            raw_text = event.get("text", "")
            bot_id = self._bot_user_id or ""
            other_user_ids = set(re.findall(r"<@([A-Z0-9]+)>", raw_text)) - {bot_id}
            user_map = self._slack_client.resolve_user_ids(other_user_ids) if other_user_ids else {}
            text = re.sub(
                r"<@([A-Z0-9]+)>\s*",
                lambda m: "" if m.group(1) == bot_id else f"@{user_map.get(m.group(1), m.group(1))} ",
                raw_text,
            ).strip()
            images, skipped_files = self._extract_images(event)
            if not text and not images:
                if skipped_files:
                    say(f"I can only read images right now — I can't process files like {', '.join(skipped_files)}. "
                        "Try sharing a screenshot or image instead!")
                else:
                    say("How can I help?")
                return

            # Inform the LLM about non-image files it can't see
            if skipped_files:
                text = (f"[Note: the user also shared non-image file(s) that you cannot read: "
                        f"{', '.join(skipped_files)}. Let them know you can only process images.]\n\n{text}")

            incoming = IncomingMessage(
                text=text,
                channel_id=channel,
                user_id=event.get("user", "unknown"),
                platform="slack",
                message_id=event.get("ts", ""),
                thread_id=event.get("thread_ts") or event.get("ts", ""),
                images=images,
                extra_system_prompt=_staleness_note(event.get("ts", "")),
            )

            ts = event.get("ts", "")
            self._local.thread_ts = ts
            self._reset_response_policy(channel)
            self._react(channel, ts, "thinking_face")
            try:
                response = on_message(incoming, self)
                if not self._should_suppress_final_response():
                    self._send_formatted(say, response, thread_ts=ts)
                if on_bot_reply and self._bot_user_id and response and not self._should_suppress_final_response():
                    on_bot_reply(channel, self._bot_user_id, response, ts)
            finally:
                self._unreact(channel, ts, "thinking_face")
                self._clear_response_policy()

            if on_observe:
                on_observe(incoming)

        @self._app.event("message")
        def handle_message_subtype_events(body):
            # Catch message subtypes that @app.message("") doesn't match.
            # Most subtypes (channel_join, message_changed, bot_message) are
            # silently acknowledged.  file_share thread replies are routed to
            # the observe path so images are captured for pending observations.
            event = body.get("event", {})
            if event.get("subtype") != "file_share":
                return
            if not on_observe or self._respond != "mentions":
                return
            thread_ts = event.get("thread_ts")
            if not thread_ts:
                return
            channel = event.get("channel", "")
            if channel not in self._allowed_channels:
                return
            images, _skipped = self._extract_images(event)
            on_observe(IncomingMessage(
                text=event.get("text", "").strip(),
                channel_id=channel,
                user_id=event.get("user", "unknown"),
                platform="slack",
                message_id=event.get("ts", ""),
                thread_id=thread_ts,
                images=images,
            ))

    def fetch_thread_context(self, channel_id: str, thread_ts: str) -> list[str] | None:
        """Fetch prior thread messages for backfill context. Delegates to SlackClient."""
        return self._slack_client.get_thread_replies(channel_id, thread_ts)

    @property
    def slack_client(self) -> SlackClient:
        """SlackClient instance for use by skills."""
        return self._slack_client

    def _resolve_user_ids(self, user_ids: set[str]) -> dict[str, str]:
        return self._slack_client.resolve_user_ids(user_ids)

    def _fetch_channel_history(self, channel_id: str, limit: int) -> tuple[list[str], dict[str, str]]:
        return self._slack_client.get_history(channel_id, limit)

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

    def _should_suppress_final_response(self) -> bool:
        return self._is_response_policy_blocked("final_response_to_source")

    def _reset_response_policy(self, channel: str) -> None:
        self._local.current_channel_id = channel
        self._local.current_channel_class = self._classify_response_policy_channel(channel)
        self._local.response_policy_suppressed = False
        self._local.response_policy_blocks = set()
        self._local.response_policy_allow_sends_to_classes = None

    def _clear_response_policy(self) -> None:
        self._local.current_channel_id = None
        self._local.current_channel_class = None
        self._local.response_policy_suppressed = False
        self._local.response_policy_blocks = set()
        self._local.response_policy_allow_sends_to_classes = None

    def _configure_response_policy(self, response_policy: dict | None) -> None:
        self._response_policy_enabled = False
        self._response_policy_exact_ids: dict[str, str] = {}
        self._response_policy_name_patterns: dict[str, list[str]] = {}
        self._response_policy_name_regex: dict[str, list[re.Pattern]] = {}
        self._response_policy_suppress_applies_in: set[str] = set()
        self._response_policy_suppress_blocks: set[str] = set()
        self._response_policy_allow_sends_to_classes: set[str] | None = None

        if not response_policy:
            return
        if not isinstance(response_policy, dict):
            raise ValueError("adapter.slack.response_policy must be a mapping")
        if not response_policy.get("enabled", False):
            return

        channel_classes = response_policy.get("channel_classes") or {}
        if not isinstance(channel_classes, dict):
            raise ValueError("adapter.slack.response_policy.channel_classes must be a mapping")

        self._response_policy_enabled = True
        for class_name, class_config in channel_classes.items():
            if class_config is None:
                class_config = {}
            if not isinstance(class_config, dict):
                raise ValueError(f"response policy channel class {class_name!r} must be a mapping")

            for channel_id in self._string_list(class_config.get("ids")):
                existing_class = self._response_policy_exact_ids.get(channel_id)
                if existing_class and existing_class != class_name:
                    raise ValueError(
                        f"Slack channel ID {channel_id!r} appears in both "
                        f"{existing_class!r} and {class_name!r} response policy classes"
                    )
                self._response_policy_exact_ids[channel_id] = class_name

            self._response_policy_name_patterns[class_name] = self._string_list(
                class_config.get("name_patterns")
            )
            self._response_policy_name_regex[class_name] = [
                re.compile(pattern) for pattern in self._string_list(class_config.get("name_regex"))
            ]

        policies = response_policy.get("tool_result_policies") or {}
        if not isinstance(policies, dict):
            raise ValueError("adapter.slack.response_policy.tool_result_policies must be a mapping")
        suppress_policy = policies.get("suppress_final_response") or {}
        if not isinstance(suppress_policy, dict):
            raise ValueError(
                "adapter.slack.response_policy.tool_result_policies.suppress_final_response "
                "must be a mapping"
            )

        self._response_policy_suppress_applies_in = set(
            self._string_list(suppress_policy.get("applies_in"))
        )
        self._response_policy_suppress_blocks = set(
            self._string_list(suppress_policy.get("blocks"))
        )
        if "allow_sends_to_classes" in suppress_policy:
            self._response_policy_allow_sends_to_classes = set(
                self._string_list(suppress_policy.get("allow_sends_to_classes"))
            )

    @staticmethod
    def _string_list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ValueError(f"Expected a string or list of strings, got {type(value).__name__}")

    def _classify_response_policy_channel(self, channel_id: str | None) -> str | None:
        if not self._response_policy_enabled or not channel_id:
            return None

        exact_class = self._response_policy_exact_ids.get(channel_id)
        if exact_class:
            return exact_class

        channel_name = self._response_policy_channel_name(channel_id)
        if not channel_name:
            return None

        matches: list[str] = []
        for class_name, patterns in self._response_policy_name_patterns.items():
            if any(fnmatch.fnmatchcase(channel_name, pattern) for pattern in patterns):
                matches.append(class_name)
        for class_name, regexes in self._response_policy_name_regex.items():
            if any(regex.search(channel_name) for regex in regexes):
                matches.append(class_name)

        unique_matches = list(dict.fromkeys(matches))
        if len(unique_matches) > 1:
            logger.warning(
                "Slack channel %s (#%s) matched multiple response policy classes: %s",
                channel_id,
                channel_name,
                ", ".join(unique_matches),
            )
            return None
        return unique_matches[0] if unique_matches else None

    def _response_policy_channel_name(self, channel_id: str) -> str | None:
        if channel_id in self._channel_name_cache:
            return self._channel_name_cache[channel_id]

        try:
            info = self._app.client.conversations_info(channel=channel_id)
            channel_name = info.get("channel", {}).get("name")
        except Exception:
            logger.warning(
                "Could not resolve Slack channel name for response policy: %s",
                channel_id,
                exc_info=True,
            )
            channel_name = None

        if channel_name:
            self._channel_name_cache[channel_id] = channel_name
        return channel_name

    def _is_response_policy_blocked(self, block: str) -> bool:
        if not getattr(self._local, "response_policy_suppressed", False):
            return False
        return block in getattr(self._local, "response_policy_blocks", set())

    def _activate_response_policy_suppression(self) -> None:
        source_class = getattr(self._local, "current_channel_class", None)
        if source_class not in self._response_policy_suppress_applies_in:
            return
        self._local.response_policy_suppressed = True
        self._local.response_policy_blocks = set(self._response_policy_suppress_blocks)
        self._local.response_policy_allow_sends_to_classes = (
            None
            if self._response_policy_allow_sends_to_classes is None
            else set(self._response_policy_allow_sends_to_classes)
        )

    @staticmethod
    def _is_slack_send_message_tool(tool_name: str) -> bool:
        return tool_name.split("__")[-1] == "slack_send_message"

    def _suppressed_send_result(self, channel_id: str) -> str:
        return json.dumps({
            "ok": True,
            "suppressed": True,
            "reason": "suppressed_by_response_policy",
            "channel_id": channel_id,
        })

    def _should_block_slack_tool_send(self, channel_id: str | None) -> bool:
        if not channel_id or not getattr(self._local, "response_policy_suppressed", False):
            return False

        current_channel_id = getattr(self._local, "current_channel_id", None)
        if (
            channel_id == current_channel_id
            and self._is_response_policy_blocked("tool_send_to_source")
        ):
            return True

        allow_sends_to_classes = getattr(
            self._local,
            "response_policy_allow_sends_to_classes",
            None,
        )
        if allow_sends_to_classes is None or channel_id == current_channel_id:
            return False

        target_class = self._classify_response_policy_channel(channel_id)
        return target_class not in allow_sends_to_classes

    def startup_onboard(self, is_onboarded, on_join) -> None:
        """Onboard allowed channels that haven't been onboarded yet.

        Runs after socket connect. Joins channels if needed, or runs
        on_join directly for channels the bot is already in.
        """
        if not self._allowed_channels or not on_join:
            return

        time.sleep(3)  # Let socket mode complete its handshake

        for channel_id in self._allowed_channels:
            if is_onboarded(channel_id):
                continue

            try:
                resp = self._app.client.conversations_info(channel=channel_id)
                channel_name = resp["channel"].get("name", channel_id)
                is_member = resp["channel"].get("is_member", False)
            except Exception:
                logger.warning("conversations_info failed for %s — skipping", channel_id, exc_info=True)
                continue

            logger.info("Startup onboarding for #%s (%s)", channel_name, channel_id)
            if not is_member:
                try:
                    self._app.client.conversations_join(channel=channel_id)
                    time.sleep(2)
                    verify = self._app.client.conversations_info(channel=channel_id)
                    if not verify.get("channel", {}).get("is_member", False):
                        logger.warning(
                            "Joined #%s but was auto-removed (workspace admin policy) — "
                            "invite the bot manually with /invite @<botname>",
                            channel_name,
                        )
                except Exception:
                    logger.warning("Could not join #%s — skipping startup onboarding", channel_name)
            else:
                try:
                    intro = on_join(channel_id, channel_name)
                    if intro:
                        self._slack_client.post_message(channel_id, intro)
                except Exception:
                    logger.exception("Startup onboarding failed for #%s", channel_name)

    def _decline_and_leave(self, channel_id: str) -> None:
        """Send a not-onboarded message to a non-allowed channel and leave it."""
        # Skip DMs and group DMs — bot can't leave those
        if channel_id.startswith("D") or channel_id.startswith("G"):
            return

        # Dedup guard: avoid sending multiple decline messages if concurrent
        # events (member_joined + app_mention) fire for the same channel
        with self._leaving_lock:
            if channel_id in self._leaving_channels:
                return
            self._leaving_channels.add(channel_id)

        try:
            self._app.client.chat_postMessage(
                channel=channel_id,
                text=(
                    "I'm not onboarded in this channel. "
                    "Please contact your workspace admin to onboard me here."
                ),
            )
        except Exception:
            logger.warning("Could not send decline message to %s", channel_id, exc_info=True)
        try:
            self._app.client.conversations_leave(channel=channel_id)
            logger.info("Left non-allowed channel %s", channel_id)
        except Exception:
            logger.warning("Could not leave channel %s", channel_id, exc_info=True)
        finally:
            with self._leaving_lock:
                self._leaving_channels.discard(channel_id)

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
        if self._should_block_slack_tool_send(message.channel_id):
            logger.info("Suppressing Slack send to current channel during silent turn")
            return

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

    def before_tool_call(self, tool_name: str, tool_input: dict) -> str | None:
        if not self._is_slack_send_message_tool(tool_name):
            return None
        channel_id = tool_input.get("channel_id") or tool_input.get("channel")
        if not self._should_block_slack_tool_send(channel_id):
            return None
        logger.info("Suppressing Slack MCP send tool to %s during silent turn", channel_id)
        return self._suppressed_send_result(channel_id)

    def on_tool_start(self, tool_name: str, tool_input: dict) -> None:
        return

    def on_tool_result(self, tool_name: str, tool_input: dict, result: str) -> None:
        if not self._response_policy_enabled:
            return
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(parsed, dict):
            return

        response_policy = parsed.get("response_policy")
        has_generic_marker = (
            isinstance(response_policy, dict)
            and response_policy.get("suppress_final_response") is True
        )
        has_legacy_marker = parsed.get("do_not_mention") is True
        if has_generic_marker or has_legacy_marker:
            self._activate_response_policy_suppression()

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

    def _extract_images(self, event: dict) -> tuple[list[ImageAttachment], list[str]]:
        """Download image files and return (images, skipped_filenames).

        Slack event payloads (``app_mention``, Socket Mode ``message``) often
        omit the ``files`` array.  When ``files`` is missing we fall back to
        fetching the canonical message via the Slack API.  Thread replies use
        ``conversations.replies``; top-level messages use ``conversations.history``.
        """
        files = event.get("files")
        if files is None:
            channel = event.get("channel", "")
            ts = event.get("ts", "")
            thread_ts = event.get("thread_ts")
            if channel and ts:
                try:
                    if thread_ts:
                        # Thread reply — conversations.history can't see these
                        resp = self._app.client.conversations_replies(
                            channel=channel, ts=thread_ts, latest=ts, inclusive=True, limit=1,
                        )
                    else:
                        resp = self._app.client.conversations_history(
                            channel=channel, latest=ts, inclusive=True, limit=1,
                        )
                    msgs = resp.get("messages", [])
                    # conversations.replies may return multiple; find the exact one
                    for m in msgs:
                        if m.get("ts") == ts:
                            files = m.get("files", [])
                            break
                    if files is None and msgs:
                        files = msgs[0].get("files", [])
                except Exception:
                    logger.warning("Failed to fetch message for file extraction", exc_info=True)
            files = files or []
        if not files:
            return [], []
        raw = self._slack_client.download_images(files)
        images = [ImageAttachment(data=r["data"], media_type=r["media_type"]) for r in raw]
        # Collect filenames of non-image files that were skipped
        image_types = {"image/png", "image/jpeg", "image/gif", "image/webp"}
        skipped = [
            f.get("name", f.get("title", "unknown"))
            for f in files if f.get("mimetype", "") not in image_types
        ]
        return images, skipped


class SlackAdapter(SlackAdapterBase):
    """
    Slack adapter using the Bolt SDK with Socket Mode.

    Requires slack-bolt: pip install mithai[slack]
    """

    def __init__(self, bot_token: str, app_token: str, allowed_channels: list[str] | None = None,
                 approval_timeout: int = 300, respond: str = "all",
                 response_policy: dict | None = None):
        try:
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError:
            raise ImportError(
                "Slack adapter requires slack-bolt. Install with: pip install mithai[slack]"
            )

        if not app_token or app_token.startswith("${"):
            raise RuntimeError(
                "SLACK_APP_TOKEN is missing or not set.\n"
                "  1. Go to https://api.slack.com/apps → select your app\n"
                "  2. Basic Information → App-Level Tokens → generate/copy token\n"
                "  3. Add SLACK_APP_TOKEN=xapp-... to your .env file"
            )

        super().__init__(
            bot_token,
            allowed_channels,
            approval_timeout,
            respond=respond,
            response_policy=response_policy,
        )
        self._handler = SocketModeHandler(self._app, app_token)

    def start(self, on_message: MessageHandler, on_channel_join: ChannelJoinHandler | None = None,
              on_observe: ChannelObserveHandler | None = None,
              on_bot_reply: BotReplyHandler | None = None) -> None:
        if not self._allowed_channels:
            logger.warning(
                "No allowed_channels configured — Slack adapter will not connect to Socket Mode. "
                "Set allowed_channels in config.yaml to enable this agent. "
                "Skipping connection to avoid competing with other agents on the same token."
            )
            # Block indefinitely without opening a Socket Mode connection.
            # An unconfigured agent must not hold a WebSocket — Slack distributes
            # events across all connections sharing a token, so a competing
            # connection from an unconfigured agent steals ~50% of events.
            threading.Event().wait()
            return
        self._register_message_handlers(on_message, on_channel_join, on_observe, on_bot_reply)
        logger.info("Starting Slack adapter (Socket Mode)")
        self._handler.start()

    def stop(self) -> None:
        self._handler.close()
