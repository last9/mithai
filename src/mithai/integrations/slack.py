"""SlackClient — low-level Slack Web API access for integrations and skills."""

import base64
import logging
import re

logger = logging.getLogger(__name__)


class SlackClient:
    """Thin wrapper around slack_sdk.WebClient for Slack API calls.

    Used by both SlackAdapterBase (internally) and skills (via adapter.slack_client).
    """

    def __init__(self, bot_token: str):
        from slack_sdk import WebClient
        self._token = bot_token
        self._client = WebClient(token=bot_token)

    def get_history(self, channel_id: str, limit: int) -> tuple[list[str], dict[str, str]]:
        """
        Fetch recent messages from a channel.

        Returns (formatted_messages, user_id_to_name_map).
        User IDs in messages are replaced with real display names.
        """
        try:
            resp = self._client.conversations_history(channel=channel_id, limit=limit)
        except Exception as exc:
            logger.warning("Failed to fetch history for channel %s: %s", channel_id, exc)
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

        user_map = self.resolve_user_ids(all_user_ids)

        def _replace_mentions(text: str) -> str:
            return re.sub(
                r"<@([A-Z0-9]+)>",
                lambda m: f"@{user_map.get(m.group(1), m.group(1))}",
                text,
            )

        formatted = []
        for msg in reversed(raw_messages):  # oldest first
            uid = msg.get("user")
            if not uid:
                continue  # skip bot/system messages with no user field
            name = user_map.get(uid, uid)
            text = _replace_mentions(msg.get("text", "")).strip()
            if text:
                formatted.append(f"{name}: {text}")

        return formatted, user_map

    @staticmethod
    def _is_valid_slack_ts(ts: str) -> bool:
        """Slack timestamps are Unix epoch floats like '1234567890.123456'."""
        try:
            float(ts)
            return "." in ts
        except (ValueError, TypeError):
            return False

    def post_message(self, channel_id: str, text: str, thread_ts: str | None = None) -> dict:
        """Post a message to a Slack channel or thread."""
        kwargs: dict = {"channel": channel_id, "text": text}
        if thread_ts:
            if self._is_valid_slack_ts(thread_ts):
                kwargs["thread_ts"] = thread_ts
            else:
                logger.warning("Ignoring invalid thread_ts %r — must be a Slack timestamp float", thread_ts)
        try:
            resp = self._client.chat_postMessage(**kwargs)
            return {"ok": resp.get("ok", False), "ts": resp.get("ts", ""), "channel": channel_id}
        except Exception:
            logger.warning("Failed to post message to %s", channel_id, exc_info=True)
            return {"ok": False, "error": "post_message failed", "channel": channel_id}

    def get_thread_replies(self, channel_id: str, thread_ts: str, limit: int = 100) -> list[str]:
        """Fetch messages in a thread, oldest-first, formatted as 'name: text'.

        Returns [] on error or when the API returns not-ok.
        """
        if not self._is_valid_slack_ts(thread_ts):
            logger.warning("Ignoring invalid thread_ts %r for get_thread_replies", thread_ts)
            return []
        try:
            resp = self._client.conversations_replies(channel=channel_id, ts=thread_ts, limit=limit)
        except Exception:
            logger.warning("Failed to fetch thread replies for %s/%s", channel_id, thread_ts, exc_info=True)
            return []

        if not resp.get("ok"):
            logger.warning("conversations_replies error: %s", resp.get("error"))
            return []

        messages = resp.get("messages", [])
        all_user_ids: set[str] = {msg["user"] for msg in messages if msg.get("user")}
        user_map = self.resolve_user_ids(all_user_ids)

        lines = []
        for msg in messages:  # already oldest-first from Slack
            uid = msg.get("user", "unknown")
            name = user_map.get(uid, uid).lower()
            text = msg.get("text", "").strip()
            if text:
                lines.append(f"{name}: {text}")
        return lines

    def get_members(self, channel_id: str) -> list[dict]:
        """Fetch all members of a channel with their display names.

        Returns a list of {id, name} dicts, sorted by name.
        Handles pagination automatically.
        """
        user_ids: set[str] = set()
        cursor = None
        try:
            while True:
                kwargs: dict = {"channel": channel_id, "limit": 200}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = self._client.conversations_members(**kwargs)
                if not resp.get("ok"):
                    logger.warning("conversations_members error for %s: %s", channel_id, resp.get("error"))
                    break
                user_ids.update(resp.get("members", []))
                cursor = resp.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
        except Exception as exc:
            logger.warning("Failed to fetch members for channel %s: %s", channel_id, exc)

        user_map = self.resolve_user_ids(user_ids)
        return sorted(
            [{"id": uid, "name": name} for uid, name in user_map.items()],
            key=lambda m: m["name"].lower(),
        )

    # Slack image MIME types accepted by Claude's vision API
    _SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

    def download_images(self, files: list[dict]) -> list[dict]:
        """Download image files from a Slack message and return base64-encoded dicts.

        Each returned dict has keys: data (base64 str), media_type (str).
        Non-image files and unsupported types are silently skipped.
        """
        import urllib.request

        results = []
        for f in files:
            mimetype = f.get("mimetype", "")
            if mimetype not in self._SUPPORTED_IMAGE_TYPES:
                continue
            url = f.get("url_private")
            if not url:
                continue
            try:
                req = urllib.request.Request(
                    url,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read()
                encoded = base64.b64encode(raw).decode("ascii")
                logger.info("Downloaded image %s (%s, %d bytes)", mimetype, url[:80], len(raw))
                results.append({
                    "data": encoded,
                    "media_type": mimetype,
                })
            except Exception as exc:
                logger.warning("Failed to download Slack image %s: %s", url, exc)
        return results

    def resolve_user_ids(self, user_ids: set[str]) -> dict[str, str]:
        """Return a map of user_id -> display_name for the given set of IDs."""
        result = {}
        for uid in user_ids:
            try:
                resp = self._client.users_info(user=uid)
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
