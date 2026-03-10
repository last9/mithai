# Mithai Binary Installer — Specification

**Status:** Draft
**Date:** 2026-03-09
**Author:** Prathamesh + Claude Code

---

## 1. Overview

Ship Mithai as a self-contained native binary that can be installed with a single
command. The binary bundles the Python runtime and core dependencies, provides an
interactive setup wizard (`mithai init`), and supports a modular skill system where
non-core skills are installed on demand.

### Goals

- **One-command install**: `curl -fsSL https://get.mithai.dev | sh`
- **Interactive setup**: `mithai init` configures adapters, LLM, k8s clusters, and MCP servers — validates all connections before finishing
- **Modular skills**: Core skills ship in the binary; optional skills installed via `mithai skill install <name>`
- **Multi-cluster k8s**: Standard KUBECONFIG merging (colon-separated paths) for multi-cluster access
- **Service management**: `mithai service install/start/stop/status` for persistent daemon deployment
- **Cross-platform**: macOS (arm64, amd64) + Linux (amd64, arm64) + Homebrew tap

### Non-Goals

- Auto-update (users manage versions manually)
- Docker-only distribution (Docker image may come later but is not the primary distribution)
- Plugin marketplace / remote skill registry (skills are local directories or installed from known sources)
- Windows support
- Non-interactive init mode (future scope — interactive only for v0.2.0)
- Config migration between versions (future scope)
- Last9-specific positioning (Mithai is a generic framework; Last9 is one integration among many)

### Positioning

Mithai is an **open-source, generic AI agent framework for infrastructure operations**.
It is not tied to any specific observability vendor, cloud provider, or LLM. Last9,
AWS, GitHub, etc. are integrations — not the core identity. The binary should feel
like installing `kubectl` or `gh` — a standalone tool that works with your existing stack.

---

## 2. Target Audience

| Persona | Use case |
|---|---|
| **SRE/DevOps** | Quick install on jump boxes, bastion hosts. Connect to multiple k8s clusters. Run as a systemd service |
| **Developers** | Local install alongside Claude Code. Use `mithai chat` for infra queries while coding |
| **Platform teams** | Deploy as a shared service. `mithai init` with team config, run as launchd/systemd service |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────┐
│  curl installer (shell script)                  │
│  - Detects OS/arch                              │
│  - Downloads binary from GitHub Releases        │
│  - Installs to /usr/local/bin or ~/.local/bin   │
│  - Prints "Run mithai init to get started"      │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  mithai binary (PyInstaller onefile)            │
│  ┌────────────────────────────────────────────┐ │
│  │ Bundled Python 3.13 runtime                │ │
│  │ Core deps: anthropic, click, pyyaml,       │ │
│  │   python-dotenv, jinja2                    │ │
│  │ Adapter deps: slack-bolt, requests         │ │
│  │ MCP deps: mcp SDK                          │ │
│  ├────────────────────────────────────────────┤ │
│  │ Core skills (bundled):                     │ │
│  │   shell, memory, sessions, http_checker    │ │
│  ├────────────────────────────────────────────┤ │
│  │ CLI commands:                              │ │
│  │   init, run, chat, skill, service, doctor  │ │
│  └────────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  Optional skills (installed to ~/.mithai/skills) │
│  kubernetes, aws, last9, github,                │
│  exception_fixer, cicd                          │
│  Each skill can declare runtime deps            │
│  (e.g., github skill needs npx/node)            │
└─────────────────────────────────────────────────┘
```

### 3.1 Binary Packaging

**Tool:** PyInstaller in `--onefile` mode

- Bundles Python 3.13 interpreter + all core dependencies into a single executable
- Uses PyInstaller's `--collect-all` for packages with dynamic imports (anthropic, mcp)
- `.spec` file committed to repo for reproducible builds
- Target: works on glibc 2.31+ (Ubuntu 20.04+) for Linux, macOS 12+ for Darwin

**Why PyInstaller over alternatives:**

| Approach | Pros | Cons |
|---|---|---|
| **PyInstaller** ✅ | Proven, single binary, handles C extensions | Large binary (~100-200MB) |
| Nuitka | Faster startup, smaller | Complex build, C compiler required on build machine |
| Shiv/PEX | Simple, reliable | Requires Python on target |
| Go/Rust wrapper | Small binary | Two-language maintenance burden |

### 3.2 Skill Architecture

Skills are split into two tiers:

**Core skills** (bundled in binary):
- `shell` — run commands with dynamic approval + learning
- `memory` — read/write/search persistent memory
- `sessions` — session management
- `http_checker` — URL health checks

**Optional skills** (installed on demand):
- `kubernetes` — cluster operations, self-healing, manifest generation
- `aws` — EC2, S3, cost management
- `last9` — observability via MCP (requires Last9 account)
- `github` — GitHub operations via MCP (requires GitHub token + Node.js)
- `exception_fixer` — orchestrates Last9 + GitHub to auto-fix exceptions
- `cicd` — GitHub Actions operations

**Skill install flow:**

```
$ mithai skill install kubernetes
→ Copies skill files to ~/.mithai/skills/kubernetes/
→ No extra runtime deps needed (uses kubectl on PATH)

