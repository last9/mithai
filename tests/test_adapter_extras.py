"""Optional adapters must declare every third-party module they import."""

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Import names that differ from their distribution names.
DISTRIBUTIONS = {"telegram": "python-telegram-bot"}


def imported_distributions(path):
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules.add(node.module.split(".")[0])
    third_party = modules - set(sys.stdlib_module_names) - {"mithai"}
    return {DISTRIBUTIONS.get(module, module) for module in third_party}


def requirement_names(requirements):
    return {re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0] for requirement in requirements}


def test_telegram_extra_declares_adapter_imports():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    declared = requirement_names(project["dependencies"] + project["optional-dependencies"]["telegram"])
    imported = imported_distributions(ROOT / "src/mithai/adapters/telegram.py")
    assert imported <= declared, f"undeclared: {sorted(imported - declared)}"
