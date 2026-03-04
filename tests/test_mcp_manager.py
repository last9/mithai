"""Tests for MCP server manager."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mithai.core.mcp_manager import MCPManager, MCPServerConfig


@pytest.fixture
def mcp_config():
    return {
        "linear": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@linear/mcp-server"],
            "env": {"LINEAR_API_KEY": "test-key"},
        },
        "github": {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@github/mcp-server"],
            "env": {"GITHUB_TOKEN": "test-token"},
        },
    }


def _setup_loop(mgr):
    """Set up a background event loop for testing (mimics _ensure_loop)."""
    mgr._loop = asyncio.new_event_loop()
    mgr._thread = threading.Thread(target=mgr._loop.run_forever, daemon=True)
    mgr._thread.start()


def _teardown_loop(mgr):
    """Tear down the background event loop."""
    if mgr._loop and mgr._loop.is_running():
        mgr._loop.call_soon_threadsafe(mgr._loop.stop)
    if mgr._thread:
        mgr._thread.join(timeout=5.0)
    if mgr._loop and not mgr._loop.is_closed():
        mgr._loop.close()
    mgr._loop = None
    mgr._thread = None


def test_parse_config(mcp_config):
    """Config is parsed into MCPServerConfig objects."""
    mgr = MCPManager(mcp_config)
    assert "linear" in mgr._configs
    assert "github" in mgr._configs

    linear = mgr._configs["linear"]
    assert linear.transport == "stdio"
    assert linear.command == "npx"
    assert linear.args == ["-y", "@linear/mcp-server"]
    assert linear.env == {"LINEAR_API_KEY": "test-key"}


def test_parse_config_defaults():
    """Missing fields get defaults."""
    mgr = MCPManager({"minimal": {"command": "test-server"}})
    conf = mgr._configs["minimal"]
    assert conf.transport == "stdio"
    assert conf.args == []
    assert conf.env == {}
    assert conf.url is None


def test_parse_config_sse():
    """SSE transport parses url and headers."""
    mgr = MCPManager({
        "remote": {
            "transport": "sse",
            "url": "https://mcp.example.com",
            "headers": {"Authorization": "Bearer xyz"},
        },
    })
    conf = mgr._configs["remote"]
    assert conf.transport == "sse"
    assert conf.url == "https://mcp.example.com"
    assert conf.headers == {"Authorization": "Bearer xyz"}


def test_parse_config_args_extra():
    """args_extra is appended to args."""
    mgr = MCPManager({
        "srv": {
            "command": "test",
            "args": ["a"],
            "args_extra": ["b", "c"],
        },
    })
    assert mgr._configs["srv"].args == ["a", "b", "c"]


def test_start_only_needed_servers(mcp_config):
    """Only servers referenced by skills are started."""
    mgr = MCPManager(mcp_config)

    with patch.object(mgr, "_connect_server", new_callable=AsyncMock) as mock_connect:
        mgr.start({"linear"})
        # Only linear should be connected
        assert mock_connect.await_count == 1
        mock_connect.assert_awaited_once()
        call_args = mock_connect.call_args
        assert call_args[0][0] == "linear"
    _teardown_loop(mgr)


def test_start_no_overlap(mcp_config):
    """No servers started if none match the needed set."""
    mgr = MCPManager(mcp_config)

    with patch.object(mgr, "_connect_server", new_callable=AsyncMock) as mock_connect:
        mgr.start({"nonexistent"})
        mock_connect.assert_not_awaited()


def test_discover_tools_empty():
    """Returns empty list for unknown server."""
    mgr = MCPManager({})
    assert mgr.discover_tools("unknown") == []


def test_discover_tools_returns_copy():
    """discover_tools returns a copy, not the internal list."""
    mgr = MCPManager({})
    from mithai.core.skill_loader import ToolDefinition
    tools = [ToolDefinition(name="test", description="d", input_schema={})]
    mgr._server_tools["srv"] = tools

    result = mgr.discover_tools("srv")
    assert result == tools
    assert result is not tools


def test_call_tool_not_connected():
    """Returns error if server not connected."""
    mgr = MCPManager({})
    result = mgr.call_tool("missing", "tool", {})
    data = json.loads(result)
    assert "error" in data
    assert "not connected" in data["error"]


def test_call_tool_success():
    """Successful tool call returns text content."""
    mgr = MCPManager({})
    _setup_loop(mgr)

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.isError = False
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = "result data"
    mock_result.content = [mock_block]
    mock_session.call_tool = AsyncMock(return_value=mock_result)

    mgr._sessions["test_server"] = {"session": mock_session, "context": MagicMock()}

    result = mgr.call_tool("test_server", "my_tool", {"arg": "val"})
    assert result == "result data"
    mock_session.call_tool.assert_awaited_once_with(name="my_tool", arguments={"arg": "val"})

    _teardown_loop(mgr)


def test_call_tool_error_result():
    """MCP tool returning isError=True is handled."""
    mgr = MCPManager({})
    _setup_loop(mgr)

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.isError = True
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = "something went wrong"
    mock_result.content = [mock_block]
    mock_session.call_tool = AsyncMock(return_value=mock_result)

    mgr._sessions["srv"] = {"session": mock_session, "context": MagicMock()}

    result = mgr.call_tool("srv", "tool", {})
    data = json.loads(result)
    assert "error" in data
    assert "something went wrong" in data["error"]

    _teardown_loop(mgr)


def test_call_tool_exception():
    """Exceptions during tool call are caught and returned as error."""
    mgr = MCPManager({})
    _setup_loop(mgr)

    mock_session = MagicMock()
    mock_session.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))

    mgr._sessions["srv"] = {"session": mock_session, "context": MagicMock()}

    result = mgr.call_tool("srv", "tool", {})
    data = json.loads(result)
    assert "error" in data
    assert "connection lost" in data["error"]

    _teardown_loop(mgr)


def test_stop_cleans_up():
    """stop() closes sessions and clears state."""
    mgr = MCPManager({})
    _setup_loop(mgr)

    mock_ctx = AsyncMock()
    mgr._sessions["srv"] = {
        "session": MagicMock(),
        "session_ctx": mock_ctx,
        "transport_ctx": mock_ctx,
    }
    mgr._server_tools["srv"] = []

    mgr.stop()

    assert mgr._loop is None
    assert mgr._sessions == {}
    assert mgr._server_tools == {}


def test_stop_no_loop():
    """stop() is safe when no event loop exists."""
    mgr = MCPManager({})
    mgr.stop()  # Should not raise
