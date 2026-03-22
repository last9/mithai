"""mithai agent — create, inspect, and validate multi-agent configuration."""

import re
from pathlib import Path

import click
from rich.table import Table

from mithai.cli.style import banner_small, console, fail, info, kv, ok, section, warn
from mithai.core.config import (
    get_agents,
    get_default_agent_id,
    get_skill_paths,
    load_config,
)
from mithai.core.skill_loader import filter_skills, load_skills


# ── Agent templates ──────────────────────────────────────────────────────────

AGENT_SYSTEM_PROMPT_TEMPLATE = (
    "You are {name}, a helpful operations assistant.\n"
    "You have access to skills that let you interact with infrastructure.\n"
    "Be concise and precise. Explain before acting.\n"
)

AGENT_ENV_TEMPLATE = (
    "# {agent_id} agent credentials\n"
    "# Slack bot — create at https://api.slack.com/apps\n"
    "{prefix}_SLACK_BOT_TOKEN=xoxb-...\n"
    "{prefix}_SLACK_APP_TOKEN=xapp-...\n"
)


def _build_config_snippet(agent_id: str, name: str, skills_csv: str,
                          agent_dir: str, prefix: str) -> str:
    """Build the YAML snippet for an agent config entry."""
    prompt_indented = "\n".join(
        f"      {line}" for line in AGENT_SYSTEM_PROMPT_TEMPLATE.format(name=name).strip().split("\n")
    )
    return (
        f"\n  {agent_id}:\n"
        f"    name: \"{name}\"\n"
        f"    system_prompt: |\n"
        f"{prompt_indented}\n"
        f"    skills:\n"
        f"      allowed: [{skills_csv}]\n"
        f"    memory:\n"
        f"      path: ./{agent_dir}/memory\n"
        f"    adapter:\n"
        f"      slack:\n"
        f"        bot_token: ${{{prefix}_SLACK_BOT_TOKEN}}\n"
        f"        app_token: ${{{prefix}_SLACK_APP_TOKEN}}\n"
    )


@click.group()
def agent():
    """Create, inspect, and validate multi-agent configuration."""
    pass


@agent.command("create")
@click.argument("agent_id")
@click.option("--name", default=None, help="Display name (defaults to agent_id titlecased)")
@click.option("--skills", "skills_csv", default="shell,memory,sessions",
              help="Comma-separated skill allowlist")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--dir", "agent_dir", default=None,
              help="Agent directory (defaults to ./agents/<agent_id>)")
def create_agent(agent_id, name, skills_csv, config_path, agent_dir):
    """Create a new agent with directory structure and config entry.

    Generates:

    \b
      agents/<agent_id>/
        memory/           — persistent memory for this agent
        .env.example      — credential template
        system_prompt.md  — editable system prompt

    Also appends the agent config to config.yaml and runs validation.
    """
    # Validate agent_id
    if not re.match(r'^[a-z][a-z0-9_]*$', agent_id):
        raise click.ClickException(
            f"Invalid agent ID '{agent_id}'. Use lowercase letters, numbers, and underscores (e.g. 'devops', 'sre_bot')."
        )

    agent_path = Path(agent_dir) if agent_dir else Path("agents") / agent_id

    display_name = name or agent_id.replace("_", " ").title()
    prefix = agent_id.upper()
    skills_list = [s.strip() for s in skills_csv.split(",") if s.strip()]

    banner_small(f"agent · create {agent_id}")
    console.print()

    # ── Create directory structure (atomic — no pre-check race) ──
    try:
        agent_path.mkdir(parents=True)
    except FileExistsError:
        raise click.ClickException(f"Directory already exists: {agent_path}")
    (agent_path / "memory").mkdir()

    # System prompt
    prompt_path = agent_path / "system_prompt.md"
    prompt_path.write_text(AGENT_SYSTEM_PROMPT_TEMPLATE.format(name=display_name))
    ok(f"Created [white]{prompt_path}[/]")

    # .env.example
    env_path = agent_path / ".env.example"
    env_path.write_text(AGENT_ENV_TEMPLATE.format(agent_id=agent_id, prefix=prefix))
    ok(f"Created [white]{env_path}[/]")

    # ── Update config.yaml ──
    config_file = Path(config_path)
    if config_file.exists():
        config_text = config_file.read_text()
        config_lines = config_text.split("\n")

        snippet = _build_config_snippet(
            agent_id=agent_id,
            name=display_name,
            skills_csv=", ".join(skills_list),
            agent_dir=str(agent_path),
            prefix=prefix,
        )

        # Check for an active (non-commented) agents: line
        has_agents_section = any(line.rstrip() == "agents:" for line in config_lines)
        if has_agents_section:
            config_text = _append_to_agents_section(config_lines, snippet)
            ok(f"Added [bright_cyan]{agent_id}[/] to existing agents section")
        else:
            # Add agents section before state: or at end of file
            agents_block = f"\nagents:{snippet}\n  default_agent: {agent_id}\n"
            if "\nstate:" in config_text:
                config_text = config_text.replace("\nstate:", f"{agents_block}\nstate:", 1)
            else:
                config_text += agents_block
            ok(f"Added [bright_cyan]agents:[/] section with [bright_cyan]{agent_id}[/]")

        config_file.write_text(config_text)
        ok(f"Updated [white]{config_file}[/]")
    else:
        warn(f"Config file not found: {config_file} — skipping config update")

    console.print()

    # ── Summary ──
    section("Next steps")
    console.print(f"    1. Copy [white]{env_path}[/] to [white].env[/] and fill in your Slack tokens")
    console.print(f"    2. Edit [white]{prompt_path}[/] to customize the system prompt")
    console.print("    3. Run [white]mithai agent validate[/] to check the configuration")
    console.print("    4. Run [white]mithai run[/] to start all agents")
    console.print()


