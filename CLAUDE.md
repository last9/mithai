# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is mithai

An AI agent framework for infrastructure operations. It gives teams an AI-powered ops agent (backed by Claude) that lives in Slack, Telegram, and a terminal simultaneously. The agent uses **skills** (plugins) to take actions and **Human MCP** for human-in-the-loop approval of risky operations.

## Common Commands

```bash
# Install dependencies (uses uv)
uv pip install -e ".[dev]"

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_engine_images.py

# Run a single test by name
uv run pytest tests/test_tool_router.py -k "test_route_calls_handler"

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/

# Build native binary
make build-binary
```

## Architecture

### Core Loop

```
Adapter (Slack/Telegram/CLI) → Engine → LLM (Claude API)
                                  ↓            ↓
                              ToolRouter    Tool calls
                                  ↓       (skill__tool namespacing)
                              Skill handler or MCP server
                                  ↓
                              HumanMCP (approve/confirm/auto)
```

**Engine** (`src/mithai/core/engine.py`) — Central orchestrator. Builds system prompt from skill prompts, runs the LLM tool-use loop with Human MCP checks. Adapter-agnostic: the adapter is passed per-call so one engine can serve multiple adapters.

**ToolRouter** (`src/mithai/core/tool_router.py`) — Namespaces tools as `skill_name__tool_name` (double underscore separator). Routes LLM tool calls back to the correct skill handler or MCP server. Enforces `allowed_tools` for multi-agent mode.

**Adapters** (`src/mithai/adapters/`) — Platform-specific message transport. All extend `Adapter` base class. `IncomingMessage` and `OutgoingMessage` are platform-agnostic dataclasses.

**HumanMCP** (`src/mithai/human/mcp.py`) — Human-in-the-loop protocol. Tools declare their level: `None` (auto), `"approve"`, `"confirm"`, or `"dynamic"` (resolved at runtime by skill's `resolve_human()` function). Config overrides in `config.yaml` can escalate or de-escalate.

### Skills

Each skill is a folder under `skills/` with:
- `prompt.md` — system prompt fragment telling the LLM what the skill does
- `tools.py` — exports `TOOLS` list (tool definitions) and `handle(name, input, ctx)` function

Optional exports: `resolve_human()` for dynamic approval, `startup()` for initialization, `bind()` for adapter binding, `mcp_tools` list for MCP server delegation.

Skills are loaded dynamically by `skill_loader.py` via `importlib`.

### Config

`config.yaml` + `.env` — Parsed by `src/mithai/core/config.py` using Pydantic models. Environment variable substitution via `${VAR}` syntax in YAML values.

### Key Subsystems

- **Memory** (`src/mithai/memory/`) — Persistent memory backend (MEMORY.md file or other backends). Shared across channels.
- **State** (`src/mithai/state/`) — State backend for conversation/session persistence.
- **Session** (`src/mithai/core/session.py`) — Session management per channel.
- **MCP Manager** (`src/mithai/core/mcp_manager.py`) — Manages external MCP server connections for skills that delegate to them.
- **Telemetry** (`src/mithai/telemetry/`) — OpenTelemetry integration (optional).
- **UI** (`src/mithai/ui/`) — Web UI adapter (optional).

## Testing

Tests use `pytest` with `pytest-asyncio` (async mode = `"auto"`). Test fixtures for skills and config are in `tests/conftest.py`. Tests create temporary skill directories with `prompt.md` + `tools.py` to test the full skill loading pipeline.

## Documented Solutions

`docs/solutions/` — documented solutions to past problems (bugs, integration issues, patterns), organized by category with YAML frontmatter (`module`, `tags`, `problem_type`). Relevant when implementing or debugging in documented areas.

## Project Config

- Python 3.11+, managed with `uv`
- Build system: hatchling
- Linter/formatter: ruff (line-length 100, target py311)
- Package source: `src/mithai/` (src layout)
- CLI entry point: `mithai` → `src/mithai/cli/main.py:cli` (Click-based)
