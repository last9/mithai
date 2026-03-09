You can interact with GitHub repositories using the `gh` CLI.

## Capabilities
- **Pull Requests** — list, view details, create, and merge PRs
- **Issues** — list, view details, create issues, and add comments
- **Actions** — list workflow runs, view run details, re-run failed jobs
- **Releases** — list releases and create new ones
- **Repository** — view repo info and list branches

## Conventions
- The `repo` parameter is always in `owner/name` format (e.g., `acme/webapp`).
- The `gh` CLI must be authenticated (`gh auth status` to verify).
- Read-only tools execute without approval. Mutating tools (create, merge, comment, rerun, release) require human approval.
- When a workflow run fails, highlight the failed jobs and suggest checking logs or re-running.
- When listing PRs or issues, default to open state unless the user asks otherwise.
