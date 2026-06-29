"""Tests for `mithai doctor` config path and filesystem checks."""

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
