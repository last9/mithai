"""Tests for `mithai init` filesystem defaults."""

import stat
from pathlib import Path

from click.testing import CliRunner

from mithai.cli.init_cmd import init


def _patch_init_prompts(monkeypatch):
    import mithai.cli.init_cmd as init_mod
    import mithai.cli.skill_cmd as skill_mod

    monkeypatch.setattr(init_mod, "_validate_anthropic_key", lambda *_: (True, "ok"))
    monkeypatch.setattr(skill_mod, "_available_optional_skills", lambda: {})
    monkeypatch.setattr(
        init_mod.Prompt,
        "ask",
        staticmethod(lambda prompt, *_, default=None, **__: "sk-ant-test" if "API key" in prompt else default),
    )
    monkeypatch.setattr(
        init_mod.IntPrompt,
        "ask",
        staticmethod(lambda *_, default=None, **__: default),
    )
    monkeypatch.setattr(
        init_mod.Confirm,
        "ask",
        staticmethod(lambda *_, default=False, **__: default),
    )


def test_init_defaults_to_current_directory(tmp_path, monkeypatch):
    _patch_init_prompts(monkeypatch)

    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(init)
        cwd = Path.cwd()

        assert result.exit_code == 0, result.output
        assert (cwd / "config.yaml").exists()
        assert (cwd / ".env").exists()
        assert (cwd / ".gitignore").exists()
        assert (cwd / "skills").is_dir()
        assert (cwd / "memory").is_dir()
        assert (cwd / ".mithai" / "state").is_dir()


def test_init_writes_dotenv_and_gitignore(tmp_path, monkeypatch):
    _patch_init_prompts(monkeypatch)

    result = CliRunner().invoke(
        init,
        ["--dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output

    env_path = tmp_path / ".env"
    assert env_path.exists()
    assert not (tmp_path / "env").exists()
    assert "ANTHROPIC_API_KEY=sk-ant-test" in env_path.read_text()
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    gitignore_path = tmp_path / ".gitignore"
    assert gitignore_path.exists()
    assert ".env" in gitignore_path.read_text().splitlines()
    assert (tmp_path / "skills").is_dir()
