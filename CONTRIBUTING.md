# Contributing

Thanks for helping improve Mithai. Keep changes focused, include tests for behavior changes, and document user-facing configuration changes.

## Development Setup

```bash
uv sync --all-extras
uv run pytest tests/ -v
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

For docs (Node 22.23.4 or later 22.x, pinned in `docs-site/.nvmrc`):

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

- `SKILL.md` for the system prompt fragment.
- `tools.py` exporting tool definitions and handlers.

Risky tools should use Human MCP approval levels and document operational impact.

## Releasing to PyPI

Keep the version in `pyproject.toml` and the `mithai` entry in `uv.lock` aligned,
and describe user-facing changes in `CHANGELOG.md`. Version 0.13.0 includes
`SKILL.md` loading and prompt-only skills; 0.12.0 on PyPI predates that support.

Before tagging, build and check the distributions:

```bash
uv build
uvx twine check dist/*
```

Install the wheel into a fresh virtual environment outside the checkout and
verify `mithai --version`, a `SKILL.md` skill with native tools, and a prompt-only
skill without `tools.py`. Repeat with the sdist to check both published formats.
Use the skill-loader tests in `tests/test_skill_loader.py` as the behavior contract.

After the release PR merges and CI passes, a maintainer must push a matching tag
(for this release, `v0.13.0`) on the merged commit. The existing Release workflow
checks the tag against `pyproject.toml`, tests the package, and publishes the
wheel and sdist through the `pypi` environment's trusted publisher. A manual
workflow run is a dry run; merging the PR alone does not publish to PyPI.

Confirm the new version can be installed from PyPI in a fresh environment and
repeat the skill-loading checks before closing the release issue.