$ mithai skill install github
→ Copies skill files to ~/.mithai/skills/github/
→ Detects that GitHub MCP server needs Node.js
→ Checks if npx is available on PATH
→ If not: "GitHub skill requires Node.js. Install it? [y/N]"
→ If yes: installs Node.js via nvm or prints install instructions
→ Verifies: npx --version ✓
```

**Skill resolution order:**

1. `~/.mithai/skills/<name>/` (user-installed, highest priority)
2. `./skills/<name>/` (project-local, for development)
3. Bundled skills (in binary, lowest priority)

### 3.3 Custom Skill Runtime

The PyInstaller binary bundles a full Python 3.13 interpreter. Custom skills (created
via `mithai skill create` or manually) are dynamically imported via `importlib` at
runtime — this works inside PyInstaller because the interpreter handles the import,
not the frozen module system.

**What custom skills can use out of the box (already bundled):**
- Python stdlib (`json`, `subprocess`, `os`, `re`, `urllib`, etc.)
- `requests` / `httpx` (HTTP calls)
- `anthropic` (LLM calls)
- `pyyaml` (YAML parsing)
- `jinja2` (templating)
- `mcp` SDK (MCP client calls)

**When a skill needs extra packages:**

If a skill directory contains a `requirements.txt`, `mithai skill install` creates
a virtual environment at `~/.mithai/skill-venvs/<name>/` and installs the packages
there. The skill loader adds this venv to `sys.path` before importing the skill.

```
$ mithai skill install my-custom-skill
  → Found requirements.txt: boto3>=1.34, redis>=5.0
  → Creating venv at ~/.mithai/skill-venvs/my-custom-skill/
  → Installing 2 packages...
  ✓ Skill installed with dependencies

$ mithai skill create my-tool
  → Created ~/.mithai/skills/my-tool/prompt.md
  → Created ~/.mithai/skills/my-tool/tools.py
  → (optional) Create requirements.txt for extra pip packages
```

**Most skills won't need this.** The typical custom skill wraps a CLI tool via
`subprocess.run()` or calls an HTTP API with `requests` — both already bundled.

---

## 4. CLI Commands

### 4.1 `mithai init` — Interactive Setup Wizard

Full guided setup that configures all integrations and validates connections.

```
$ mithai init

🔧 Mithai Setup Wizard
======================

Step 1/6: LLM Provider
  Which LLM provider? [anthropic]
  Anthropic API key: sk-ant-••••••••
  Model [claude-sonnet-4-6]:
  ✓ API key valid — model accessible

Step 2/6: Adapter
  Which adapters? (space to select, enter to confirm)
  ❯ [x] Slack
    [ ] Telegram
    [x] CLI (always enabled)

  Slack Bot Token: xoxb-••••••••
  Slack App Token: xapp-••••••••
  ✓ Slack connected — workspace: "Last9" — bot user: @mithai

Step 3/6: Kubernetes
  Install kubernetes skill? [Y/n]
  ✓ kubectl found: v1.29.2

  KUBECONFIG paths (colon-separated, or press enter for default):
  > ~/.kube/config:/tmp/kubeconfig/alpha/platform

  Available contexts:
    1. alpha-last9-platform (current)
    2. staging-gke
    3. prod-eks-us-east

  ✓ Context alpha-last9-platform: 42 nodes, 1,203 pods
  ✓ Context staging-gke: 8 nodes, 156 pods
  ✓ Context prod-eks-us-east: 24 nodes, 892 pods

Step 4/6: MCP Servers
  Configure Last9? [Y/n]
  Last9 API Token: ••••••••
  ✓ Last9 MCP connected — 4 tools available

  Configure GitHub? [Y/n]
  GitHub Token: ghp_••••••••
  ✓ npx found: 10.8.2
  ✓ GitHub MCP connected — 18 tools available

