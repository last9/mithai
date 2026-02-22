"""Shared test fixtures."""

import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_skill_dir(tmp_path):
    """Create a temporary skill directory with a test skill."""
    skill_dir = tmp_path / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)

    (skill_dir / "prompt.md").write_text("You are a test skill. You can echo messages.")

    (skill_dir / "tools.py").write_text('''
import json

TOOLS = [
    {
        "name": "echo",
        "description": "Echo a message back.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "risky_action",
        "description": "A risky action that needs approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
            },
            "required": ["target"],
        },
        "human": "approve",
    },
]

def handle(name, input, ctx):
    if name == "echo":
        return json.dumps({"echoed": input["message"]})
    elif name == "risky_action":
        return json.dumps({"result": f"Acted on {input['target']}"})
    return json.dumps({"error": f"Unknown: {name}"})
''')

    return tmp_path / "skills"


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temporary config.yaml."""
    config = {
        "bot": {
            "name": "test-mithai",
            "system_prompt": "You are a test bot.",
        },
        "adapter": {"type": "cli"},
        "llm": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 256,
            "anthropic": {"api_key": "test-key"},
        },
        "skills": {
            "paths": [str(tmp_path / "skills")],
        },
    }

    import yaml
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path
