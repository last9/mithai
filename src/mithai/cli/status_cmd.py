"""mithai status — show system status at a glance."""

import click

from mithai.cli.style import banner_small, console, kv, ok, section
from mithai.core.config import (
    get_adapter_types,
    get_agents,
    get_llm_config,
    get_mcp_config,
    get_skill_paths,
    load_config,
)


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
def status(config_path):
    """Show system status — config, skills, sessions, memory."""
    config = load_config(config_path)

    banner_small("status")

    # Mode
    agents = get_agents(config)
    if agents:
        kv("Mode", f"multi-agent ({len(agents)} agents)", indent=4)
    else:
        kv("Mode", "single-agent", indent=4)

    # LLM
    section("LLM")
    llm_config = get_llm_config(config)
    kv("Provider", llm_config.get("provider", "?"), indent=4)
    kv("Model", llm_config.get("model", "?"), indent=4)
    kv("Max tokens", str(llm_config.get("max_tokens", 4096)), indent=4)

    # Adapters
    section("Adapters")
    if agents:
        for agent_id, agent_def in agents.items():
            adapter_cfg = agent_def.get("adapter", {})
            adapter_types = list(adapter_cfg.keys()) if adapter_cfg else ["none"]
            kv(agent_id, ", ".join(adapter_types), indent=4)
    else:
        adapter_types = get_adapter_types(config)
        for at in adapter_types:
            ok(f"  {at}")

    # Skills
    section("Skills")
    from mithai.core.skill_loader import load_skills

    skill_paths = get_skill_paths(config)
    all_skills = load_skills(skill_paths)
    total_tools = sum(len(s.tools) for s in all_skills.values())
    kv("Loaded", f"{len(all_skills)} skills, {total_tools} native tools", indent=4)
    for name in sorted(all_skills.keys()):
        console.print(f"      [bright_cyan]{name}[/]", highlight=False)

    # MCP
    mcp_config = get_mcp_config(config)
    if mcp_config:
        section("MCP Servers")
        for name, cfg in mcp_config.items():
            transport = cfg.get("transport", "stdio")
            url = cfg.get("url", cfg.get("command", "?"))
            kv(name, f"{transport} → {url}", indent=4)

    # Sessions
    section("Sessions")
    from mithai.cli.run_cmd import _create_state
    from mithai.core.session import SessionManager

    state = _create_state(config)
    mgr = SessionManager(state)
    sessions = mgr.list_sessions(limit=100)
    kv("Total", str(len(sessions)), indent=4)
    if sessions:
        latest = sessions[0]
        kv("Latest", f"{latest['session_id']} ({latest.get('updated_at', '?')[:16].replace('T', ' ')})", indent=4)

    # Memory
    section("Memory")
    learning = config.get("learning", {})
    if learning.get("enabled", True):
        kv("Learning", "enabled", indent=4)
        kv("Reflection", "on" if learning.get("reflection") else "off", indent=4)
    else:
        kv("Learning", "disabled", indent=4)

    memory_config = learning.get("memory", {})
    backend = memory_config.get("backend", "filesystem")
    kv("Backend", backend, indent=4)

    console.print()
