"""Session manager — conversation history per channel/thread."""

import logging
from datetime import datetime, timezone

from mithai.state.base import StateBackend

logger = logging.getLogger(__name__)

NAMESPACE = "sessions"


class SessionManager:
    """
    Manages conversation sessions scoped to platform + channel.

    Each session is an append-only log of turns (user message, tool calls,
    assistant response) stored via the StateBackend.
    """

    def __init__(self, state: StateBackend, max_turns: int = 50):
        self._state = state
        self._max_turns = max_turns

    @staticmethod
    def session_key(platform: str, channel_id: str, *, agent_id: str | None = None) -> str:
        if agent_id:
            return f"{platform}:{channel_id}:{agent_id}"
        return f"{platform}:{channel_id}"

    def load(self, key: str) -> dict:
        """Load a session or create an empty one."""
        session = self._state.get(NAMESPACE, key)
        if session is not None:
            return session

        now = datetime.now(timezone.utc).isoformat()
        parts = key.split(":", 1)
        return {
            "session_id": key,
            "platform": parts[0] if len(parts) > 1 else "",
            "channel_id": parts[1] if len(parts) > 1 else key,
            "created_at": now,
            "updated_at": now,
            "turns": [],
        }

    def append_turn(self, key: str, turn: dict) -> None:
        """Append a turn to the session and persist."""
        session = self.load(key)
        session["turns"].append(turn)

        # Trim oldest turns if over limit
        if len(session["turns"]) > self._max_turns:
            session["turns"] = session["turns"][-self._max_turns:]

        session["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._state.set(NAMESPACE, key, session)

    def delete(self, key: str) -> None:
        """Delete a session by key."""
        self._state.delete(NAMESPACE, key)

    def get_session(self, key: str) -> dict | None:
        """Get a session by key."""
        return self._state.get(NAMESPACE, key)

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """List sessions, most recently updated first."""
        keys = self._state.list_keys(NAMESPACE)
        sessions = []
        for key in keys:
            session = self._state.get(NAMESPACE, key)
            if session is None:
                continue
            turns = session.get("turns", [])
            last_message = turns[-1]["user_message"] if turns else ""
            sessions.append({
                "session_id": session.get("session_id", key),
                "platform": session.get("platform", ""),
                "channel_id": session.get("channel_id", ""),
                "updated_at": session.get("updated_at", ""),
                "turn_count": len(turns),
                "last_message": last_message[:100],
            })

        sessions.sort(key=lambda s: s["updated_at"], reverse=True)
        return sessions[:limit]

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search across sessions by keyword in messages and responses."""
        query_lower = query.lower()
        keys = self._state.list_keys(NAMESPACE)
        results = []

        for key in keys:
            session = self._state.get(NAMESPACE, key)
            if session is None:
                continue

            for turn in session.get("turns", []):
                text = (
                    turn.get("user_message", "")
                    + " "
                    + turn.get("assistant_response", "")
                )
                if query_lower in text.lower():
                    results.append({
                        "session_id": session.get("session_id", key),
                        "timestamp": turn.get("timestamp", ""),
                        "user_message": turn.get("user_message", ""),
                        "assistant_response": turn.get("assistant_response", "")[:200],
                    })
                    if len(results) >= limit:
                        return results

        return results

    @staticmethod
    def build_turn(
        user_id: str,
        user_message: str,
        tool_calls: list[dict],
        assistant_response: str,
    ) -> dict:
        """Build a turn dict from the components of a single interaction."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "user_message": user_message,
            "tool_calls": tool_calls,
            "assistant_response": assistant_response,
        }