def _append_to_agents_section(lines: list[str], snippet: str) -> str:
    """Append an agent snippet to an existing agents: section.

    Accepts pre-split lines (to avoid splitting the config text twice).
    Finds the end of the agents block (next non-indented line or EOF) and
    inserts the snippet just before it.
    """
    lines = list(lines)  # copy to avoid mutating caller's list
    in_agents = False
    insert_idx = len(lines)

    for i, line in enumerate(lines):
        if line.rstrip() == "agents:":
            in_agents = True
            continue
        if in_agents:
            # A non-empty line that isn't indented means a new top-level key
            if line and not line[0].isspace() and line[0] != "#":
                insert_idx = i
                break

    # Insert before the next top-level key (or at EOF)
    snippet_lines = snippet.rstrip("\n").split("\n")
    for j, sl in enumerate(snippet_lines):
        lines.insert(insert_idx + j, sl)

    return "\n".join(lines)


@agent.command("list")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def list_agents(config_path):
    """List all configured agents."""
    config = load_config(config_path)
    agents = get_agents(config)

    if not agents:
        banner_small("agents")
        info("Single-agent mode — no agents configured.")
        console.print(
            "    Add an [bright_cyan]agents:[/] section to your config.yaml "
            "to enable multi-agent mode."
        )
        console.print()
        return

    default_id = get_default_agent_id(config)

    banner_small("agents")
    console.print()

    table = Table(
        show_header=True, header_style="bold bright_white",
        border_style="dim", padding=(0, 1), show_edge=False,
    )
    table.add_column("Agent", style="bright_cyan")
    table.add_column("Name", style="white")
    table.add_column("Skills", style="white", justify="right")
    table.add_column("Adapter", style="green")
    table.add_column("Memory", style="dim")
    table.add_column("", style="bright_magenta")  # default marker

    # Load skills to resolve counts
    skill_paths = get_skill_paths(config)
    all_skills = load_skills(skill_paths)

    for agent_id, agent_def in agents.items():
        name = agent_def.get("name", agent_id)

        # Resolve skill count
        allowed = agent_def.get("skills", {}).get("allowed")
        if allowed:
            agent_skills = filter_skills(all_skills, allowed)
        else:
            agent_skills = all_skills
        skill_count = str(len(agent_skills))

        # Adapter info
        adapter_cfg = agent_def.get("adapter", {})
        adapter_types = list(adapter_cfg.keys())
        adapter_label = ", ".join(adapter_types) if adapter_types else "[muted]none[/]"

        # Memory path
        memory_path = agent_def.get("memory", {}).get("path", "")
        memory_label = memory_path if memory_path else "[muted]shared[/]"

        # Default marker
        marker = "default" if agent_id == default_id else ""

        table.add_row(agent_id, name, skill_count, adapter_label, memory_label, marker)

    console.print(table)
    console.print()
    console.print(f"  [muted]{len(agents)} agent(s) configured[/]")
    console.print()


