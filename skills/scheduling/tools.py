"""Skill: Scheduling — agent cloud platform API (preferred) or local crontab fallback."""

import json
import logging
import os
import re
import subprocess
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from pathlib import Path

_startup_logger = logging.getLogger(__name__)

_MARKER = "# mithai:"
_LABEL_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_EMPTY_SCHEDULES = json.dumps({"schedules": [], "message": "No scheduled tasks found"})

_DATA_DIR = str(Path.home() / ".mithai")
_TOKEN_PATH = os.path.join(_DATA_DIR, "secrets", "slack_schedule_token")
_PAYLOAD_DIR = os.path.join(_DATA_DIR, "schedules")

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_schedules",
        "description": "List current scheduled tasks created by this agent.",
        "input_schema": {"type": "object", "properties": {}},
        "human": None,
    },
    {
        "name": "create_schedule",
        "description": (
            "Create a scheduled task. The task fires on the cron schedule and "
            "the agent processes it automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cron_expression": {
                    "type": "string",
                    "description": (
                        "Cron expression with 5 fields: minute hour day month weekday. "
                        "Examples: '0 12 * * 1-5' (noon weekdays), '30 17 * * 1-5' (5:30 PM weekdays)"
                    ),
                },
                "task_text": {
                    "type": "string",
                    "description": "The task instruction (e.g. 'give an end of day summary')",
                },
                "label": {
                    "type": "string",
                    "description": "Short identifier for this schedule (e.g. 'eod-summary')",
                },
            },
            "required": ["cron_expression", "task_text", "label"],
        },
        "human": "confirm",
    },
    {
        "name": "delete_schedule",
        "description": "Remove a scheduled task by its label.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Label of the schedule to remove",
                },
            },
            "required": ["label"],
        },
        "human": "confirm",
    },
]

# ---------------------------------------------------------------------------
# Shared validation
# ---------------------------------------------------------------------------


def _validate_create_input(input: dict) -> str | None:
    """Return error JSON string if input is invalid, None if ok."""
    if not _LABEL_RE.match(input["label"]):
        return json.dumps(
            {"error": "Label must contain only letters, numbers, hyphens, and underscores"}
        )
    fields = input["cron_expression"].strip().split()
    if len(fields) != 5:
        return json.dumps({"error": f"Cron expression must have 5 fields, got {len(fields)}"})
    return None


# ---------------------------------------------------------------------------
# Backend ABC
# ---------------------------------------------------------------------------


class SchedulingBackend(ABC):
    def initialize(self) -> None:
        """Called by startup() after the backend is chosen. No-op by default."""

    @abstractmethod
    def list(self, ctx: dict) -> str:
        """Return JSON string with {"schedules": [...]} or {"error": ...}."""
        ...

    @abstractmethod
    def create(self, input: dict, ctx: dict) -> str:
        """Return JSON string with {"created": True, ...} or {"error": ...}."""
        ...

    @abstractmethod
    def delete(self, input: dict, ctx: dict) -> str:
        """Return JSON string with {"deleted": True, ...} or {"error": ...}."""
        ...


# ---------------------------------------------------------------------------
# Agent Cloud Platform backend
# ---------------------------------------------------------------------------