Step 5/6: Skills
  Installing selected skills...
  ✓ kubernetes (8 read + 6 write tools)
  ✓ last9 (4 MCP tools)
  ✓ github (18 MCP tools)
  ✓ exception_fixer (1 native + 12 MCP tools)
  ✓ shell (1 tool, dynamic approval)
  ✓ memory (3 tools)

Step 6/6: Service
  Install as system service? [y/N]
  → Run manually with: mithai run

✓ Config written to ~/.mithai/config.yaml
✓ Memory directory: ~/.mithai/memory/
✓ State directory: ~/.mithai/state/

Run: mithai run
```

**Handling existing configs:**

If `~/.mithai/config.yaml` already exists (e.g., from a pip-based install or a
previous `mithai init`), the wizard presents an interactive choice:

```
⚠ Existing configuration found at ~/.mithai/config.yaml

  1. Start fresh (backup existing to config.yaml.bak)
  2. Update existing (add missing sections, keep current values)
  3. Abort

Choose [1/2/3]:
```

Option 2 (update) is the most useful for upgrades — it preserves existing tokens
and adapter config while adding new sections introduced in newer versions.

**What init validates:**
- LLM API key: makes a test API call
- Slack tokens: connects via Bolt, verifies bot identity
- Telegram token: calls `getMe` API
- kubectl: runs `kubectl cluster-info` against each context
- MCP servers: connects, calls `list_tools`, verifies tool count
- Node.js: checks `npx --version` if GitHub skill is selected

### 4.2 `mithai run` — Start the Agent

```
$ mithai run                           # all configured adapters
$ mithai run --adapter slack           # only Slack
$ mithai run --adapter cli             # only CLI (same as `mithai chat`)
$ mithai run --config ./my-config.yaml # custom config path
```

### 4.3 `mithai chat` — Interactive CLI Mode

Shortcut for `mithai run --adapter cli`. Opens a REPL.

### 4.4 `mithai skill` — Skill Management

```
$ mithai skill list                    # list all available + installed skills
$ mithai skill install kubernetes      # install optional skill
$ mithai skill install github          # install + resolve deps (Node.js)
$ mithai skill remove aws             # remove installed skill
$ mithai skill create my-skill        # scaffold a new custom skill
$ mithai skill validate               # check all skills for errors
```

### 4.5 `mithai service` — System Service Management

```
$ mithai service install               # create systemd unit or launchd plist
$ mithai service start                 # start the service
$ mithai service stop                  # stop the service
$ mithai service status                # show service status + recent logs
$ mithai service uninstall             # remove the service
```

**Linux (systemd):**
```ini
# /etc/systemd/system/mithai.service
[Unit]
Description=Mithai AI Operations Agent
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mithai run
EnvironmentFile=/etc/mithai/env
WorkingDirectory=/var/lib/mithai
Restart=on-failure
RestartSec=10
User=mithai

[Install]
WantedBy=multi-user.target
```

**macOS (launchd):**
```xml
<!-- ~/Library/LaunchAgents/io.mithai.agent.plist -->
<plist version="1.0">
<dict>
  <key>Label</key><string>io.mithai.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/mithai</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><!-- loaded from ~/.mithai/env --></dict>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/mithai.log</string>
  <key>StandardErrorPath</key><string>/tmp/mithai.err</string>
</dict>
</plist>
```

### 4.6 `mithai doctor` — Diagnostics

```
$ mithai doctor

Mithai Doctor
=============
Version:    0.2.0
Platform:   darwin-arm64
Config:     ~/.mithai/config.yaml

Checks:
  ✓ LLM (Anthropic claude-sonnet-4-6): connected
  ✓ Slack adapter: connected (workspace: Last9)
  ✓ kubectl: v1.29.2
  ✓ Cluster alpha-last9-platform: 42 nodes healthy
  ✗ Cluster staging-gke: connection refused
  ✓ MCP server last9: 4 tools
  ✓ MCP server github: 18 tools
  ✓ Node.js: v22.0.0 (required by github skill)
  ✓ Skills loaded: 8 (4 core + 4 optional)
  ✓ Memory dir: ~/.mithai/memory/ (writable)
  ✓ State dir: ~/.mithai/state/ (writable)

