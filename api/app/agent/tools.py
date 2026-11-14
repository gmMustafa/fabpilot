"""How the agent calls the MCP servers (5.2a).

The agent lives in `api`, and the MCP servers depend on `api` (they import its data layer). So the
agent must NOT import them: it starts them as SUBPROCESSES over stdio, exactly like Claude Desktop
does. That's the real deployment shape, not a test trick.

The nodes only see the small `Tools` protocol, so tests use `FakeTools` (fixed answers, no
processes) and one integration test starts the real servers.

Why not langchain-mcp-adapters: 0.3.2 requires mcp<2, our servers need mcp 2.x, and the workspace
has ONE environment (checked in 5.2a: installing it would have downgraded the servers' SDK).
"""

import os
import sys
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Literal, Protocol, Self

from mcp import Client
from mcp.client.stdio import StdioServerParameters

Server = Literal["sensors", "docs"]

# How to start each server: the same entry points as .mcp.json, with this interpreter (the
# workspace venv has both server packages installed).
SERVER_MODULES: dict[Server, str] = {"sensors": "sensor_mcp.server", "docs": "docs_mcp.server"}


class ToolCallError(Exception):
    """A tool returned is_error=True. The message is the server's text (actionable, 4.1a)."""


class Tools(Protocol):
    async def call(self, server: Server, tool: str, args: Mapping[str, Any]) -> dict[str, Any]: ...


def _unwrap(structured: dict[str, Any] | None) -> dict[str, Any]:
    """MCP wraps list results as {"result": [...]} (4.1a probe); objects come as they are."""
    return structured or {}


class McpTools:
    """Both servers as subprocesses, for the duration of one `async with` block.

    Open and close it in the SAME task (anyio cancel scopes, 4.1a); calling tools from nodes that
    LangGraph runs in other tasks is fine.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = {**os.environ, **(env or {})}
        self._stack = AsyncExitStack()
        self._clients: dict[Server, Client] = {}

    async def __aenter__(self) -> Self:
        for server, module in SERVER_MODULES.items():
            params = StdioServerParameters(
                command=sys.executable, args=["-m", module], env=self._env
            )
            self._clients[server] = await self._stack.enter_async_context(Client(params))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._stack.aclose()

    async def call(self, server: Server, tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
        result = await self._clients[server].call_tool(tool, dict(args))
        if result.is_error:
            text = " ".join(c.text for c in result.content if hasattr(c, "text"))
            raise ToolCallError(f"{tool}: {text}")
        return _unwrap(result.structured_content)


Answer = dict[str, Any] | Callable[[Mapping[str, Any]], dict[str, Any]] | ToolCallError


class FakeTools:
    """Fixed answers per (server, tool); records every call. For node and graph tests."""

    def __init__(self, answers: Mapping[tuple[Server, str], Answer]) -> None:
        self._answers = dict(answers)
        self.calls: list[tuple[Server, str, dict[str, Any]]] = []

    async def call(self, server: Server, tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append((server, tool, dict(args)))
        answer = self._answers[(server, tool)]
        if isinstance(answer, ToolCallError):
            raise answer
        return answer(args) if callable(answer) else answer