class AgentCloudPlatformBackend(SchedulingBackend):
    def __init__(self, url: str, token: str):
        self._url = url
        self._token = token

    def _request(self, method: str, path: str, body: dict | None = None, logger=None) -> dict:
        url = f"{self._url}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url, data=data,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 204:
                    return {}
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode() if e.fp else ""
            return {"error": f"API {e.code}: {body_text}"}
        except urllib.error.URLError as e:
            if logger:
                logger.error("Scheduler API unreachable (%s %s): %s", method, path, e)
            return {"error": f"API unreachable: {e.reason}"}
        except Exception as e:
            if logger:
                logger.exception("Unexpected API error (%s %s)", method, path)
            return {"error": f"API request failed: {e}"}

    def _list_raw(self, logger=None) -> list[dict] | dict:
        result = self._request("GET", "/v1/schedules", logger=logger)
        if "error" in result:
            return result
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "schedules" in result:
            return result["schedules"]
        return {"error": f"Unexpected API response: {list(result.keys())}"}

    def list(self, ctx: dict) -> str:
        schedules = self._list_raw(logger=ctx.get("logger"))
        if isinstance(schedules, dict) and "error" in schedules:
            return json.dumps(schedules)
        if not schedules:
            return _EMPTY_SCHEDULES
        return json.dumps({"schedules": [
            {"label": s.get("name", ""), "cron_expression": s.get("cron", ""),
             "paused": s.get("paused", False), "id": s.get("id", "")}
            for s in schedules
        ]})

    def create(self, input: dict, ctx: dict) -> str:
        if err := _validate_create_input(input):
            return err
        channel_id = ctx.get("channel_id", "")
        if not channel_id:
            return json.dumps({"error": "channel_id is required — run this from a Slack channel"})
        # Include the Slack posting instruction in the payload text so the
        # CLI agent spawned by the scheduler knows where to post its response.
        task_text = (
            f"{input['task_text']}\n\n"
            f"Post your response to Slack channel {channel_id} using slack__slack_send_message."
        )
        result = self._request("POST", "/v1/schedules", {
            "name": input["label"],
            "cron": input["cron_expression"],
            "payload": {"text": task_text, "channel_id": channel_id},
        }, logger=ctx.get("logger"))
        if "error" in result:
            return json.dumps(result)
        return json.dumps({
            "created": True, "label": input["label"],
            "cron_expression": input["cron_expression"], "task_text": input["task_text"],
            "message": f"Schedule '{input['label']}' created. Fires on: {input['cron_expression']}",
        })

    def delete(self, input: dict, ctx: dict) -> str:
        label = input["label"]
        # Agent cloud platform API requires an internal ID, not the user-visible name.
        # We must list all schedules to resolve label -> ID before deleting.
        schedules = self._list_raw(logger=ctx.get("logger"))
        if isinstance(schedules, dict) and "error" in schedules:
            return json.dumps(schedules)
        match = next((s for s in schedules if s.get("name") == label), None)
        if not match:
            return json.dumps({"error": f"No schedule found with label '{label}'"})
        result = self._request("DELETE", f"/v1/schedules/{match['id']}", logger=ctx.get("logger"))
        if "error" in result:
            return json.dumps(result)
        return json.dumps({"deleted": True, "label": label, "message": f"Schedule '{label}' removed."})


# ---------------------------------------------------------------------------
# Local crontab backend
# ---------------------------------------------------------------------------