1 issue found.
```

---

## 5. Multi-Cluster Kubernetes

### Approach: Standard KUBECONFIG Merging

Mithai uses the same `KUBECONFIG` semantics as `kubectl`:

```yaml
# ~/.mithai/config.yaml
skills:
  config:
    kubernetes:
      # Colon-separated list of kubeconfig files (same as KUBECONFIG env var)
      kubeconfig: "~/.kube/config:/tmp/kubeconfig/alpha/platform:/tmp/kubeconfig/staging"
      # Default context (empty = current context from first kubeconfig)
      context: ""
      # Default namespace
      default_namespace: default
```

**How it works:**

1. `mithai init` prompts for KUBECONFIG paths and lists available contexts
2. The kubernetes skill's `kubectl` calls use `--kubeconfig` and `--context` flags
3. Users can specify cluster in natural language: *"get pods from staging-gke"*
4. The LLM resolves context names from the available list (injected into skill prompt)
5. Falls back to `KUBECONFIG` env var if not set in config

**Skill prompt injection for multi-cluster:**
```markdown
### Available Kubernetes Contexts
- alpha-last9-platform (current)
- staging-gke
- prod-eks-us-east

When the user mentions a cluster, map it to the correct context name
and pass it as the `context` parameter to kubernetes tools.
```

---

## 6. Secrets Management

**Approach:** Environment variables (current behavior, proven pattern)

```
# ~/.mithai/env (loaded by service, sourced by user)
ANTHROPIC_API_KEY=sk-ant-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
LAST9_API_TOKEN=...
GITHUB_TOKEN=ghp_...
```

- `mithai init` writes `~/.mithai/env` with the tokens collected during setup
- `config.yaml` uses `${ENV_VAR}` references (existing pattern)
- `mithai service install` creates an `EnvironmentFile` pointing to `~/.mithai/env`
- File permissions set to `0600` on creation
- `mithai doctor` verifies env vars are set but never prints values

---

## 7. File Layout

```
~/.mithai/                          # User's mithai home
├── config.yaml                     # Main configuration
├── env                             # Environment variables (secrets)
├── memory/                         # Persistent memory
│   ├── MEMORY.md                   # General facts
│   ├── daily/                      # Daily observations
│   └── approvals.json              # Approval learning data
├── state/                          # Session state
│   └── sessions/                   # Per-channel session files
└── skills/                         # User-installed optional skills
    ├── kubernetes/
    │   ├── prompt.md
    │   ├── tools.py
    │   └── scripts/
    ├── github/
    │   ├── prompt.md
    │   └── tools.py
    └── ...
```

---

## 8. Build & Distribution

### 8.1 Local Build Script

```makefile
# Makefile additions
PLATFORMS = darwin-arm64 darwin-amd64 linux-amd64 linux-arm64
VERSION  = $(shell python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")

.PHONY: build-binary
build-binary:
	pyinstaller mithai.spec --noconfirm

.PHONY: build-all
build-all:  ## Build for all platforms (requires cross-compilation or CI)
	@for platform in $(PLATFORMS); do \
		echo "Building mithai-$$platform-$(VERSION)..."; \
	done

.PHONY: release
release: build-binary  ## Create checksums and prepare release artifacts
	cd dist && shasum -a 256 mithai-* > checksums.txt
	@echo "Upload dist/mithai-* and dist/checksums.txt to GitHub Releases"
```

### 8.2 Build Environment

Both macOS and Linux build machines are available.

| Platform | Build method |
|---|---|
| macOS arm64 | Native build on Apple Silicon |
| macOS amd64 | Native build on Intel Mac (or cross-compile on arm64) |
| Linux amd64 | Native build on Linux machine |
| Linux arm64 | Native build on Linux arm64 machine |

**Note:** PyInstaller does not support cross-compilation — binaries must be built
on the target OS/arch. Each platform requires a separate build run.

### 8.3 PyInstaller Spec

```python
# mithai.spec
a = Analysis(
    ['src/mithai/cli/main.py'],
    pathex=['src'],
    datas=[
        ('skills/shell', 'skills/shell'),
        ('skills/memory', 'skills/memory'),
        ('skills/sessions', 'skills/sessions'),
        ('skills/http_checker', 'skills/http_checker'),
    ],
    hiddenimports=[
        'mithai.adapters.slack',
        'mithai.adapters.telegram',
        'mithai.adapters.cli',
        'mithai.llm.anthropic',
        'mithai.state.filesystem',
        'mithai.memory.filesystem',
        'mcp',
        'mcp.client.stdio',
        'mcp.client.sse',
        'mcp.client.streamable_http',
    ],
    # ...
)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name='mithai',
    strip=True,
    upx=True,
)
```

### 8.3 GitHub Releases

- Binaries named: `mithai-darwin-arm64`, `mithai-darwin-amd64`, `mithai-linux-amd64`, `mithai-linux-arm64`
- Each release includes `checksums.txt` (SHA256)
- Release notes generated from git log since last tag

### 8.4 Homebrew Tap

```ruby
# nishantmodak/homebrew-mithai/Formula/mithai.rb
class Mithai < Formula
  desc "AI agent framework for infrastructure operations"
  homepage "https://github.com/nishantmodak/mithai"
  version "0.2.0"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/nishantmodak/mithai/releases/download/v0.2.0/mithai-darwin-arm64"
      sha256 "..."
    else
      url "https://github.com/nishantmodak/mithai/releases/download/v0.2.0/mithai-darwin-amd64"
      sha256 "..."
    end
  end

  on_linux do
    url "https://github.com/nishantmodak/mithai/releases/download/v0.2.0/mithai-linux-amd64"
    sha256 "..."
  end

  def install
    bin.install "mithai-#{OS.mac? ? "darwin" : "linux"}-#{Hardware::CPU.arm? ? "arm64" : "amd64"}" => "mithai"
  end

  test do
    system "#{bin}/mithai", "--version"
  end
