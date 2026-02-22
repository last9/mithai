"""Build context dict for skill tool handlers."""

import logging

from mithai.state.base import StateBackend


def build_context(
    state: StateBackend,
    channel_id: str,
    user_id: str,
    skill_config: dict | None = None,
) -> dict:
    """
    Build the ctx dict passed to every skill handler.

    ctx = {
        "state": StateBackend,     # Persistent key-value store
        "channel_id": str,         # Where the message came from
        "user_id": str,            # Who sent it
        "config": dict,            # Skill-specific config from config.yaml
        "logger": Logger,          # Named logger
    }
    """
    return {
        "state": state,
        "channel_id": channel_id,
        "user_id": user_id,
        "config": skill_config or {},
        "logger": logging.getLogger("mithai.skill"),
    }
