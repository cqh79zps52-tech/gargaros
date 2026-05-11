from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

logger = logging.getLogger(__name__)


class MCPBackend:
    """Owns the lifecycle of a Windows-MCP child process and a single ClientSession."""

    def __init__(self, command: str = "windows-mcp", args: tuple[str, ...] = ()):
        self._command = command
        self._args = list(args)
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._call_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._session is not None:
            return
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=self._command, args=self._args)
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self._session = session
            self._stack = stack
            logger.info("MCP backend started: %s", self._command)
        except BaseException:
            await stack.aclose()
            raise

    async def stop(self) -> None:
        if self._stack is None:
            return
        try:
            await self._stack.aclose()
        finally:
            self._session = None
            self._stack = None

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError("Backend not started")
        async with self._call_lock:
            return await self._session.call_tool(name, args)

    @property
    def is_alive(self) -> bool:
        return self._session is not None
