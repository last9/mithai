# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for mithai — AI agent framework for infrastructure operations.

Build: pyinstaller mithai.spec --noconfirm
Output: dist/mithai (single binary)

Core skills (shell, memory, sessions, http_checker) are bundled as data files.
Optional skills (kubernetes, aws, last9, github, etc.) are installed separately
via `mithai skill install <name>`.
"""

import platform

block_cipher = None

# Core skills to bundle in the binary
CORE_SKILLS = [
    'shell',
    'memory',
    'sessions',
    'http_checker',
]

# Build datas list from core skills
datas = []
for skill in CORE_SKILLS:
    datas.append((f'skills/{skill}', f'skills/{skill}'))

# All optional skills are bundled as installable archives
OPTIONAL_SKILLS = [
    'aws',
    'cicd',
    'exception_fixer',
    'github',
    'kubernetes',
    'last9',
]
for skill in OPTIONAL_SKILLS:
    datas.append((f'skills/{skill}', f'_optional_skills/{skill}'))

a = Analysis(
    ['src/mithai/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Core mithai modules (lazy-imported, PyInstaller won't trace them)
        'mithai',
        'mithai.__main__',
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

        # Third-party: always needed
        'anthropic',
        'click',
        'yaml',
        'dotenv',
        'jinja2',
        'requests',

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

# Determine platform suffix for binary name
arch = platform.machine()
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
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
