"""Post-turn reflection — extract learnings from interactions."""

import json
import logging
from datetime import date

from mithai.llm.base import LLMProvider
from mithai.memory.base import MemoryBackend

logger = logging.getLogger(__name__)

REFLECTION_PROMPT = """\
You are reviewing a bot interaction to extract learnings worth remembering.

Focus on:
- Infrastructure facts (resource types, names, relationships)
- Error patterns and how they were resolved
- Corrections (wrong assumption → right answer)
- Successful multi-step procedures

Be concise — one bullet per learning. If nothing new was learned, respond with exactly "none".
"""


def reflect(turn_data: dict, llm: LLMProvider, memory: MemoryBackend) -> None:
    """Extract learnings from a turn and append to daily log.

    Runs as a background task after the response is sent.
    """
    # Only reflect on turns with tool calls — pure text turns rarely teach anything
    if not turn_data.get("tool_calls"):
        return

    try:
        summary = {
            "user_message": turn_data.get("user_message", ""),
            "tool_calls": turn_data.get("tool_calls", []),
            "assistant_response": turn_data.get("assistant_response", "")[:500],
        }

        messages = [{"role": "user", "content": json.dumps(summary, indent=2)}]
        response = llm.create_message(
            system=REFLECTION_PROMPT,
            messages=messages,
            max_tokens=256,
        )

        text = ""
        for block in response.content:
            if block.get("type") == "text":
                text += block["text"]
        text = text.strip()

        if not text or text.lower() == "none":
            return

        path = f"daily/{date.today()}.md"
        timestamp = turn_data.get("timestamp", "")
        memory.write(path, f"\n### {timestamp}\n{text}\n", append=True)

        logger.debug("Reflection written to %s", path)

    except Exception:
        logger.debug("Reflection failed", exc_info=True)
