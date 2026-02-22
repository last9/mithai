"""mithai skill — manage skills (create, list, validate)."""

import click
from pathlib import Path

from mithai.core.config import get_skill_paths, load_config
from mithai.core.skill_loader import load_skills, validate_skill


SKILL_PROMPT_TEMPLATE = """Describe what this skill does.
The AI will see this as part of its system prompt.
"""

SKILL_TOOLS_TEMPLATE = '''"""Skill: {name}"""

import json

TOOLS = [
    {{
        "name": "example_tool",
        "description": "Describe what this tool does.",
        "input_schema": {{
            "type": "object",
            "properties": {{
                "param": {{
                    "type": "string",
                    "description": "Describe the parameter",
                }},
            }},
            "required": ["param"],
        }},
        # Uncomment for human-in-the-loop:
        # "human": "approve",   # or "confirm"
    }},
]


def handle(name: str, input: dict, ctx: dict) -> str:
    """Route tool calls to implementations."""
    if name == "example_tool":
        return json.dumps({{"result": f"Got: {{input['param']}}""}})

    return json.dumps({{"error": f"Unknown tool: {{name}}"}})
'''


@click.group()
def skill():
    """Manage mithai skills."""
    pass


@skill.command()
@click.argument("name")
@click.option("--dir", "skills_dir", default="./skills", help="Skills directory")
def create(name, skills_dir):
    """Create a new skill from template."""
    skill_dir = Path(skills_dir) / name
    if skill_dir.exists():
        raise click.ClickException(f"Skill directory already exists: {skill_dir}")

    skill_dir.mkdir(parents=True)
    (skill_dir / "prompt.md").write_text(SKILL_PROMPT_TEMPLATE)
    (skill_dir / "tools.py").write_text(SKILL_TOOLS_TEMPLATE.format(name=name))

    click.echo(f"Created skill '{name}' at {skill_dir}/")
    click.echo(f"  Edit {skill_dir}/prompt.md — describe the skill")
    click.echo(f"  Edit {skill_dir}/tools.py — define tools and handlers")


@skill.command("list")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def list_skills(config_path):
    """List all loaded skills and their tools."""
    try:
        config = load_config(config_path)
        skill_paths = get_skill_paths(config)
    except FileNotFoundError:
        # Fall back to default skills directory
        skill_paths = [Path("./skills")]

    skills = load_skills(skill_paths)

    if not skills:
        click.echo("No skills found.")
        return

    for name, sk in sorted(skills.items()):
        tool_names = [t.name for t in sk.tools]
        human_levels = [t.human or "auto" for t in sk.tools]
        click.echo(f"  {name}")
        for tool, level in zip(tool_names, human_levels):
            marker = f" [{level}]" if level != "auto" else ""
            click.echo(f"    - {tool}{marker}")


@skill.command()
@click.argument("name", required=False)
@click.option("--dir", "skills_dir", default="./skills", help="Skills directory")
def validate(name, skills_dir):
    """Validate skill(s) — check prompt.md, tools.py, and contract."""
    base = Path(skills_dir)

    if name:
        dirs = [base / name]
    else:
        if not base.exists():
            raise click.ClickException(f"Skills directory not found: {base}")
        dirs = sorted(d for d in base.iterdir() if d.is_dir() and not d.name.startswith("."))

    all_valid = True
    for skill_dir in dirs:
        errors = validate_skill(skill_dir)
        if errors:
            all_valid = False
            click.echo(f"  {skill_dir.name}: FAILED")
            for err in errors:
                click.echo(f"    - {err}")
        else:
            # Count tools for summary
            from mithai.core.skill_loader import _load_skill
            sk = _load_skill(skill_dir)
            tool_count = len(sk.tools) if sk else 0
            click.echo(f"  {skill_dir.name}: OK ({tool_count} tools)")

    if all_valid:
        click.echo("\nAll skills valid.")
    else:
        raise click.ClickException("Some skills have errors.")
