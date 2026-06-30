"""Tests for skill loader."""


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


def test_validate_prompt_only_skill_is_valid(tmp_path):
    """tools.py is optional: a prompt-only skill validates clean."""
    skill_dir = tmp_path / "prompt_only"
    skill_dir.mkdir()
    (skill_dir / "prompt.md").write_text("test")

    assert validate_skill(skill_dir) == []


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


def test_load_dynamic_skill(tmp_dynamic_skill_dir):
    """Skills with resolve_human are loaded correctly."""
    skills = load_skills([tmp_dynamic_skill_dir])
    assert "dynamic_shell" in skills

    skill = skills["dynamic_shell"]
    assert skill.tools[0].human == "dynamic"
    assert skill.resolve_human is not None

    # Safe command → auto-execute
    level = skill.resolve_human("run_command", {"command": "uptime"}, {})
    assert level is None

    # Unsafe command → needs approval
    level = skill.resolve_human("run_command", {"command": "rm -rf /"}, {})
    assert level == "approve"


def test_load_skill_without_resolve_human(tmp_skill_dir):
    """Skills without resolve_human have it set to None."""
    skills = load_skills([tmp_skill_dir])
    skill = skills["test_skill"]
    assert skill.resolve_human is None


def test_validate_dynamic_human_level(tmp_path):
    """dynamic is a valid human level."""
    skill_dir = tmp_path / "dynamic_skill"
    skill_dir.mkdir()
    (skill_dir / "prompt.md").write_text("test")
    (skill_dir / "tools.py").write_text('''
TOOLS = [{"name": "x", "description": "x", "input_schema": {}, "human": "dynamic"}]
def resolve_human(n, i, c): return None
def handle(n, i, c): pass
''')

    errors = validate_skill(skill_dir)
    assert errors == []


def test_skill_verify_false_by_default(tmp_skill_dir):
    """Skills without VERIFY = True have verify=False."""
    skills = load_skills([tmp_skill_dir])
    assert skills["test_skill"].verify is False


def test_skill_verify_true_when_declared(tmp_path):
    """Skills with VERIFY = True in tools.py have verify=True."""
    skill_dir = tmp_path / "skills" / "ops_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "prompt.md").write_text("ops skill")
    (skill_dir / "tools.py").write_text(
        'TOOLS = [{"name": "t", "description": "d", "input_schema": {}}]\n'
        'VERIFY = True\n'
        'def handle(n, i, c): pass\n'
    )
    skills = load_skills([tmp_path / "skills"])
    assert skills["ops_skill"].verify is True


def test_skill_verify_false_when_explicitly_false(tmp_path):
    """Skills with VERIFY = False have verify=False."""
    skill_dir = tmp_path / "skills" / "safe_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "prompt.md").write_text("safe skill")
    (skill_dir / "tools.py").write_text(
        'TOOLS = [{"name": "t", "description": "d", "input_schema": {}}]\n'
        'VERIFY = False\n'
        'def handle(n, i, c): pass\n'
    )
    skills = load_skills([tmp_path / "skills"])
    assert skills["safe_skill"].verify is False


_TOOLS_PY = (
    'TOOLS = [{"name": "t", "description": "d", "input_schema": {}}]\n'
    'def handle(n, i, c): pass\n'
)


def test_load_skill_via_skill_md(tmp_path):
    """A skill exposing skill.md (instead of prompt.md) loads."""
    skill_dir = tmp_path / "skills" / "md_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("skill md prompt")
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    skills = load_skills([tmp_path / "skills"])
    assert skills["md_skill"].prompt == "skill md prompt"


def test_prompt_md_wins_over_skill_md(tmp_path):
    """When both files exist, prompt.md takes precedence."""
    skill_dir = tmp_path / "skills" / "both_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "prompt.md").write_text("from prompt")
    (skill_dir / "skill.md").write_text("from skill")
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    skills = load_skills([tmp_path / "skills"])
    assert skills["both_skill"].prompt == "from prompt"


def test_validate_skill_via_skill_md(tmp_path):
    """validate_skill accepts skill.md as the prompt file."""
    skill_dir = tmp_path / "md_skill"
    skill_dir.mkdir()
    (skill_dir / "skill.md").write_text("skill md prompt")
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    assert validate_skill(skill_dir) == []


def test_validate_missing_both_prompt_files(tmp_path):
    """Missing both prompt.md and skill.md is an error mentioning both."""
    skill_dir = tmp_path / "bad_skill"
    skill_dir.mkdir()
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    errors = validate_skill(skill_dir)
    assert any("prompt.md" in e and "skill.md" in e for e in errors)


def test_load_prompt_only_skill(tmp_path):
    """A skill with skill.md but no tools.py loads as a prompt-only skill."""
    import pytest

    skill_dir = tmp_path / "skills" / "last9_logs"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("query logs prompt")

    skills = load_skills([tmp_path / "skills"])
    skill = skills["last9_logs"]
    assert skill.prompt == "query logs prompt"
    assert skill.tools == []
    # The placeholder handle must raise if ever invoked (no native tools).
    with pytest.raises(RuntimeError):
        skill.handle("anything", {}, {})


def test_frontmatter_stripped_from_prompt(tmp_path):
    """YAML frontmatter is stripped before the prompt is injected."""
    skill_dir = tmp_path / "skills" / "fm_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text(
        "---\n"
        "name: fm_skill\n"
        "description: does things\n"
        "metadata:\n"
        "  k: v\n"
        "---\n"
        "Actual prompt body.\n"
    )

    skills = load_skills([tmp_path / "skills"])
    prompt = skills["fm_skill"].prompt
    assert prompt == "Actual prompt body."
    assert "name:" not in prompt
    assert "description:" not in prompt


def test_no_frontmatter_left_intact(tmp_path):
    """A prompt with a leading '---' that is not frontmatter is untouched."""
    skill_dir = tmp_path / "skills" / "dash_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("Heading\n---\nbody")
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    skills = load_skills([tmp_path / "skills"])
    assert skills["dash_skill"].prompt == "Heading\n---\nbody"


def test_crlf_frontmatter_stripped(tmp_path):
    """Frontmatter with CRLF line endings is stripped."""
    skill_dir = tmp_path / "skills" / "crlf_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("---\r\nname: x\r\n---\r\nBody.\r\n")
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    skills = load_skills([tmp_path / "skills"])
    assert skills["crlf_skill"].prompt == "Body."


def test_empty_frontmatter_block_stripped(tmp_path):
    """An empty frontmatter block (--- immediately followed by ---) is stripped."""
    skill_dir = tmp_path / "skills" / "empty_fm"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("---\n---\nBody.")
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    skills = load_skills([tmp_path / "skills"])
    assert skills["empty_fm"].prompt == "Body."


def test_frontmatter_without_trailing_newline(tmp_path):
    """A file that is only frontmatter with no trailing newline strips to empty."""
    skill_dir = tmp_path / "skills" / "fm_eof"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.md").write_text("---\nname: x\n---")
    (skill_dir / "tools.py").write_text(_TOOLS_PY)

    skills = load_skills([tmp_path / "skills"])
    assert skills["fm_eof"].prompt == ""
