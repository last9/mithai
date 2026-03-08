# Exception Fixer

Investigate exceptions from Last9 and fix them by opening pull requests on GitHub.

## Workflow

1. Use `get_exceptions` and `get_service_traces` to find the exception and its stack trace.
2. Extract the repo, file path, and line number from the stack trace. Use `search_code` if unclear.
3. Read `CLAUDE.md`, `AGENTS.md`, or `.claude/CLAUDE.md` from the repo root for coding conventions.
4. Read the source file(s) and any related test files.
5. Explain the root cause and proposed fix to the user before writing code.
6. Create a `fix/<short-description>` branch, push the fix, and check CI via `get_pull_request_status`.
7. If CI fails, read the failure, fix, and push again (max 3 attempts).
8. Call `format_pr_body` to generate the PR description, then pass its output as the body to `create_pull_request`.

Keep changes minimal. Update tests if needed. Works for any language — infer from file extensions.
Always use `format_pr_body` for the PR body — never write it manually.
