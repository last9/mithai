"""Tests for Human MCP."""

from mithai.core.skill_loader import ToolDefinition
from mithai.human.mcp import HumanMCP


class FakeAdapter:
    """Fake adapter for testing."""

    def __init__(self, approve=True):
        self._approve = approve
        self.requests = []

    def request_human_approval(self, request, channel_id):
        self.requests.append(request)
        return self._approve


def test_auto_execute_no_human_field():
    adapter = FakeAdapter()
    mcp = HumanMCP()

    tool_def = ToolDefinition(name="get_pods", description="List pods", input_schema={})
    result = mcp.request_approval("k8s__get_pods", {}, tool_def, "ch1", adapter=adapter)

    assert result is True
    assert len(adapter.requests) == 0  # Never asked human


def test_approve_level():
    adapter = FakeAdapter(approve=True)
    mcp = HumanMCP()

    tool_def = ToolDefinition(
        name="restart", description="Restart", input_schema={}, human="approve"
    )
    result = mcp.request_approval("k8s__restart", {"dep": "nginx"}, tool_def, "ch1", adapter=adapter)

    assert result is True
    assert len(adapter.requests) == 1
    assert adapter.requests[0].level == "approve"


def test_deny():
    adapter = FakeAdapter(approve=False)
    mcp = HumanMCP()

    tool_def = ToolDefinition(
        name="restart", description="Restart", input_schema={}, human="approve"
    )
    result = mcp.request_approval("k8s__restart", {}, tool_def, "ch1", adapter=adapter)

    assert result is False


def test_config_override_escalate():
    mcp = HumanMCP(config={"overrides": {"shell__run": "confirm"}})

    tool_def = ToolDefinition(name="run", description="Run", input_schema={})
    level = mcp.resolve_level("shell__run", tool_def)

    assert level == "confirm"


def test_config_override_deescalate():
    mcp = HumanMCP(config={"overrides": {"k8s__restart": None}})

    tool_def = ToolDefinition(
        name="restart", description="Restart", input_schema={}, human="approve"
    )
    level = mcp.resolve_level("k8s__restart", tool_def)

    assert level is None  # De-escalated to auto-execute


def test_dynamic_level_resolves_to_none():
    """When dynamic resolves to None, tool auto-executes without asking human."""
    adapter = FakeAdapter()
    mcp = HumanMCP()

    # Simulate what the engine does: resolve dynamic → replace tool_def
    tool_def = ToolDefinition(
        name="run_command", description="Run a shell command", input_schema={}, human=None
    )
    result = mcp.request_approval("shell__run_command", {"command": "uptime"}, tool_def, "ch1", adapter=adapter)

    assert result is True
    assert len(adapter.requests) == 0  # No human asked


def test_dynamic_level_resolves_to_approve():
    """When dynamic resolves to approve, human is consulted."""
    adapter = FakeAdapter(approve=True)
    mcp = HumanMCP()

    # Simulate what the engine does: resolve dynamic → replace tool_def with "approve"
    tool_def = ToolDefinition(
        name="run_command", description="Run a shell command", input_schema={}, human="approve"
    )
    result = mcp.request_approval("shell__run_command", {"command": "rm -rf /"}, tool_def, "ch1", adapter=adapter)

    assert result is True
    assert len(adapter.requests) == 1
    assert adapter.requests[0].level == "approve"
