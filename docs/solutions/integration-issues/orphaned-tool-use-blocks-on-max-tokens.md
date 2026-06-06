---
title: "Orphaned tool_use blocks on max_tokens truncation crash the engine tool loop"
date: 2026-06-06
category: integration-issues
module: core/engine
problem_type: integration_issue
component: assistant
symptoms:
  - "Anthropic BadRequestError 400: tool_use ids were found without tool_result blocks immediately after"
  - "Long silent gap (~96s) in logs between last executed tool and the failure"
  - "A requested tool (memory_write) never logged as executed"
  - "Slack channel onboarding fails with a traceback ending in the nudge create_message call"
root_cause: missing_workflow_step
resolution_type: code_fix
severity: high
tags: [anthropic-api, tool-use, max-tokens, stop-reason, error-recovery, onboarding, llm-engine]
---

# Orphaned tool_use blocks on max_tokens truncation crash the engine tool loop

## Problem

The engine's tool loop assumed `stop_reason == "tool_use"` is the only way a response can carry `tool_use` blocks. When Anthropic truncates generation mid-tool_use (`stop_reason == "max_tokens"`), the loop exited without answering those blocks, leaving an orphaned `tool_use` as the last assistant message — and the very next API call failed with a 400.

## Symptoms

- `BadRequestError: 400 ... 'messages.N: tool_use ids were found without tool_result blocks immediately after: toolu_...'` on a follow-up call (here: the silent-response nudge in `_handle_inner`)
- ~96 seconds of log silence before the error — the wall-clock time of generating right up to the 4096 `max_tokens` cap
- The truncated tool call (`memory__memory_write`) never appears in "Executing tool" logs
- Trigger: Slack channel onboarding, whose prompt forbids text output and requests a full MEMORY.md overwrite in one `memory_write` call

## What Didn't Work

- **"`_build_history` session replay produced the orphan"** — ruled out: replay uses synthetic `hist_{turn}_{idx}` ids, but the orphan was a real `toolu_*` id, so it came from a live response in the current run.
- **"The LLM client prunes or reorders messages"** — ruled out by reading `anthropic.py` `_call_api`: messages are sent verbatim.
- **"Deployed code differs from local"** — ruled out: every traceback line number matched local source exactly (`handle:194`, `_handle_inner:427`, `create_message:48`, `_call_api:136`), which also pinpointed the failing call as the nudge.

## Solution

Drive the loop off the *presence* of `tool_use` blocks, not the stop_reason label, and close orphans before any further API call (`src/mithai/core/engine.py`):

```python
# before
while response.stop_reason == "tool_use":

# after
while response.stop_reason == "tool_use" or any(
    b["type"] == "tool_use" for b in response.content
):
    if response.stop_reason != "tool_use":
        # max_tokens cut generation mid-tool_use — answer each orphan with a
        # synthetic error tool_result, then re-call so the model can recover
        orphan_ids = [b["id"] for b in response.content if b["type"] == "tool_use"]
        # (recovery-budget cap of 2 + strip-and-bail fallback omitted — see engine.py)
        messages.append({"role": "user", "content": [
            LLMProvider.format_tool_result(oid, json.dumps({
                "error": "This tool call did not execute: the response was "
                         f"cut off before it completed (stop_reason="
                         f"{response.stop_reason}). Retry with a smaller "
                         "input, e.g. split the work into chunks.",
            }))
            for oid in orphan_ids
        ]})
        response = self._llm.create_message(...)  # recovery synthesis call
        messages.append({"role": "assistant", "content": response.content})
        continue
```

Regression tests in `tests/test_nudge.py` (`TestMaxTokensTruncatedToolUse`) encode the API contract in a reusable helper:

```python
def _assert_tool_pairing_valid(messages):
    """Every tool_use id in an assistant message must have a matching
    tool_result in the immediately following message."""
```

The reproduction test failed pre-fix with the exact production error shape.

## Why This Works

The Anthropic Messages API requires every `tool_use` block in an assistant message to be answered by a matching `tool_result` in the immediately following user message. `max_tokens` truncation leaves a (possibly incomplete) `tool_use` block in `content` that the loop never executes, so it is never answered. Because the onboarding prompt suppresses text output, the extracted response text was empty and the engine took the nudge path — appending a plain user message after the dangling assistant message, which violates the pairing invariant. The fix preserves the invariant unconditionally: orphans are either answered with synthetic error results (letting the model retry in smaller chunks) or stripped after the recovery budget is spent, so the array can never be left invalid.

## Prevention

- Any LLM tool loop must handle **every** stop_reason that can carry `tool_use` blocks — key off block presence in `content`, not the stop_reason label.
- Close orphaned `tool_use` ids with an explanatory synthetic `tool_result` before any further API call; cap recovery attempts and keep a strip-and-continue fallback so the array can never be left invalid.
- Run `_assert_tool_pairing_valid` against the messages of every mocked API call in tool-loop tests — it catches invalid sequences before they reach the wire.
- Prompt-design caution: avoid demanding one giant single tool call (e.g. "rewrite the entire MEMORY.md in one `memory_write`"); instruct the model to chunk large writes, and size `max_tokens` against the largest legitimate single tool payload.

## Related Issues

- Fix PR: last9/mithai#90
- Related onboarding hardening (same flow, different root causes): last9/mithai#56 (minimal phase-2 system prompt), last9/mithai#57 (concurrent onboarding lock)