end
```

**Install:** `brew install nishantmodak/mithai/mithai`

### 8.5 Curl Installer

```bash
#!/bin/sh
# install.sh — hosted at https://get.mithai.dev
set -euf

REPO="nishantmodak/mithai"
INSTALL_DIR="${MITHAI_INSTALL_DIR:-/usr/local/bin}"

detect_platform() {
  OS=$(uname -s | tr '[:upper:]' '[:lower:]')
  ARCH=$(uname -m)
  case "$ARCH" in
    x86_64|amd64) ARCH="amd64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
  esac
  echo "${OS}-${ARCH}"
}

PLATFORM=$(detect_platform)
VERSION=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" | grep tag_name | cut -d'"' -f4)
URL="https://github.com/${REPO}/releases/download/${VERSION}/mithai-${PLATFORM}"

echo "Installing mithai ${VERSION} for ${PLATFORM}..."
curl -fsSL "$URL" -o /tmp/mithai
chmod +x /tmp/mithai

if [ -w "$INSTALL_DIR" ]; then
  mv /tmp/mithai "$INSTALL_DIR/mithai"
else
  sudo mv /tmp/mithai "$INSTALL_DIR/mithai"
fi

echo "✓ mithai installed to ${INSTALL_DIR}/mithai"
echo "Run: mithai init"
```

---

## 9. Versioning

**Scheme:** Semantic versioning (semver), manual releases.

```
v0.1.0  — current (Python package only)
v0.2.0  — first binary release (this spec)
v0.3.0  — MCP server mode (Claude Code integration)
v1.0.0  — stable, production-ready
```

- Version lives in `pyproject.toml` (`project.version`)
- Binary embeds version at build time
- `mithai --version` prints: `mithai 0.2.0 (darwin-arm64)`
- Git tags: `v0.2.0`
- No auto-update; users re-run the curl installer or `brew upgrade mithai`

---

## 10. Implementation Sequence

### Phase 1: Binary Build (core)
1. Create `mithai.spec` PyInstaller spec file
2. Add `build-binary` target to Makefile
3. Bundle core skills (shell, memory, sessions, http_checker) as data files
4. Test binary on macOS arm64 + Linux amd64
5. Add `--version` flag to CLI

### Phase 2: Skill Install System
6. Implement `mithai skill install <name>` — copies from a bundled archive or fetches from GitHub release
7. Implement skill dependency resolution (e.g., github skill → check for npx)
8. Implement `~/.mithai/skills/` as a skill search path
9. Implement `mithai skill remove <name>`

### Phase 3: Interactive Init
10. Build `mithai init` wizard with step-by-step prompts
11. Add connection validation for each integration
12. Write `~/.mithai/config.yaml` and `~/.mithai/env`
13. Multi-cluster KUBECONFIG support in wizard

### Phase 4: Service Management
14. Implement `mithai service install` (systemd + launchd)
15. Implement `mithai service start/stop/status`
16. Implement `mithai service uninstall`

### Phase 5: Distribution
17. Write `install.sh` curl installer
18. Create Homebrew tap repository
19. Create GitHub Release workflow (manual trigger)
20. Write `mithai doctor` diagnostics command

---

## 11. Testing Strategy

**Approach:** Manual QA checklist for release verification.

### Release QA Checklist

```
Binary Build
  [ ] Binary builds without errors on macOS arm64
  [ ] Binary builds without errors on Linux amd64
  [ ] `mithai --version` prints correct version and platform
  [ ] Binary size is within expected range

