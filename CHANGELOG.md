# Changelog

All notable public changes will be documented here.

## 0.13.1

- Restrict Telegram access to configured chats and protect approval decisions.
- Require authentication for the embedded API and protect learned approval history.
- Show Telegram approval outcomes and retry transient polling failures.
- Export telemetry from `mithai chat`, including the optional logs bridge, and
  drain traces, metrics, and logs on exit, EOF, or errors.
- Honor OTLP endpoint and header environment variables when YAML settings are
  omitted, and document sending chat traces to Last9.

## 0.13.0

- Load skills from `SKILL.md` (or `skill.md`) and strip leading YAML frontmatter
  from their prompt bodies.
- Support prompt-only skills without `tools.py`.
- Migrate existing skills from `prompt.md` to `SKILL.md`; `prompt.md` is no
  longer loaded as a skill prompt.

## 0.15.0

- OpenAI-compatible provider: optional Responses API (`api: responses`) so reasoning models can
  call function tools; reasoning items are replayed statelessly across turns.
- Optional `reasoning_effort` for both OpenAI APIs; usage reports cached input and reasoning tokens
  when the endpoint returns them.

## 0.14.0

- OpenAI-compatible provider for OpenAI and xAI (Chat Completions) with classified availability
  errors.

## 0.12.0

- Prepare Mithai for open-source publication.
- Sanitize public history and remove private infrastructure defaults.
- Add community health files and public-safe release defaults.
