"""mithai chat — shortcut for running with CLI adapter."""

import click

from mithai.cli.style import banner, banner_small, console, kv, setup_logging
from mithai import get_version_string
from mithai.core.config import get_agents, get_default_agent_id, get_llm_config, load_config


@click.command()
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--agent", "agent_id", default=None, help="Agent ID to chat with (multi-agent mode)")
@click.option("--verbose", is_flag=True, help="Enable debug logging")
def chat(config_path, agent_id, verbose):
    """Start an interactive CLI chat session (for development/testing)."""
    setup_logging(verbose)

    config = load_config(config_path)
    agents_config = get_agents(config)

    from mithai.adapters.cli import CLIAdapter
    from mithai.core.engine import Engine
    from mithai.cli.run_cmd import _create_llm, _create_state, _create_memory_backend

    adapter = CLIAdapter()
    llm = _create_llm(config)
    state = _create_state(config)

    if agents_config:
        # Multi-agent mode — pick an agent to chat with
        if agent_id is None:
            agent_id = get_default_agent_id(config)
        if agent_id is None:
            agent_id = next(iter(agents_config))

        if agent_id not in agents_config:
            available = ", ".join(sorted(agents_config.keys()))
            raise click.ClickException(
                f"Agent '{agent_id}' not found. Available: {available}"
            )

        agent_def = agents_config[agent_id]

        # Load filtered skills
        from mithai.core.config import get_agent_config, get_skill_paths
        from mithai.core.skill_loader import filter_skills, load_skills

        skill_paths = get_skill_paths(config)
        all_skills = load_skills(skill_paths)

        allowed = agent_def.get("skills", {}).get("allowed")
        if allowed:
            agent_skills = filter_skills(all_skills, allowed)
        else:
            agent_skills = dict(all_skills)

        # Agent-specific memory
        memory_path = agent_def.get("memory", {}).get("path")
        if memory_path:
            from mithai.memory.filesystem import FilesystemMemoryBackend
            memory = FilesystemMemoryBackend(memory_path)
        else:
            memory = _create_memory_backend(config)

        agent_config = get_agent_config(config, agent_id)

        engine = Engine(
            config=agent_config,
            llm=llm,
            state=state,
            memory=memory,
            agent_id=agent_id,
            skills=agent_skills,
        )

        banner(get_version_string())
        banner_small(f"chat · {agent_id}")
        agent_name = agent_def.get("name", agent_id)
        kv("Agent", f"{agent_name} [muted]({agent_id})[/]", indent=4)
    else:
        # Single-agent mode
        memory = _create_memory_backend(config)
        engine = Engine(config=config, llm=llm, state=state, memory=memory)
        banner(get_version_string())
        banner_small("chat")

    llm_config = get_llm_config(config)
    kv("LLM", f"{llm_config.get('provider', '?')} / {llm_config.get('model', '?')}", indent=4)
    kv("Skills", f"{len(engine._skills)} loaded", indent=4)
    console.print()

    adapter.set_engine(engine)
    try:
        adapter.start(on_message=engine.handle)
    except KeyboardInterrupt:
        console.print("\n  [muted]Bye![/]")
    finally:
        adapter.stop()
