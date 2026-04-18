"""Post-turn response verifier.

Runs a cheap secondary LLM call to check that the agent's response
does not contradict what the tools actually returned.

Only runs when at least one "verified" skill (e.g. aws, kubernetes) was
called during the turn — deterministic skills (shell, memory) are skipped.
"""

import logging

from mithai.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a fact-checker. Compare the agent's response against tool results. "
    "Only flag clear numerical or factual contradictions — not style, phrasing, or omissions. "
    "Reply with exactly one line: PASS  or  FAIL: <brief reason>"
)


def verify(response_text: str, tool_calls: list[dict], llm: LLMProvider) -> str | None:
    """Check response_text against tool_calls for factual contradictions.

    Returns a failure description string if a contradiction is found,
    or None if the response passes (or no tool calls to check against).
    """
    if not tool_calls:
        return None

    # Skip entries without result_summary — error/denied/unknown-tool entries
    # have no factual content to check against.
    verifiable = [t for t in tool_calls if t.get("result_summary")]
    if not verifiable:
        return None

    results_block = "\n".join(
        f"- {t['tool']}: {t['result_summary']}" for t in verifiable
    )
    prompt = (
        f"Tool results:\n{results_block}\n\n"
        f"Agent response:\n{response_text}\n\n"
        "Does the response contradict any tool result? Reply PASS or FAIL: <reason>"
    )

    try:
        resp = llm.create_message(
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
        )
    except Exception:
        logger.warning("Verifier LLM call failed", exc_info=True)
        return None

    if not resp.content:
        return None

    text = resp.content[0].get("text", "").strip()
    if text.upper().startswith("PASS"):
        return None

    return text.removeprefix("FAIL:").strip()


def verified_skills_called(tool_calls: list[dict], verified_skills: set[str]) -> bool:
    """Return True if any tool call belongs to a verified skill."""
    if not tool_calls or not verified_skills:
        return False
    return any(
        t["tool"].split("__")[0] in verified_skills
        for t in tool_calls
    )
