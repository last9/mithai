"""Discover and load skills from disk."""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """A single tool within a skill."""

    name: str
    description: str
    input_schema: dict
    human: str | None = None  # None = auto-execute, "approve", "confirm"


@dataclass
class Skill:
    """A loaded skill with its prompt, tools, and handler."""

    name: str
    prompt: str
    tools: list[ToolDefinition]
    handle: Callable
    source_dir: Path = field(repr=False)
    resolve_human: Callable | None = field(default=None, repr=False)
    mcp_tools: list[dict] = field(default_factory=list, repr=False)


def _load_skill(skill_dir: Path) -> Skill | None:
    """Load a single skill from a directory."""
    prompt_file = skill_dir / "prompt.md"
    tools_file = skill_dir / "tools.py"

    if not prompt_file.exists() or not tools_file.exists():
        logger.debug("Skipping %s: missing prompt.md or tools.py", skill_dir.name)
        return None

    prompt = prompt_file.read_text().strip()

    # Dynamic import of tools.py
    module_name = f"mithai_skill_{skill_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, tools_file)
    mod = importlib.util.module_from_spec(spec)

    # Add skill dir to path so skills can do relative imports
    skill_parent = str(skill_dir.parent)
    if skill_parent not in sys.path:
        sys.path.insert(0, skill_parent)

    spec.loader.exec_module(mod)

    raw_tools = getattr(mod, "TOOLS", None)
    handle_fn = getattr(mod, "handle", None)
    resolve_human_fn = getattr(mod, "resolve_human", None)
    raw_mcp_tools = getattr(mod, "MCP_TOOLS", [])

    if raw_tools is None:
        logger.warning("Skill %s: missing TOOLS export", skill_dir.name)
        return None
    if handle_fn is None:
        logger.warning("Skill %s: missing handle() function", skill_dir.name)
        return None

    tools = [
        ToolDefinition(
            name=t["name"],
            description=t["description"],
            input_schema=t["input_schema"],
            human=t.get("human"),
        )
        for t in raw_tools
    ]

    return Skill(
        name=skill_dir.name,
        prompt=prompt,
        tools=tools,
        handle=handle_fn,
        source_dir=skill_dir,
        resolve_human=resolve_human_fn,
        mcp_tools=raw_mcp_tools if isinstance(raw_mcp_tools, list) else [],
    )


def load_skills(skill_paths: list[Path]) -> dict[str, Skill]:
    """
    Discover and load all skills from the given directories.

    Skills are directories containing prompt.md + tools.py.
    Later paths override earlier ones if skill names collide.
    """
    skills: dict[str, Skill] = {}

    for base_path in skill_paths:
        if not base_path.exists():
            logger.debug("Skill path does not exist: %s", base_path)
            continue

        for skill_dir in sorted(base_path.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith((".", "_")):
                continue

            skill = _load_skill(skill_dir)
            if skill:
                if skill.name in skills:
                    logger.info(
                        "Skill %s from %s overrides %s",
                        skill.name,
                        skill.source_dir,
                        skills[skill.name].source_dir,
                    )
                skills[skill.name] = skill
                logger.info(
                    "Loaded skill: %s (%d tools)",
                    skill.name,
                    len(skill.tools),
                )

    return skills


def validate_skill(skill_dir: Path) -> list[str]:
    """
    Validate a skill directory. Returns list of errors (empty = valid).
    """
    errors = []
    prompt_file = skill_dir / "prompt.md"
    tools_file = skill_dir / "tools.py"

    if not skill_dir.is_dir():
        errors.append(f"Not a directory: {skill_dir}")
        return errors

    if not prompt_file.exists():
        errors.append("Missing prompt.md")
    elif not prompt_file.read_text().strip():
        errors.append("prompt.md is empty")

    if not tools_file.exists():
        errors.append("Missing tools.py")
        return errors

    try:
        spec = importlib.util.spec_from_file_location(f"validate_{skill_dir.name}", tools_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        errors.append(f"Failed to import tools.py: {e}")
        return errors

    raw_tools = getattr(mod, "TOOLS", None)
    if raw_tools is None:
        errors.append("tools.py does not export TOOLS")
    elif not isinstance(raw_tools, list):
        errors.append("TOOLS must be a list")
    else:
        for i, t in enumerate(raw_tools):
            if "name" not in t:
                errors.append(f"Tool {i}: missing 'name'")
            if "description" not in t:
                errors.append(f"Tool {i}: missing 'description'")
            if "input_schema" not in t:
                errors.append(f"Tool {i}: missing 'input_schema'")
            human = t.get("human")
            if human is not None and human not in ("approve", "confirm", "dynamic"):
                errors.append(f"Tool {i}: invalid human level '{human}' (must be approve, confirm, or dynamic)")

    if not hasattr(mod, "handle"):
        errors.append("tools.py does not export handle() function")
    elif not callable(mod.handle):
        errors.append("handle is not callable")

    # Validate MCP_TOOLS if present
    raw_mcp = getattr(mod, "MCP_TOOLS", None)
    if raw_mcp is not None:
        if not isinstance(raw_mcp, list):
            errors.append("MCP_TOOLS must be a list")
        else:
            for i, entry in enumerate(raw_mcp):
                if not isinstance(entry, dict):
                    errors.append(f"MCP_TOOLS[{i}]: must be a dict")
                    continue
                if "server" not in entry:
                    errors.append(f"MCP_TOOLS[{i}]: missing 'server'")
                if "tools" not in entry:
                    errors.append(f"MCP_TOOLS[{i}]: missing 'tools'")
                elif not isinstance(entry["tools"], list) and entry["tools"] != "*":
                    errors.append(f"MCP_TOOLS[{i}]: 'tools' must be a list or '*'")
                human = entry.get("human")
                if human is not None and human not in ("approve", "confirm"):
                    errors.append(f"MCP_TOOLS[{i}]: invalid human level '{human}'")

    return errors