Install
  [ ] curl installer detects platform correctly
  [ ] curl installer downloads and installs binary
  [ ] `brew install nishantmodak/mithai/mithai` works
  [ ] Binary runs from installed location

Init Wizard
  [ ] `mithai init` runs the full wizard
  [ ] Anthropic API key validation works (valid + invalid key)
  [ ] Slack token validation works
  [ ] kubectl context detection works
  [ ] MCP server connection test works (Last9 streamablehttp)
  [ ] MCP server connection test works (GitHub stdio)
  [ ] Config written to ~/.mithai/config.yaml
  [ ] Env file written to ~/.mithai/env with 0600 permissions
  [ ] Existing config detection works (fresh / update / abort)

Skills
  [ ] `mithai skill list` shows core + installed skills
  [ ] `mithai skill install kubernetes` works
  [ ] `mithai skill install github` detects Node.js dependency
  [ ] `mithai skill remove kubernetes` works
  [ ] `mithai skill create my-skill` scaffolds correctly
  [ ] `mithai skill validate` catches errors

Runtime
  [ ] `mithai chat` starts CLI REPL
  [ ] `mithai run --adapter slack` connects to Slack
  [ ] Tool calling works (e.g., shell__run_command)
  [ ] Human MCP approval flow works in Slack
  [ ] Multi-cluster k8s context switching works
  [ ] MCP-backed tools work (Last9, GitHub)

Service
  [ ] `mithai service install` creates service file
  [ ] `mithai service start` starts the service
  [ ] `mithai service status` shows running
  [ ] `mithai service stop` stops the service
  [ ] `mithai service uninstall` removes service file

Doctor
  [ ] `mithai doctor` runs all checks
  [ ] Reports failures clearly (e.g., unreachable cluster)
  [ ] Works without network (offline checks pass)
```

---

## 12. Resolved Decisions

1. **Skill updates**: Skills have independent version numbers. `mithai skill update` checks for newer skill versions independently of the binary. Skills are not tied to binary releases — they evolve on their own cadence.

2. **Offline mode**: Full offline support. CLI tools (`doctor`, `skill list`, `service status`) work without internet. Additionally, support local LLM providers (Ollama, llama.cpp) as an alternative to Anthropic for air-gapped environments. The `llm.provider` config key supports `anthropic` (default) and `ollama`.

3. **Config migration**: Future scope. Not in v0.2.0. Breaking config changes will be documented in release notes for now.

4. **Telemetry**: Opt-in. `mithai init` asks if the user wants to share anonymous usage data. Off by default. Covers: skill usage frequency, adapter types, error rates. No PII, no message content, no tool inputs.

5. **Multi-user**: Single config per instance. One `config.yaml`, one Slack workspace, one set of credentials. Deploy multiple Mithai instances for multiple workspaces.

6. **Custom skill runtime**: Bundled Python by default. Custom skills use the PyInstaller-embedded interpreter. Skills with extra dependencies declare them in `requirements.txt` — `mithai skill install` creates a per-skill venv at `~/.mithai/skill-venvs/<name>/`.

7. **Existing config handling**: Interactive choice — user picks between starting fresh (with backup), updating existing config, or aborting.

8. **Build environment**: Native builds on both macOS and Linux machines (PyInstaller does not cross-compile). Four platform targets: darwin-arm64, darwin-amd64, linux-amd64, linux-arm64.

9. **Testing**: Manual QA checklist for release verification. No automated integration test suite for the binary in v0.2.0.

10. **Non-interactive init**: Future scope. v0.2.0 is interactive only.

11. **MCP server mode** (Claude Code integration): Not in v0.2.0 scope. Planned for v0.3.0. When built, approval model will use CLI prompts (stdin) — simple, no Slack dependency required for dev use.

12. **Positioning**: Generic open-source framework. Not tied to Last9 or any specific vendor.
