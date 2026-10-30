"""Keep the SKILL.md release distinct from the old PyPI distribution."""

import tomllib
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def test_skill_md_release_has_new_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    # 0.12.0 on PyPI predates SKILL.md and cannot be replaced in place.
    assert Version(project["version"]) >= Version("0.13.0")


def test_lockfile_matches_release_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    package = next(package for package in lock["package"] if package["name"] == "mithai")
    assert package["version"] == project["version"]


def test_chat_telemetry_release_has_new_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    # 0.13.0 predates chat's telemetry initialization and exit flush.
    assert Version(project["version"]) >= Version("0.13.1")
