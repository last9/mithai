# Security Policy

## Reporting Vulnerabilities

Please report security issues privately by emailing security@last9.io. Do not open a public issue for vulnerabilities, leaked credentials, or exploit details.

Include:

- Affected version or commit.
- Reproduction steps.
- Impact and affected configuration.
- Any relevant logs with secrets removed.

We aim to acknowledge reports within 3 business days.

## Supported Versions

Security fixes target the latest public release and `master`.

## Secret Handling

Mithai can connect to chat platforms, LLM providers, MCP servers, cloud APIs, and local shells. Treat `.env`, `.mithai/`, `.wrangler/`, `memory/`, state/session directories, kubeconfigs, and generated logs as sensitive. They must not be committed.

If you accidentally publish a secret, revoke it first, then remove it from git history before sharing the repository further.
