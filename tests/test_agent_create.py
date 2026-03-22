"""Tests for mithai agent create command."""

import tempfile
from pathlib import Path

from click.testing import CliRunner

from mithai.cli.agent_cmd import agent


def _make_config(tmpdir: Path) -> Path:
    """Create a minimal config.yaml in tmpdir."""
    config = tmpdir / "config.yaml"
    config.write_text(
        "bot:\n"
        "  name: test\n"
        "\n"
        "state:\n"
        "  backend: filesystem\n"
        "  filesystem:\n"
        "    path: ./.mithai/state\n"
    )
    return config


def test_create_generates_directory_and_files():
    """agent create produces system_prompt.md, .env.example, and memory/."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = _make_config(Path(tmpdir))
        agent_dir = Path(tmpdir) / "agents" / "mybot"

        runner = CliRunner()
        result = runner.invoke(agent, [
            "create", "mybot",
            "--name", "My Bot",
            "--skills", "shell,memory",
            "--config", str(config),
            "--dir", str(agent_dir),
        ])

        assert result.exit_code == 0, result.output
        assert (agent_dir / "system_prompt.md").exists()
        assert (agent_dir / ".env.example").exists()
        assert (agent_dir / "memory").is_dir()
        assert "My Bot" in (agent_dir / "system_prompt.md").read_text()


def test_create_adds_agents_section_to_config():
    """agent create adds an agents: block when none exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = _make_config(Path(tmpdir))
        agent_dir = Path(tmpdir) / "agents" / "sre"

        runner = CliRunner()
        result = runner.invoke(agent, [
            "create", "sre",
            "--skills", "shell,kubernetes",
            "--config", str(config),
            "--dir", str(agent_dir),
        ])

        assert result.exit_code == 0, result.output
        config_text = config.read_text()
        assert "agents:" in config_text
        assert "sre:" in config_text
        assert "default_agent: sre" in config_text
        assert "shell, kubernetes" in config_text


def test_create_appends_to_existing_agents_section():
    """agent create appends to an existing agents: section."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = Path(tmpdir) / "config.yaml"
        config.write_text(
            "bot:\n"
            "  name: test\n"
            "\n"
            "agents:\n"
            "  first:\n"
            "    name: First\n"
            "    skills:\n"
            "      allowed: [shell]\n"
            "\n"
            "  default_agent: first\n"
            "\n"
            "state:\n"
            "  backend: filesystem\n"
        )

        runner = CliRunner()
        result = runner.invoke(agent, [
            "create", "second",
            "--config", str(config),
            "--dir", str(Path(tmpdir) / "agents" / "second"),
        ])

        assert result.exit_code == 0, result.output
        config_text = config.read_text()
        assert "first:" in config_text
        assert "second:" in config_text


def test_create_rejects_invalid_agent_id():
    """agent create rejects IDs with uppercase or hyphens."""
    runner = CliRunner()
    result = runner.invoke(agent, ["create", "Bad-Name"])
    assert result.exit_code != 0
    assert "Invalid agent ID" in result.output


def test_create_rejects_duplicate_directory():
    """agent create fails if the agent directory already exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = _make_config(Path(tmpdir))
        agent_dir = Path(tmpdir) / "agents" / "dup"
        agent_dir.mkdir(parents=True)

        runner = CliRunner()
        result = runner.invoke(agent, [
            "create", "dup",
            "--config", str(config),
            "--dir", str(agent_dir),
        ])

        assert result.exit_code != 0
        assert "already exists" in result.output
