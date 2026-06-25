# Contributing

Thanks for helping improve Mithai. Keep changes focused, include tests for behavior changes, and document user-facing configuration changes.

## Development Setup

```bash
uv sync --all-extras
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

For docs:

```bash
python3 docs-site/migrate.py
cd docs-site
npm ci --legacy-peer-deps
npm run build
```

## Pull Requests

- Open an issue first for large features or behavior changes.
- Keep PRs small enough to review.
- Add or update tests for changed behavior.
- Update README/docs when configuration, CLI behavior, skills, adapters, or security posture changes.
- Do not commit runtime state, secrets, tokens, `.env`, `.mithai/`, `.wrangler/`, or `memory/`.

## Adding Skills

Skills live under `skills/<name>/` and normally contain:

- `prompt.md` for the system prompt fragment.
- `tools.py` exporting tool definitions and handlers.

Risky tools should use Human MCP approval levels and document operational impact.