@agent.command("info")
@click.argument("agent_id")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def agent_info(agent_id, config_path):
    """Show detailed info about a specific agent."""
    config = load_config(config_path)
    agents = get_agents(config)

    if not agents:
        fail("Single-agent mode — no agents configured.")
        return

    if agent_id not in agents:
        fail(f"Agent [bright_cyan]{agent_id}[/] not found.")
        available = ", ".join(sorted(agents.keys()))
        console.print(f"    Available: [bright_cyan]{available}[/]")
        return

    agent_def = agents[agent_id]
    default_id = get_default_agent_id(config)

    banner_small(f"agent · {agent_id}")
    console.print()

    # Basic info
    name = agent_def.get("name", agent_id)
    kv("Name", name, indent=4)
    if agent_id == default_id:
        kv("Default", "[bright_magenta]yes[/]", indent=4)

    # System prompt (truncated)
    prompt = agent_def.get("system_prompt", "").strip()
    if prompt:
        preview = prompt[:120].replace("\n", " ")
        if len(prompt) > 120:
            preview += "..."
        kv("Prompt", f"[muted]{preview}[/]", indent=4)

    # Skills
    section("Skills")
    allowed = agent_def.get("skills", {}).get("allowed")
    skill_paths = get_skill_paths(config)
    all_skills = load_skills(skill_paths)

    if allowed:
        agent_skills = filter_skills(all_skills, allowed)
        missing = set(allowed) - set(agent_skills.keys())

        skill_table = Table(
            show_header=True, header_style="bold bright_white",
            border_style="dim", padding=(0, 1), show_edge=False,
        )
        skill_table.add_column("Skill", style="bright_cyan")
        skill_table.add_column("Tools", style="white", justify="right")
        skill_table.add_column("Status", style="green")

        for skill_name in sorted(allowed):
            if skill_name in agent_skills:
                sk = agent_skills[skill_name]
                tool_count = len(sk.tools)
                mcp_count = sum(
                    len(e.get("tools", [])) if isinstance(e.get("tools"), list)
                    else 0
                    for e in sk.mcp_tools
                )
                mcp_wildcard = any(e.get("tools") == "*" for e in sk.mcp_tools)
                count_label = str(tool_count)
                if mcp_count:
                    count_label += f" + {mcp_count} MCP"
                if mcp_wildcard:
                    count_label += " + MCP(*)"
                skill_table.add_row(skill_name, count_label, "[green]loaded[/]")
            else:
                skill_table.add_row(skill_name, "—", "[red]not found[/]")

        console.print(skill_table)

        if missing:
            console.print()
            for m in sorted(missing):
                warn(f"Skill [bright_cyan]{m}[/] is in allowlist but not found")
    else:
        info(f"All {len(all_skills)} skills (no allowlist filter)")

    # Adapter
    section("Adapter")
    adapter_cfg = agent_def.get("adapter", {})
    if adapter_cfg:
        for adapter_type, type_cfg in adapter_cfg.items():
            kv("Type", adapter_type, indent=4)
            if isinstance(type_cfg, dict):
                for k, v in type_cfg.items():
                    # Mask tokens
                    display_v = str(v)
                    if "token" in k.lower() and isinstance(v, str):
                        if v.startswith("${"):
                            display_v = f"[muted]{v}[/]"
                        else:
                            display_v = "[muted]****[/]"
                    kv(k, display_v, indent=6)
    else:
        warn("No adapter configured — this agent won't receive messages")

    # Memory
    section("Memory")
    memory_path = agent_def.get("memory", {}).get("path")
    if memory_path:
        kv("Backend", "filesystem (agent-specific)", indent=4)
        kv("Path", memory_path, indent=4)
    else:
        kv("Backend", "shared (global config)", indent=4)

    console.print()


@agent.command("validate")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def validate_agents(config_path):
    """Validate all agent configurations."""
    config = load_config(config_path)
    agents = get_agents(config)

    if not agents:
        info("Single-agent mode — nothing to validate.")
        return

    banner_small("agent · validate")
    console.print()

    skill_paths = get_skill_paths(config)
    all_skills = load_skills(skill_paths)
    issues = 0

    for agent_id, agent_def in agents.items():
        console.print(f"  [bright_cyan]{agent_id}[/]")

        # Check skills
        allowed = agent_def.get("skills", {}).get("allowed")
        if allowed:
            agent_skills = filter_skills(all_skills, allowed)
            missing = set(allowed) - set(agent_skills.keys())
            if missing:
                for m in sorted(missing):
                    fail(f"  Skill [white]{m}[/] in allowlist but not found")
                    issues += 1
            else:
                ok(f"  Skills: {len(agent_skills)} loaded")
        else:
            ok(f"  Skills: all {len(all_skills)} (no filter)")

        # Check adapter
        adapter_cfg = agent_def.get("adapter", {})
        if not adapter_cfg:
            warn("  No adapter — agent won't receive messages")
            issues += 1
        else:
            for adapter_type in adapter_cfg:
                ok(f"  Adapter: {adapter_type}")

        # Check memory
        memory_path = agent_def.get("memory", {}).get("path")
        if memory_path:
            p = Path(memory_path)
            if p.exists():
                ok(f"  Memory: {memory_path}")
            else:
                warn(f"  Memory dir doesn't exist: {memory_path} (will be created on first run)")
        else:
            ok("  Memory: shared")

        console.print()

    if issues == 0:
        ok("All agents valid.")
    else:
        warn(f"{issues} issue(s) found.")
    console.print()
