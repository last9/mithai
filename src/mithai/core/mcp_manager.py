"""MCP server manager — connects to MCP servers and exposes their tools.

Manages the lifecycle of MCP client sessions. Each configured server
is a subprocess (stdio transport) that the manager connects to, discovers
tools from, and can execute tool calls against.

Skills declare which MCP tools they need via MCP_TOOLS in their tools.py.
The manager only starts servers that are actually referenced by skills.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from mithai.core.skill_loader import ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Parsed config for a single MCP server."""

    name: str
    transport: str  # "stdio" or "sse"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None  # sse only
    headers: dict[str, str] = field(default_factory=dict)


class MCPManager:
    """
    Manages connections to MCP servers and exposes their tools.

    Each server is started as a subprocess (stdio) or connected via SSE.
    Tools are discovered on startup and can be called by name.
    """

    def __init__(self, mcp_config: dict):
        self._configs: dict[str, MCPServerConfig] = {}
        self._sessions: dict[str, dict] = {}
        self._server_tools: dict[str, list[ToolDefinition]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._parse_config(mcp_config)

    def _parse_config(self, mcp_config: dict) -> None:
        """Parse the mcp_servers config section."""
        for name, conf in mcp_config.items():
            self._configs[name] = MCPServerConfig(
                name=name,
                transport=conf.get("transport", "stdio"),
                command=conf.get("command"),
                args=conf.get("args", []) + conf.get("args_extra", []),
                env=conf.get("env", {}),
                url=conf.get("url"),
                headers=conf.get("headers", {}),
            )

    def start(self, needed_servers: set[str]) -> None:
        """Connect to MCP servers that skills actually reference."""
        servers_to_start = needed_servers & set(self._configs.keys())
        if not servers_to_start:
            return

        self._loop = asyncio.new_event_loop()
        for name in servers_to_start:
            config = self._configs[name]
            try:
                self._loop.run_until_complete(self._connect_server(name, config))
                tool_count = len(self._server_tools.get(name, []))
                logger.info("Connected to MCP server: %s (%d tools)", name, tool_count)
            except Exception:
                logger.exception("Failed to connect to MCP server: %s", name)

    async def _connect_server(self, name: str, config: MCPServerConfig) -> None:
        """Connect to a single MCP server and discover its tools."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if config.transport == "stdio":
            if not config.command:
                raise ValueError(f"MCP server '{name}' requires a command for stdio transport")

            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env or None,
            )
            # Enter both context managers and keep them open
            transport_ctx = stdio_client(params)
            read_stream, write_stream = await transport_ctx.__aenter__()

            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            await session.initialize()

            self._sessions[name] = {
                "session": session,
                "session_ctx": session_ctx,
                "transport_ctx": transport_ctx,
            }

        elif config.transport == "sse":
            from mcp.client.sse import sse_client

            if not config.url:
                raise ValueError(f"MCP server '{name}' requires a url for sse transport")

            transport_ctx = sse_client(url=config.url, headers=config.headers)
            read_stream, write_stream = await transport_ctx.__aenter__()

            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            await session.initialize()

            self._sessions[name] = {
                "session": session,
                "session_ctx": session_ctx,
                "transport_ctx": transport_ctx,
            }
        else:
            raise ValueError(f"Unknown transport '{config.transport}' for MCP server '{name}'")

        # Discover tools
        result = await session.list_tools()
        tools = []
        for tool in result.tools:
            tools.append(ToolDefinition(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema,
                human=None,  # Skills set human levels, not the server
            ))
        self._server_tools[name] = tools

    def discover_tools(self, server_name: str) -> list[ToolDefinition]:
        """Return all tools discovered from a specific MCP server."""
        return list(self._server_tools.get(server_name, []))

    def _reconnect(self, server_name: str) -> bool:
        """Reconnect to an MCP server after a connection failure."""
        config = self._configs.get(server_name)
        if not config:
            return False

        # Close stale session
        entry = self._sessions.pop(server_name, None)
        if entry:
            for ctx_key in ("session_ctx", "transport_ctx"):
                try:
                    ctx = entry.get(ctx_key)
                    if ctx:
                        self._loop.run_until_complete(ctx.__aexit__(None, None, None))
                except Exception:
                    pass

        try:
            self._loop.run_until_complete(self._connect_server(server_name, config))
            logger.info("Reconnected to MCP server: %s", server_name)
            return True
        except Exception:
            logger.exception("Failed to reconnect to MCP server: %s", server_name)
            return False

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """Execute an MCP tool call and return the result as a string.

        Auto-reconnects once if the connection is dead.
        """
        entry = self._sessions.get(server_name)
        if entry is None:
            return json.dumps({"error": f"MCP server not connected: {server_name}"})

        session = entry["session"]
        try:
            result = self._loop.run_until_complete(
                session.call_tool(name=tool_name, arguments=arguments)
            )
            return self._extract_result(result)

        except Exception as e:
            logger.warning(
                "MCP tool call failed (%s.%s), attempting reconnect: %s",
                server_name, tool_name, e,
            )
            # Try reconnecting once
            if self._reconnect(server_name):
                entry = self._sessions.get(server_name)
                if entry:
                    try:
                        result = self._loop.run_until_complete(
                            entry["session"].call_tool(name=tool_name, arguments=arguments)
                        )
                        return self._extract_result(result)
                    except Exception as retry_err:
                        logger.exception("MCP tool call failed after reconnect: %s.%s", server_name, tool_name)
                        return json.dumps({"error": str(retry_err)})

            return json.dumps({"error": str(e)})

    @staticmethod
    def _extract_result(result) -> str:
        """Extract text content from an MCP tool result."""
        if result.isError:
            parts = []
            for block in result.content:
                if block.type == "text":
                    parts.append(block.text)
            return json.dumps({"error": "\n".join(parts) or "MCP tool returned an error"})

        parts = []
        for block in result.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts) if parts else json.dumps({"result": "ok"})

    def stop(self) -> None:
        """Disconnect from all MCP servers."""
        if not self._loop:
            return

        for name, entry in self._sessions.items():
            try:
                session_ctx = entry.get("session_ctx")
                if session_ctx:
                    self._loop.run_until_complete(session_ctx.__aexit__(None, None, None))
            except Exception:
                logger.debug("Error closing MCP session %s", name, exc_info=True)
            try:
                transport_ctx = entry.get("transport_ctx")
                if transport_ctx:
                    self._loop.run_until_complete(transport_ctx.__aexit__(None, None, None))
            except Exception:
                logger.debug("Error closing MCP transport %s", name, exc_info=True)

        self._loop.close()
        self._loop = None
        self._sessions.clear()
        self._server_tools.clear()
