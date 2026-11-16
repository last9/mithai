# Changelog

All notable public changes will be documented here.

## 0.14.0

- Add a configurable OpenAI-compatible chat-completions provider for OpenAI,
  xAI, and compatible endpoints, with tool calls, usage telemetry, retries, and
  redacted typed errors.

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

## 0.12.0

- Prepare Mithai for open-source publication.
- Sanitize public history and remove private infrastructure defaults.
- Add community health files and public-safe release defaults.
