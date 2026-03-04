"""MCP server manager — connects to MCP servers and exposes their tools.

Manages the lifecycle of MCP client sessions. Each configured server
is a subprocess (stdio transport) or connected via SSE/streamablehttp.

Skills declare which MCP tools they need via MCP_TOOLS in their tools.py.
The manager only starts servers that are actually referenced by skills.

Architecture: A background thread runs an asyncio event loop that keeps
all MCP sessions alive (required by transports like streamablehttp that
use anyio task groups). Sync callers use run_coroutine_threadsafe to
bridge into the async loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import threading
from dataclasses import dataclass, field

from mithai.core.skill_loader import ToolDefinition

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """Parsed config for a single MCP server."""

    name: str
    transport: str  # "stdio", "sse", or "streamablehttp"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class MCPManager:
    """
    Manages connections to MCP servers and exposes their tools.

    Runs a background event loop thread to keep async transports alive.
    """

    def __init__(self, mcp_config: dict):
        self._configs: dict[str, MCPServerConfig] = {}
        self._sessions: dict[str, dict] = {}
        self._server_tools: dict[str, list[ToolDefinition]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
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

    def _ensure_loop(self) -> None:
        """Start the background event loop thread if not running."""
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="mcp-event-loop",
            daemon=True,
        )
        self._thread.start()

    def _run_async(self, coro, timeout: float = 30.0):
        """Run an async coroutine on the background loop and block for result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def start(self, needed_servers: set[str]) -> None:
        """Connect to MCP servers that skills actually reference."""
        servers_to_start = needed_servers & set(self._configs.keys())
        if not servers_to_start:
            return

        self._ensure_loop()
        for name in servers_to_start:
            config = self._configs[name]
            try:
                self._run_async(self._connect_server(name, config), timeout=30.0)
                tool_count = len(self._server_tools.get(name, []))
                logger.info("Connected to MCP server: %s (%d tools)", name, tool_count)
            except Exception:
                logger.exception("Failed to connect to MCP server: %s", name)

    async def _connect_server(self, name: str, config: MCPServerConfig) -> None:
        """Connect to a single MCP server and discover its tools.

        Uses try/except to ensure partially opened transport and session
        contexts are closed if initialization or tool discovery fails.
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        transport_ctx = None
        session_ctx = None

        try:
            if config.transport == "stdio":
                if not config.command:
                    raise ValueError(f"MCP server '{name}' requires a command for stdio transport")

                params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env or None,
                )
                transport_ctx = stdio_client(params)
                read_stream, write_stream = await transport_ctx.__aenter__()

            elif config.transport == "sse":
                from mcp.client.sse import sse_client

                if not config.url:
                    raise ValueError(f"MCP server '{name}' requires a url for sse transport")

                transport_ctx = sse_client(url=config.url, headers=config.headers)
                read_stream, write_stream = await transport_ctx.__aenter__()

            elif config.transport == "streamablehttp":
                from mcp.client.streamable_http import streamablehttp_client

                if not config.url:
                    raise ValueError(f"MCP server '{name}' requires a url for streamablehttp transport")

                transport_ctx = streamablehttp_client(url=config.url, headers=config.headers)
                # streamablehttp returns (read, write, get_session_id) — 3 values
                read_stream, write_stream, _get_session_id = await transport_ctx.__aenter__()

            else:
                raise ValueError(f"Unknown transport '{config.transport}' for MCP server '{name}'")

            session_ctx = ClientSession(read_stream, write_stream)
            session = await session_ctx.__aenter__()
            await session.initialize()

            # Discover tools
            result = await session.list_tools()
            tools = []
            for tool in result.tools:
                tools.append(ToolDefinition(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema,
                    human=None,
                ))
            self._server_tools[name] = tools

            self._sessions[name] = {
                "session": session,
                "session_ctx": session_ctx,
                "transport_ctx": transport_ctx,
            }

        except BaseException:
            # Clean up partially opened contexts to avoid leaking
            # subprocesses or HTTP connections.
            for ctx in (session_ctx, transport_ctx):
                if ctx is not None:
                    try:
                        await ctx.__aexit__(None, None, None)
                    except Exception:
                        logger.debug("Error cleaning up context for %s", name, exc_info=True)
            raise

    def discover_tools(self, server_name: str) -> list[ToolDefinition]:
        """Return all tools discovered from a specific MCP server."""
        return list(self._server_tools.get(server_name, []))

    def _reconnect(self, server_name: str) -> bool:
        """Reconnect to an MCP server after a connection failure."""
        config = self._configs.get(server_name)
        if not config or not self._loop:
            return False

        # Close stale session
        entry = self._sessions.pop(server_name, None)
        if entry:
            for ctx_key in ("session_ctx", "transport_ctx"):
                try:
                    ctx = entry.get(ctx_key)
                    if ctx:
                        self._run_async(ctx.__aexit__(None, None, None), timeout=10.0)
                except Exception:
                    pass

        try:
            self._run_async(self._connect_server(server_name, config), timeout=30.0)
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
            result = self._run_async(
                session.call_tool(name=tool_name, arguments=arguments),
                timeout=60.0,
            )
            return self._extract_result(result)

        except Exception as e:
            logger.warning(
                "MCP tool call failed (%s.%s), attempting reconnect: %s",
                server_name, tool_name, e,
            )
            if self._reconnect(server_name):
                entry = self._sessions.get(server_name)
                if entry:
                    try:
                        result = self._run_async(
                            entry["session"].call_tool(name=tool_name, arguments=arguments),
                            timeout=60.0,
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
        """Disconnect from all MCP servers and stop the background loop."""
        if not self._loop:
            return

        for name, entry in self._sessions.items():
            for ctx_key in ("session_ctx", "transport_ctx"):
                try:
                    ctx = entry.get(ctx_key)
                    if ctx:
                        self._run_async(ctx.__aexit__(None, None, None), timeout=10.0)
                except Exception:
                    logger.debug("Error closing MCP %s for %s", ctx_key, name, exc_info=True)

        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5.0)

        self._loop.close()
        self._loop = None
        self._thread = None
        self._sessions.clear()
        self._server_tools.clear()
