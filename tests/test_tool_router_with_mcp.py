"""Tests for ToolRouter with MCP tools integrated."""

import json
import logging
from unittest.mock import MagicMock

from mithai.core.skill_loader import Skill, ToolDefinition
from mithai.core.tool_router import ToolRouter


def _make_mock_mcp_manager(tools_by_server=None):
    """Create a mock MCPManager with discoverable tools."""
    mgr = MagicMock()
    tools_by_server = tools_by_server or {}

    def discover_tools(server_name):
        return list(tools_by_server.get(server_name, []))

    mgr.discover_tools = MagicMock(side_effect=discover_tools)
    mgr.server_names = MagicMock(return_value=list(tools_by_server.keys()))
    mgr.call_tool = MagicMock(return_value='{"mcp_result": "ok"}')
    return mgr


def _make_skill_with_mcp(name, mcp_tools):
    """Create a Skill with MCP_TOOLS declaration."""
    return Skill(
        name=name,
        prompt="test skill",
        tools=[
            ToolDefinition(
                name="local_tool",
                description="A local tool",
                input_schema={"type": "object"},
            ),
        ],
        handle=lambda n, i, c: json.dumps({"local": n}),
        source_dir=MagicMock(),
        mcp_tools=mcp_tools,
    )


class TestToolRouterWithMCP:
    """Test ToolRouter when MCP tools are present."""

    def test_mcp_tools_appear_in_collect(self):
        """MCP tools are included in collect_tools_for_llm."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="searchIssues", description="Search issues", input_schema={}),
                ToolDefinition(name="createIssue", description="Create issue", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["searchIssues", "createIssue"], "human": "approve"},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        names = [t["name"] for t in tools]
        assert "triage__local_tool" in names
        assert "triage__searchIssues" in names
        assert "triage__createIssue" in names

    def test_direct_mcp_tools_appear_in_collect(self):
        """Configured MCP server tools are exposed without MCP_TOOLS declarations."""
        mcp = _make_mock_mcp_manager({
            "last9": [
                ToolDefinition(
                    name="prometheus_instant_query",
                    description="Run PromQL",
                    input_schema={"type": "object"},
                ),
            ],
        })
        skill = _make_skill_with_mcp("triage", [])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        names = [t["name"] for t in tools]
        assert "triage__local_tool" in names
        assert "mcp__last9__prometheus_instant_query" in names

        direct = next(t for t in tools if t["name"] == "mcp__last9__prometheus_instant_query")
        assert direct["description"].startswith("[mcp__last9]")

    def test_route_direct_mcp_tool(self):
        """Direct MCP tools route to the configured MCP server."""
        mcp = _make_mock_mcp_manager({
            "last9": [
                ToolDefinition(name="prometheus_labels", description="Labels", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        result = router.route("mcp__last9__prometheus_labels", {"lookback_minutes": 60}, {})

        mcp.call_tool.assert_called_once_with(
            "last9",
            "prometheus_labels",
            {"lookback_minutes": 60},
        )
        assert "mcp_result" in result

    def test_mcp_tools_filtered_to_requested(self):
        """Only requested MCP tools are included, not all from server."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="searchIssues", description="Search", input_schema={}),
                ToolDefinition(name="createIssue", description="Create", input_schema={}),
                ToolDefinition(name="deleteIssue", description="Delete", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["searchIssues"]},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        names = [t["name"] for t in tools]
        assert "triage__searchIssues" in names
        assert "triage__createIssue" not in names
        assert "triage__deleteIssue" not in names

    def test_mcp_tools_wildcard(self):
        """tools='*' includes all tools from the server."""
        mcp = _make_mock_mcp_manager({
            "github": [
                ToolDefinition(name="listRepos", description="List repos", input_schema={}),
                ToolDefinition(name="createPR", description="Create PR", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("devops", [
            {"server": "github", "tools": "*"},
        ])

        router = ToolRouter({"devops": skill}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        names = [t["name"] for t in tools]
        assert "devops__listRepos" in names
        assert "devops__createPR" in names

    def test_mcp_tools_wildcard_list(self):
        """tools=['*'] is treated like tools='*'."""
        mcp = _make_mock_mcp_manager({
            "github": [
                ToolDefinition(name="listRepos", description="List repos", input_schema={}),
                ToolDefinition(name="createPR", description="Create PR", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("devops", [
            {"server": "github", "tools": ["*"]},
        ])

        router = ToolRouter({"devops": skill}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        names = [t["name"] for t in tools]
        assert "devops__listRepos" in names
        assert "devops__createPR" in names

    def test_available_tool_names_matches_indexes(self):
        """The router exposes a single source of truth for dispatchable tools."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="search", description="Search", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["search"]},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)

        assert router.available_tool_names() == {
            "triage__local_tool",
            "triage__search",
            "mcp__linear__search",
        }

    def test_is_mcp_tool(self):
        """is_mcp_tool distinguishes MCP from native tools."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="search", description="Search", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["search"]},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)

        assert router.is_mcp_tool("triage__search") is True
        assert router.is_mcp_tool("mcp__linear__search") is True
        assert router.is_mcp_tool("triage__local_tool") is False

    def test_route_mcp_tool(self):
        """MCP tools are routed to mcp_manager.call_tool."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="search", description="Search", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["search"]},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        result = router.route("triage__search", {"query": "bugs"}, {})

        mcp.call_tool.assert_called_once_with("linear", "search", {"query": "bugs"})
        assert "mcp_result" in result

    def test_route_native_tool_still_works(self):
        """Native skill tools still route to skill.handle."""
        mcp = _make_mock_mcp_manager({})
        skill = _make_skill_with_mcp("triage", [])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        result = router.route("triage__local_tool", {}, {})

        data = json.loads(result)
        assert data["local"] == "local_tool"
        mcp.call_tool.assert_not_called()

    def test_get_definition_mcp_tool(self):
        """get_definition works for MCP tools with correct human level."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="search", description="Search issues", input_schema={"type": "object"}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["search"], "human": "approve"},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        defn = router.get_definition("triage__search")

        assert defn is not None
        assert defn.name == "search"
        assert defn.human == "approve"

    def test_mcp_human_overrides(self):
        """Per-tool human overrides from MCP_TOOLS are applied."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="searchIssues", description="Search", input_schema={}),
                ToolDefinition(name="createIssue", description="Create", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {
                "server": "linear",
                "tools": ["searchIssues", "createIssue"],
                "human": "approve",
                "human_overrides": {"searchIssues": None},
            },
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)

        search_def = router.get_definition("triage__searchIssues")
        create_def = router.get_definition("triage__createIssue")

        assert search_def.human is None  # Override to auto-execute
        assert create_def.human == "approve"  # Default from MCP_TOOLS

    def test_native_tool_takes_precedence_over_mcp(self):
        """If a native tool collides with MCP tool name, native wins."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="local_tool", description="Collision!", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["local_tool"]},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)

        # Should NOT be in MCP index — native takes precedence
        assert router.is_mcp_tool("triage__local_tool") is False

        # Route goes to native handle, not MCP
        result = router.route("triage__local_tool", {}, {})
        data = json.loads(result)
        assert data["local"] == "local_tool"
        mcp.call_tool.assert_not_called()

    def test_description_includes_skill_name(self):
        """MCP tool descriptions are prefixed with skill name."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="search", description="Search issues", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["search"]},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        mcp_tool = next(t for t in tools if t["name"] == "triage__search")
        assert mcp_tool["description"].startswith("[triage]")

    def test_no_mcp_manager_ignores_mcp_tools(self):
        """Without MCPManager, MCP_TOOLS are silently ignored."""
        skill = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["search"]},
        ])

        router = ToolRouter({"triage": skill}, mcp_manager=None)
        tools = router.collect_tools_for_llm()

        names = [t["name"] for t in tools]
        assert "triage__local_tool" in names
        assert "triage__search" not in names

    def test_disconnected_server_logs_warning(self):
        """Skills referencing a server with no tools get a warning, not crash."""
        mcp = _make_mock_mcp_manager({})  # No tools discovered
        skill = _make_skill_with_mcp("triage", [
            {"server": "offline_server", "tools": ["search"]},
        ])

        # Should not raise
        router = ToolRouter({"triage": skill}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        # Only the native tool should be present
        names = [t["name"] for t in tools]
        assert names == ["triage__local_tool"]

    def test_multiple_skills_same_server(self):
        """Multiple skills can use tools from the same MCP server."""
        mcp = _make_mock_mcp_manager({
            "linear": [
                ToolDefinition(name="searchIssues", description="Search", input_schema={}),
                ToolDefinition(name="createIssue", description="Create", input_schema={}),
            ],
        })
        skill_a = _make_skill_with_mcp("triage", [
            {"server": "linear", "tools": ["searchIssues"]},
        ])
        skill_b = _make_skill_with_mcp("planning", [
            {"server": "linear", "tools": ["createIssue"]},
        ])

        router = ToolRouter({"triage": skill_a, "planning": skill_b}, mcp_manager=mcp)
        tools = router.collect_tools_for_llm()

        names = [t["name"] for t in tools]
        assert "triage__searchIssues" in names
        assert "planning__createIssue" in names
        # Each is namespaced under its own skill
        assert "triage__createIssue" not in names
        assert "planning__searchIssues" not in names

    def test_mcp_tool_collision_across_servers_warns(self, caplog):
        """Overlapping tool names from different MCP servers within a skill log a warning."""
        mcp = _make_mock_mcp_manager({
            "server_a": [
                ToolDefinition(name="search", description="From A", input_schema={}),
            ],
            "server_b": [
                ToolDefinition(name="search", description="From B", input_schema={}),
            ],
        })
        skill = _make_skill_with_mcp("triage", [
            {"server": "server_a", "tools": ["search"]},
            {"server": "server_b", "tools": ["search"]},
        ])

        with caplog.at_level(logging.WARNING, logger="mithai.core.tool_router"):
            router = ToolRouter({"triage": skill}, mcp_manager=mcp)

        assert any("collision" in r.message.lower() for r in caplog.records)
        # The last server wins
        server_name, _, _ = router._mcp_index["triage__search"]  # noqa: SLF001
        assert server_name == "server_b"
