"""mithai skill — manage skills (create, list, install, remove, validate)."""

import importlib.util
import shutil
import subprocess

import click
from pathlib import Path

from mithai import get_bundled_path
from mithai.core.config import get_skill_paths, load_config
from mithai.core.skill_loader import load_skills, validate_skill


# Skills that ship inside the binary and are always loaded
CORE_SKILLS = {"shell", "memory", "sessions", "http_checker"}

# Runtime dependency checks for optional skills.
# Each entry maps a skill name to a list of checks.
# Each check is: {"command": "...", "label": "...", "install_hint": "..."}
SKILL_DEPS = {
    "github": [
        {
            "command": "gh --version",
            "label": "GitHub CLI (gh)",
            "install_hint": "Install gh: https://cli.github.com/ or `brew install gh`",
        },
    ],
    "kubernetes": [
        {
            "command": "kubectl version --client -o json",
            "label": "kubectl",
            "install_hint": "Install kubectl: https://kubernetes.io/docs/tasks/tools/",
        },
    ],
    "aws": [
        {
            "command": "aws --version",
            "label": "AWS CLI",
            "install_hint": "Install AWS CLI: https://aws.amazon.com/cli/",
        },
    ],
}


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


def _user_skills_dir() -> Path:
    """Return the user's installed skills directory (~/.mithai/skills/)."""
    return Path.home() / ".mithai" / "skills"


def _optional_skills_source() -> Path:
    """Return the path where optional skills are stored in the bundle."""
    return get_bundled_path() / "_optional_skills"


def _available_optional_skills() -> dict[str, Path]:
    """Discover optional skills available for installation.

    Looks in _optional_skills/ (binary mode) and ./skills/ (source mode).
    Returns a dict of skill_name -> source_path.
    """
    available = {}

    # Binary mode: _optional_skills/ inside the PyInstaller bundle
    opt_dir = _optional_skills_source()
    if opt_dir.exists():
        for d in sorted(opt_dir.iterdir()):
            if d.is_dir() and not d.name.startswith((".", "_")):
                if (d / "tools.py").exists() and (d / "prompt.md").exists():
                    available[d.name] = d

    # Source mode fallback: ./skills/ directory (non-core skills)
    if not available:
        source_dir = Path.cwd() / "skills"
        if source_dir.exists():
            for d in sorted(source_dir.iterdir()):
                if d.is_dir() and d.name not in CORE_SKILLS and not d.name.startswith((".", "_")):
                    if (d / "tools.py").exists() and (d / "prompt.md").exists():
                        available[d.name] = d

    return available


def _check_deps(skill_name: str) -> list[dict]:
    """Check runtime dependencies for a skill. Returns list of failed checks."""
    checks = SKILL_DEPS.get(skill_name, [])
    failed = []
    for check in checks:
        try:
            subprocess.run(
                check["command"],
                shell=True,
                capture_output=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, FileNotFoundError):
            failed.append(check)
    return failed


def _count_tools(skill_dir: Path) -> int:
    """Count native tools in a skill without fully loading it."""
    tools_file = skill_dir / "tools.py"
    if not tools_file.exists():
        return 0
    try:
        spec = importlib.util.spec_from_file_location(f"_count_{skill_dir.name}", tools_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        native = len(getattr(mod, "TOOLS", []))
        mcp = sum(
            len(e.get("tools", [])) if isinstance(e.get("tools"), list) else 0
            for e in getattr(mod, "MCP_TOOLS", [])
        )
        mcp_wildcard = any(
            e.get("tools") == "*" for e in getattr(mod, "MCP_TOOLS", [])
        )
        label = f"{native} native"
        if mcp > 0:
            label += f" + {mcp} MCP"
        if mcp_wildcard:
            label += " + MCP (*)"
        return label
    except Exception:
        return "?"


@click.group()
def skill():
    """Manage mithai skills."""
    pass


@skill.command()
@click.argument("name")
@click.option("--dir", "skills_dir", default=None, help="Install to this directory instead of ~/.mithai/skills/")
def install(name, skills_dir):
    """Install an optional skill."""
    target_dir = Path(skills_dir) if skills_dir else _user_skills_dir()
    target = target_dir / name

    if target.exists():
        raise click.ClickException(
            f"Skill '{name}' is already installed at {target}. "
            f"Run `mithai skill remove {name}` first to reinstall."
        )

    # Find the skill source
    available = _available_optional_skills()
    if name not in available:
        available_names = ", ".join(sorted(available.keys())) if available else "(none found)"
        raise click.ClickException(
            f"Skill '{name}' not found. Available: {available_names}"
        )

    if name in CORE_SKILLS:
        click.echo(f"'{name}' is a core skill — already bundled and active.")
        return

    source = available[name]

    # Check runtime dependencies
    failed_deps = _check_deps(name)
    if failed_deps:
        click.echo(f"Dependency check for '{name}':")
        for dep in failed_deps:
            click.echo(f"  ✗ {dep['label']} — not found")
            click.echo(f"    {dep['install_hint']}")
        if not click.confirm("\nInstall anyway (skill may not work without dependencies)?"):
            raise click.Abort()

    # Copy skill files
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )

    # Count tools for summary
    tool_label = _count_tools(target)
    click.echo(f"✓ Installed skill '{name}' ({tool_label} tools)")
    click.echo(f"  Location: {target}")

    # Show dep status if all passed
    deps = SKILL_DEPS.get(name, [])
    if deps and not failed_deps:
        for dep in deps:
            click.echo(f"  ✓ {dep['label']}")


