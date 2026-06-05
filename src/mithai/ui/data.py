"""ControlRoomData — read-only data access layer for the Control Room UI."""

import re

from mithai.core.config import get_skill_paths
from mithai.core.session import SessionManager
from mithai.core.skill_loader import load_skills
from mithai.memory.base import MemoryBackend
from mithai.state.base import StateBackend


class ControlRoomData:
    """Thin read-only wrapper over StateBackend + MemoryBackend.

    No new storage — just queries existing data through the same
    backends the engine writes to.
    """

    def __init__(
        self,
        state: StateBackend,
        memory: MemoryBackend | None,
        config: dict,
    ):
        self._state = state
        self._memory = memory
        self._config = config
        self._sessions = SessionManager(state)

    # ── Sessions ──

    def list_sessions(self, limit: int = 50) -> list[dict]:
        return self._sessions.list_sessions(limit=limit)

    def get_session(self, key: str) -> dict | None:
        return self._sessions.get_session(key)

    def search_sessions(self, query: str, limit: int = 20) -> list[dict]:
        return self._sessions.search(query, limit=limit)

    def get_session_stats(self) -> dict:
        keys = self._state.list_keys("sessions")
        by_platform: dict[str, int] = {}
        total_turns = 0
        for key in keys:
            session = self._state.get("sessions", key)
            if session is None:
                continue
            platform = session.get("platform", "unknown")
            by_platform[platform] = by_platform.get(platform, 0) + 1
            total_turns += len(session.get("turns", []))
        total = len(keys)
        return {
            "total": total,
            "by_platform": by_platform,
            "total_turns": total_turns,
            "avg_turns": round(total_turns / total, 1) if total > 0 else 0,
        }

    # ── Approvals ──

    def get_approvals(self) -> dict:
        if not self._memory:
            return {}
        return self._memory.read_json("approvals.json") or {}

    def get_approval_stats(self) -> dict:
        approvals = self.get_approvals()
        threshold = self._config.get("learning", {}).get("approval_auto_promote", 3)
        total_approved = 0
        total_denied = 0
        auto_promoted = 0

        for tool_data in approvals.values():
            for counts in tool_data.values():
                a = counts.get("approved", 0)
                d = counts.get("denied", 0)
                total_approved += a
                total_denied += d
                if a >= threshold and d == 0:
                    auto_promoted += 1

        return {
            "total_approved": total_approved,
            "total_denied": total_denied,
            "auto_promoted_count": auto_promoted,
            "threshold": threshold,
        }

    # ── Memory ──

    def list_memory_files(self) -> list[str]:
        if not self._memory:
            return []
        return self._memory.list_files(glob="**/*")

    def read_memory_file(self, path: str) -> str | None:
        if not self._memory:
            return None
        return self._memory.read(path)

    def write_memory_file(self, path: str, content: str) -> bool:
        """Create or overwrite a memory file. Returns False for an invalid path
        (traversal/absolute) or when no memory backend is configured."""
        if not self._memory or not self._memory.validate_path(path):
            return False
        self._memory.write(path, content)
        return True

    def delete_memory_file(self, path: str) -> bool:
        """Delete a memory file. Returns False for an invalid path / no backend,
        and False if the file did not exist."""
        if not self._memory or not self._memory.validate_path(path):
            return False
        return self._memory.delete(path)

    def search_memory(self, query: str) -> list[dict]:
        if not self._memory:
            return []
        results = self._memory.search(query)
        return [
            {
                "file": r.path,
                "matches": [{"line": m.line, "text": m.text} for m in r.matches],
            }
            for r in results
        ]

    # ── Skills ──

    def list_skills(self) -> list[dict]:
        skill_paths = get_skill_paths(self._config)
        skills = load_skills(skill_paths)
        return [
            {
                "name": name,
                "tool_count": len(skill.tools),
                "tools": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "human": t.human or "none",
                        "input_schema": t.input_schema,
                    }
                    for t in skill.tools
                ],
            }
            for name, skill in sorted(skills.items())
        ]

    # ── Config ──

    def get_config(self) -> dict:
        return _redact_secrets(self._config)

    def get_config_path(self) -> str:
        return "config.yaml"


_SECRET_PATTERNS = re.compile(
    r"(api_key|token|secret|password|credential)", re.IGNORECASE
)


def _redact_secrets(obj, parent_key: str = ""):
    """Recursively redact values that look like secrets."""
    if isinstance(obj, dict):
        return {k: _redact_secrets(v, parent_key=k) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_redact_secrets(item, parent_key=parent_key) for item in obj]
    elif isinstance(obj, str):
        # Redact if parent key matches secret patterns
        if _SECRET_PATTERNS.search(parent_key):
            return "***REDACTED***"
        # Redact unresolved env vars
        if obj.startswith("${") and obj.endswith("}"):
            return "***REDACTED***"
        return obj
    return obj
