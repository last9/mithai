"""Per-turn budgets for expensive MCP log tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

DEFAULT_LOG_TOOL_NAMES = frozenset({"get_logs", "get_service_logs"})
DEFAULT_MAX_LOG_TOOLS_PER_TURN = 5
DEFAULT_LONG_RANGE_THRESHOLD_MINUTES = 60


def get_tool_budget_config(config: dict) -> dict:
    """Return normalized tool-budget settings."""
    raw = config.get("tool_budgets") or {}
    log_tools = raw.get("log_tools") or {}
    tool_names = log_tools.get("tools")
    if tool_names is None:
        names = DEFAULT_LOG_TOOL_NAMES
    else:
        names = frozenset(str(name) for name in tool_names)

    return {
        "enabled": raw.get("enabled", True),
        "log_tools": {
            "max_per_turn": int(log_tools.get("max_per_turn", DEFAULT_MAX_LOG_TOOLS_PER_TURN)),
            "max_long_range_per_turn": int(log_tools.get("max_long_range_per_turn", 1)),
            "long_range_threshold_minutes": int(
                log_tools.get("long_range_threshold_minutes", DEFAULT_LONG_RANGE_THRESHOLD_MINUTES)
            ),
            "tools": names,
        },
    }


def is_budgeted_log_tool(prefixed_name: str, tool_names: frozenset[str]) -> bool:
    """Return True when the tool suffix matches a configured log tool."""
    return prefixed_name.rsplit("__", 1)[-1] in tool_names


def count_budgeted_log_tools(turn_tool_calls: list[dict], tool_names: frozenset[str]) -> int:
    return sum(
        1
        for call in turn_tool_calls
        if is_budgeted_log_tool(call.get("tool", ""), tool_names)
    )


def count_long_range_log_tools(
    turn_tool_calls: list[dict],
    tool_names: frozenset[str],
    threshold_minutes: int,
) -> int:
    return sum(
        1
        for call in turn_tool_calls
        if is_budgeted_log_tool(call.get("tool", ""), tool_names)
        and is_long_range_log_query(call.get("input"), threshold_minutes)
    )


def _parse_iso_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_long_range_log_query(tool_input: dict[str, Any] | None, threshold_minutes: int) -> bool:
    """Detect log queries that span more than the configured threshold."""
    if not tool_input:
        return False

    lookback = tool_input.get("lookback_minutes")
    if lookback is not None:
        try:
            return float(lookback) > threshold_minutes
        except (TypeError, ValueError):
            return False

    start = _parse_iso_timestamp(str(tool_input.get("start_time_iso") or ""))
    end = _parse_iso_timestamp(str(tool_input.get("end_time_iso") or ""))
    if start and end:
        minutes = (end - start).total_seconds() / 60
        return minutes > threshold_minutes

    # get_service_logs defaults to a 60-minute lookback when no range is set.
    if tool_input.get("service_name") and not start and lookback is None:
        return threshold_minutes <= 60

    return False


def budget_exhausted_result(reason: str, **extra: Any) -> str:
    payload = {
        "budget_exhausted": True,
        "reason": reason,
        **extra,
    }
    return json.dumps(payload)