class CrontabBackend(SchedulingBackend):
    def initialize(self) -> None:
        self._ensure_token_file()
        try:
            os.makedirs(_PAYLOAD_DIR, exist_ok=True)
            os.chmod(_PAYLOAD_DIR, 0o700)
        except OSError:
            pass

    def _ensure_token_file(self) -> str | None:
        # Prefer user token: messages posted with it trigger app_mention events.
        # Bot token posts are suppressed by Slack (loop prevention).
        token = os.environ.get("SLACK_USER_TOKEN") or os.environ.get("SLACK_BOT_TOKEN", "")
        if not token:
            return "SLACK_USER_TOKEN or SLACK_BOT_TOKEN not set in environment"
        try:
            os.makedirs(os.path.dirname(_TOKEN_PATH), exist_ok=True)
            with open(_TOKEN_PATH, "w") as f:
                f.write(token)
            os.chmod(_TOKEN_PATH, 0o600)
            return None
        except OSError as e:
            return f"Could not write token file: {e}"

    def _get_crontab(self) -> str:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        return result.stdout if result.returncode == 0 else ""

    def _set_crontab(self, content: str) -> dict:
        result = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
        if result.returncode != 0:
            return {"error": f"Failed to write crontab: {result.stderr.strip()}"}
        return {"ok": True}

    def _parse_entries(self, crontab: str) -> list[dict]:
        entries = []
        for line in crontab.splitlines():
            line = line.strip()
            if _MARKER not in line:
                continue
            parts = line.split(_MARKER, 1)
            cron_and_cmd = parts[0].strip()
            label = parts[1].strip() if len(parts) > 1 else ""
            fields = cron_and_cmd.split(None, 5)
            cron_expr = " ".join(fields[:5]) if len(fields) >= 5 else cron_and_cmd

            task_text = ""
            payload_file = os.path.join(_PAYLOAD_DIR, f"{label}.json")
            try:
                with open(payload_file) as pf:
                    text = json.load(pf).get("text", "")
                m = re.search(r"<@[^>]+>\s*(.*)", text)
                task_text = m.group(1).strip() if m else text
            except (OSError, json.JSONDecodeError):
                pass

            entries.append({"label": label, "cron_expression": cron_expr, "task_text": task_text})
        return entries

    def list(self, ctx: dict) -> str:
        entries = self._parse_entries(self._get_crontab())
        if not entries:
            return _EMPTY_SCHEDULES
        return json.dumps({"schedules": entries})

    def create(self, input: dict, ctx: dict) -> str:
        if err := _validate_create_input(input):
            return err

        cron_expr = input["cron_expression"]
        channel_id = ctx.get("channel_id", "")
        task_text = input["task_text"]
        label = input["label"]

        bot_user_id = os.environ.get("BOT_USER_ID", "")
        if not bot_user_id:
            return json.dumps({"error": "BOT_USER_ID not set in environment"})

        if token_err := self._ensure_token_file():
            return json.dumps({"error": token_err})

        crontab = self._get_crontab()
        if f"{_MARKER}{label}" in crontab:
            return json.dumps({"error": f"Schedule '{label}' already exists. Delete it first."})

        try:
            os.makedirs(_PAYLOAD_DIR, exist_ok=True)
        except OSError as e:
            return json.dumps({"error": f"Could not create payload dir: {e}"})

        payload_file = os.path.join(_PAYLOAD_DIR, f"{label}.json")
        with open(payload_file, "w") as f:
            json.dump({"channel": channel_id, "text": f"<@{bot_user_id}> {task_text}"}, f)
        os.chmod(payload_file, 0o600)

        curl_cmd = (
            f'bash -c \'curl -s -X POST https://slack.com/api/chat.postMessage'
            f' -H "Authorization: Bearer $(cat {_TOKEN_PATH})"'
            f' -H "Content-type: application/json"'
            f" -d @{payload_file}'"
        )
        cron_line = f"{cron_expr} {curl_cmd} {_MARKER}{label}"
        new_crontab = (crontab.rstrip("\n") + "\n" + cron_line + "\n") if crontab.strip() else cron_line + "\n"

        result = self._set_crontab(new_crontab)
        if "error" in result:
            return json.dumps(result)

        return json.dumps({
            "created": True, "label": label, "cron_expression": cron_expr,
            "task_text": task_text, "message": f"Schedule '{label}' created.",
        })

    def delete(self, input: dict, ctx: dict) -> str:
        label = input["label"]
        crontab = self._get_crontab()
        marker = f"{_MARKER}{label}"
        lines = crontab.splitlines()
        new_lines = [line for line in lines if marker not in line]

        if len(new_lines) == len(lines):
            return json.dumps({"error": f"No schedule found with label '{label}'"})

        new_crontab = "\n".join(new_lines) + "\n" if new_lines else ""
        result = self._set_crontab(new_crontab)
        if "error" in result:
            return json.dumps(result)

        try:
            os.remove(os.path.join(_PAYLOAD_DIR, f"{label}.json"))
        except OSError:
            pass

        return json.dumps({"deleted": True, "label": label, "message": f"Schedule '{label}' removed."})


# ---------------------------------------------------------------------------
# Active backend (set by startup)
# ---------------------------------------------------------------------------

_backend: SchedulingBackend = CrontabBackend()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def startup(config):
    """Select backend from config and initialize."""
    global _backend

    backend_type = config.get("backend", "crontab")

    if backend_type == "agent_cloud_platform":
        url = config.get("scheduling_backend_url", "")
        token = config.get("scheduling_backend_token", "")
        if not url or not token:
            _startup_logger.warning(
                "Scheduling: backend=agent_cloud_platform but scheduling_backend_url/scheduling_backend_token missing — falling back to crontab"
            )
            _backend = CrontabBackend()
        else:
            _backend = AgentCloudPlatformBackend(url, token)
            _startup_logger.info("Scheduling: agent cloud platform backend (%s)", url)
    else:
        _backend = CrontabBackend()
        _startup_logger.info("Scheduling: local crontab backend")

    _backend.initialize()


def handle(name: str, input: dict, ctx: dict) -> str:
    if name == "list_schedules":
        return _backend.list(ctx)
    if name == "create_schedule":
        return _backend.create(input, ctx)
    if name == "delete_schedule":
        return _backend.delete(input, ctx)
    return json.dumps({"error": f"Unknown tool: {name}"})