@skill.command()
@click.argument("name")
@click.option("--dir", "skills_dir", default=None, help="Remove from this directory instead of ~/.mithai/skills/")
def remove(name, skills_dir):
    """Remove an installed optional skill."""
    target_dir = Path(skills_dir) if skills_dir else _user_skills_dir()
    target = target_dir / name

    if name in CORE_SKILLS:
        raise click.ClickException(f"'{name}' is a core skill and cannot be removed.")

    if not target.exists():
        raise click.ClickException(f"Skill '{name}' is not installed at {target}.")

    shutil.rmtree(target)
    click.echo(f"✓ Removed skill '{name}' from {target}")


@skill.command()
@click.argument("name")
@click.option("--dir", "skills_dir", default=None, help="Upgrade in this directory instead of ~/.mithai/skills/")
def upgrade(name, skills_dir):
    """Upgrade an installed optional skill to the latest bundled version."""
    target_dir = Path(skills_dir) if skills_dir else _user_skills_dir()
    target = target_dir / name

    if not target.exists():
        raise click.ClickException(
            f"Skill '{name}' is not installed. Run `mithai skill install {name}` first."
        )

    available = _available_optional_skills()
    if name not in available:
        raise click.ClickException(f"Skill '{name}' not found in bundled skills.")

    source = available[name]

    # Remove old, copy new
    shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )

    tool_label = _count_tools(target)
    click.echo(f"✓ Upgraded skill '{name}' ({tool_label} tools)")


@skill.command("list")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def list_skills(config_path):
    """List all skills — core (bundled), installed, and available."""
    # Load active skills from config
    try:
        config = load_config(config_path)
        skill_paths = get_skill_paths(config)
    except (FileNotFoundError, ValueError):
        skill_paths = [get_bundled_path() / "skills"]
        user_dir = _user_skills_dir()
        if user_dir.exists():
            skill_paths.append(user_dir)

    loaded = load_skills(skill_paths)
    available = _available_optional_skills()
    user_dir = _user_skills_dir()

    # Core skills
    click.echo("Core skills (bundled):")
    for name in sorted(CORE_SKILLS):
        if name in loaded:
            sk = loaded[name]
            click.echo(f"  {name}")
            for t in sk.tools:
                marker = f" [{t.human}]" if t.human else ""
                click.echo(f"    - {t.name}{marker}")
        else:
            click.echo(f"  {name} (not loaded)")

    # Installed optional skills
    installed = set()
    if user_dir.exists():
        installed = {
            d.name for d in user_dir.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_"))
        }

    if installed:
        click.echo("\nInstalled skills:")
        for name in sorted(installed):
            sk = loaded.get(name)
            if sk:
                tools = sk.tools
                click.echo(f"  {name}")
                for t in tools:
                    marker = f" [{t.human}]" if t.human else ""
                    click.echo(f"    - {t.name}{marker}")
            else:
                click.echo(f"  {name} (not loaded — check config)")

    # Active optional skills loaded from config paths (e.g., ./skills/)
    active_from_config = {
        name for name in loaded
        if name not in CORE_SKILLS and name not in installed
    }
    if active_from_config:
        click.echo("\nActive skills (from config paths):")
        for name in sorted(active_from_config):
            sk = loaded[name]
            click.echo(f"  {name}")
            for t in sk.tools:
                marker = f" [{t.human}]" if t.human else ""
                click.echo(f"    - {t.name}{marker}")

    # Available for install (not yet installed or active)
    not_installed = set(available.keys()) - installed - CORE_SKILLS - active_from_config

    if not_installed:
        click.echo("\nAvailable to install:")
        for name in sorted(not_installed):
            tool_label = _count_tools(available[name])
            click.echo(f"  {name} ({tool_label} tools) — `mithai skill install {name}`")


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
            from mithai.core.skill_loader import _load_skill
            sk = _load_skill(skill_dir)
            tool_count = len(sk.tools) if sk else 0
            click.echo(f"  {skill_dir.name}: OK ({tool_count} tools)")

    if all_valid:
        click.echo("\nAll skills valid.")
    else:
        raise click.ClickException("Some skills have errors.")
