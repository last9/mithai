"""Tests for `mithai doctor` config path and filesystem checks."""

import os
from pathlib import Path

from click.testing import CliRunner

from mithai.cli import doctor_cmd


def test_doctor_defaults_to_local_config(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_cmd, "_check_llm", lambda _config: True)
    monkeypatch.setattr(doctor_cmd, "_check_adapters", lambda _config: 0)
    monkeypatch.setattr(doctor_cmd, "_check_mcp", lambda _config: 0)
    monkeypatch.setattr(doctor_cmd, "_check_kubectl", lambda _config: 0)
    monkeypatch.setattr(doctor_cmd, "_check_gh_cli", lambda: True)
    monkeypatch.setattr(doctor_cmd, "_check_skills", lambda _config: 0)
    monkeypatch.setattr(doctor_cmd, "_check_directories", lambda _config: 0)
    monkeypatch.setattr(doctor_cmd, "load_skills", lambda _paths: {})

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        Path("config.yaml").write_text(
            "adapter:\n"
            "  types: [cli]\n"
            "llm:\n"
            "  provider: anthropic\n"
            "  anthropic:\n"
            "    api_key: ${ANTHROPIC_API_KEY}\n"
        )
        Path(".env").write_text("ANTHROPIC_API_KEY=sk-ant-test\n")

        result = runner.invoke(doctor_cmd.doctor)

    assert result.exit_code == 0, result.output
    assert "Config" in result.output
    assert "config.yaml" in result.output


def test_doctor_checks_configured_filesystem_dirs(tmp_path):
    memory_dir = tmp_path / "memory"
    state_dir = tmp_path / ".mithai" / "state"
    memory_dir.mkdir()
    state_dir.mkdir(parents=True)

    config = {
        "learning": {
            "memory": {
                "backend": "filesystem",
                "filesystem": {"path": str(memory_dir)},
            },
        },
        "state": {
            "backend": "filesystem",
            "filesystem": {"path": str(state_dir)},
        },
    }

    assert doctor_cmd._check_directories(config) == 0


def test_doctor_accepts_legacy_memory_dir(tmp_path):
    memory_dir = tmp_path / "legacy-memory"
    memory_dir.mkdir()

    assert doctor_cmd._configured_memory_dir(
        {"learning": {"memory_dir": str(memory_dir)}}
    ) == memory_dir


def test_doctor_resolves_new_filesystem_memory_shape():
    config = {
        "learning": {
            "memory": {"backend": "filesystem", "filesystem": {"path": "/custom/mem"}},
        },
    }
    assert doctor_cmd._configured_memory_dir(config) == Path("/custom/mem")
    # Absent config falls back to the canonical default.
    assert doctor_cmd._configured_memory_dir({}) == Path("./memory")


def test_doctor_skips_non_filesystem_backends():
    memory_none = doctor_cmd._configured_memory_dir(
        {"learning": {"memory": {"backend": "redis"}}}
    )
    state_none = doctor_cmd._configured_state_dir({"state": {"backend": "memory"}})

    assert memory_none is None
    assert state_none is None


def test_check_directories_reports_missing_dir(tmp_path):
    config = {
        "learning": {
            "memory": {"backend": "filesystem", "filesystem": {"path": str(tmp_path / "nope")}},
        },
        "state": {
            "backend": "filesystem",
            "filesystem": {"path": str(tmp_path / ".mithai" / "state")},
        },
    }
    (tmp_path / ".mithai" / "state").mkdir(parents=True)

    # Missing memory dir -> one failure.
    assert doctor_cmd._check_directories(config) == 1


def test_check_directories_reports_non_writable_dir(tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    memory_dir.chmod(0o500)  # exists but not writable
    try:
        config = {
            "learning": {
                "memory": {
                    "backend": "filesystem",
                    "filesystem": {"path": str(memory_dir)},
                },
            },
        }
        # State backend non-filesystem -> skipped, so only memory is checked.
        config["state"] = {"backend": "memory"}

        # A non-writable existing dir counts as a failure (skip if test runs as root).
        if os.access(memory_dir, os.W_OK):
            return
        assert doctor_cmd._check_directories(config) == 1
    finally:
        memory_dir.chmod(0o700)
