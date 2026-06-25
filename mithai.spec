# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for mithai — AI agent framework for infrastructure operations.

Build: pyinstaller mithai.spec --noconfirm
Output: dist/mithai (single binary)

Core skills are bundled as data files. Optional skills are installed separately
via `mithai skill install <name>`.
"""

import os
import platform

# PyInstaller helpers — required, unconditional. pyproject.toml pins
# pyinstaller>=6.0 so both `collect_all` and `copy_metadata` are guaranteed.
from PyInstaller.utils.hooks import collect_all, copy_metadata

# Bundle mithai's *.dist-info so importlib.metadata.version("mithai") works
# inside the frozen binary — without this, src/mithai/__init__.py falls back
# to "0.0.0-dev" and `mithai --version` reports the wrong version.
# This must succeed at build time (mithai is the package we're freezing); if
# the dist-info is missing, fail the build loudly rather than ship a binary
# with the silent fallback.
mithai_metadata = copy_metadata('mithai')

# Optional third-party collections — each wrapped so a missing dep (e.g.
# building without an extra) degrades gracefully without masking the required
# PyInstaller helpers above. _safe_collect keeps the (datas, binaries,
# hiddenimports) tuple ordering in one place so a per-package block can't slot
# the three lists into the wrong Analysis fields.
def _safe_collect(pkg):
    try:
        return collect_all(pkg)
    except ImportError:
        # Package genuinely absent (extra not installed) — degrade gracefully.
        # Any other failure (broken install, bad transitive dep) surfaces as a
        # build error instead of silently shipping without the package.
        return [], [], []

uvicorn_datas, uvicorn_binaries, uvicorn_hiddenimports = _safe_collect('uvicorn')
starlette_datas, starlette_binaries, starlette_hiddenimports = _safe_collect('starlette')
otel_datas, otel_binaries, otel_hiddenimports = _safe_collect('opentelemetry')

# Last9 GenAI span processor — enriches LLM spans with Last9's GenAI semantics.
# Optional: only present when built with the `telemetry` extra (which depends
# on last9-genai). Without it the binary still exports OTLP fine; mithai's tracer
# just skips the processor (the `from last9_genai import ...` is guarded).
last9_datas, last9_binaries, last9_hiddenimports = _safe_collect('last9_genai')

# Build pipelines that require the optional GenAI processor can set
# MITHAI_REQUIRE_TELEMETRY=1 to fail loudly if the `telemetry` extra was not
# installed. Public release builds leave it optional and keep graceful
# degradation.
if os.environ.get('MITHAI_REQUIRE_TELEMETRY', '').strip() == '1' and not last9_hiddenimports:
    raise SystemExit(
        "MITHAI_REQUIRE_TELEMETRY=1 but collect_all('last9_genai') returned nothing — "
        "install the 'telemetry' extra (pip install 'mithai[telemetry]') before building."
    )

block_cipher = None

# Core skills to bundle in the binary
CORE_SKILLS = [
    'shell',
    'memory',
    'sessions',
    'http_checker',
    'scheduling',
    'kubernetes',
]

# Build datas list from core skills
datas = []
for skill in CORE_SKILLS:
    datas.append((f'skills/{skill}', f'skills/{skill}'))

# Bundle UI templates and static assets
datas.append(('src/mithai/ui/templates', 'mithai/ui/templates'))
datas.append(('src/mithai/ui/static', 'mithai/ui/static'))

a = Analysis(
    ['src/mithai/__main__.py'],
    pathex=['src'],
    binaries=[] + uvicorn_binaries + starlette_binaries + otel_binaries + last9_binaries,
    datas=datas + uvicorn_datas + starlette_datas + otel_datas + last9_datas + mithai_metadata,
    hiddenimports=[] + uvicorn_hiddenimports + starlette_hiddenimports + otel_hiddenimports + last9_hiddenimports + [
        # Core mithai modules (lazy-imported, PyInstaller won't trace them)
        'mithai',
        'mithai.__main__',
        'mithai.adapters.api',
        'mithai.adapters.cli',
        'mithai.adapters.slack',
        'mithai.adapters.telegram',
        'mithai.adapters.formatters',
        'mithai.llm.anthropic',
        'mithai.llm.base',
        'mithai.state.filesystem',
        'mithai.state.memory',
        'mithai.core.engine',
        'mithai.core.mcp_manager',
        'mithai.core.config',
        'mithai.core.context',
        'mithai.core.reflection',
        'mithai.core.session',
        'mithai.core.skill_loader',
        'mithai.core.tool_router',
        'mithai.human.mcp',
        'mithai.cli.run_cmd',
        'mithai.cli.chat_cmd',
        'mithai.cli.init_cmd',
        'mithai.cli.skill_cmd',
        'mithai.cli.service_cmd',
        'mithai.cli.doctor_cmd',
        'mithai.cli.ui_cmd',
        'mithai.cli.agent_cmd',
        'mithai.cli.style',

        # Memory backends
        'mithai.memory.base',
        'mithai.memory.filesystem',
        'mithai.memory.redis',
        'mithai.memory.s3',

        # Control Room UI
        'mithai.ui',
        'mithai.ui.app',
        'mithai.ui.data',

        # Third-party: always needed
        'anthropic',
        'click',
        'yaml',
        'dotenv',
        'jinja2',
        'requests',
        'rich',
        'rich.console',
        'rich.panel',
        'rich.table',
        'rich.markdown',
        'rich.theme',
        'rich.prompt',

        # Third-party: optional adapters
        'slack_bolt',
        'slack_bolt.adapter.socket_mode',
        'slack_sdk',
        'slack_sdk.errors',
        'slack_sdk.web',

        # Third-party: MCP
        'mcp',
        'mcp.client.stdio',
        'mcp.client.sse',
        'mcp.client.streamable_http',

        # Third-party: UI (Control Room) — collected via collect_all above

        # Transitive deps that PyInstaller may miss
        'anyio',
        'anyio._backends._asyncio',
        'httpx',
        'httpcore',
        'h11',
        'certifi',
        'charset_normalizer',
        'idna',
        'sniffio',
        'pydantic',
        'pydantic.deprecated.decorator',
        'pydantic_core',
        'annotated_types',
        'typing_extensions',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude unnecessary large packages
        'tkinter',
        'matplotlib',
        'numpy',
        'pandas',
        'scipy',
        'PIL',
        'cv2',
        'test',
        'unittest',
        'pytest',
    ],
    noarchive=False,
    optimize=0,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

# Determine platform suffix for binary name.
# MITHAI_TARGET_ARCH overrides the detected arch for cross-compilation
# (e.g. building x86_64 binary on ARM macOS).
_target_arch = os.environ.get('MITHAI_TARGET_ARCH', '').strip()
arch = _target_arch or platform.machine()
if arch == 'x86_64':
    arch = 'amd64'
system = platform.system().lower()
binary_name = f'mithai-{system}-{arch}'

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=binary_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=_target_arch or None,
    codesign_identity=None,
    entitlements_file=None,
)
