"""Tests for skill loader."""

from pathlib import Path

from mithai.core.skill_loader import load_skills, validate_skill


def test_load_skills(tmp_skill_dir):
    skills = load_skills([tmp_skill_dir])
    assert "test_skill" in skills

    skill = skills["test_skill"]
    assert skill.name == "test_skill"
    assert len(skill.tools) == 2
    assert skill.tools[0].name == "echo"
    assert skill.tools[0].human is None
    assert skill.tools[1].name == "risky_action"
    assert skill.tools[1].human == "approve"
    assert "test skill" in skill.prompt.lower()


def test_load_skills_empty_dir(tmp_path):
    empty = tmp_path / "empty_skills"
    empty.mkdir()
    skills = load_skills([empty])
    assert skills == {}


def test_load_skills_nonexistent_dir(tmp_path):
    skills = load_skills([tmp_path / "nonexistent"])
    assert skills == {}


def test_validate_skill(tmp_skill_dir):
    skill_dir = tmp_skill_dir / "test_skill"
    errors = validate_skill(skill_dir)
    assert errors == []


def test_validate_missing_prompt(tmp_path):
    skill_dir = tmp_path / "bad_skill"
    skill_dir.mkdir()
    (skill_dir / "tools.py").write_text("TOOLS = []\ndef handle(n, i, c): pass")

    errors = validate_skill(skill_dir)
    assert any("prompt.md" in e for e in errors)


def test_validate_missing_tools(tmp_path):
    skill_dir = tmp_path / "bad_skill"
    skill_dir.mkdir()
    (skill_dir / "prompt.md").write_text("test")

    errors = validate_skill(skill_dir)
    assert any("tools.py" in e for e in errors)


def test_validate_invalid_human_level(tmp_path):
    skill_dir = tmp_path / "bad_skill"
    skill_dir.mkdir()
    (skill_dir / "prompt.md").write_text("test")
    (skill_dir / "tools.py").write_text('''
TOOLS = [{"name": "x", "description": "x", "input_schema": {}, "human": "invalid"}]
def handle(n, i, c): pass
''')

    errors = validate_skill(skill_dir)
    assert any("invalid" in e for e in errors)
