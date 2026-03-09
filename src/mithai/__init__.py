"""mithai — AI agent framework for organizations."""

import platform
import sys
from pathlib import Path

__version__ = "0.2.0"


def _platform_tag() -> str:
    """Return a platform tag like 'darwin-arm64'."""
    system = platform.system().lower()
    arch = platform.machine()
    if arch == "x86_64":
        arch = "amd64"
    return f"{system}-{arch}"


def get_version_string() -> str:
    """Return version string with platform info."""
    return f"{__version__} ({_platform_tag()})"


def get_bundled_path() -> Path:
    """Return the base path for bundled data files.

    When running from a PyInstaller binary, data files are extracted
    to sys._MEIPASS. When running from source, use the repo root.
    """
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    # Running from source — assume repo root is two levels up from src/mithai/
    return Path(__file__).resolve().parent.parent.parent
