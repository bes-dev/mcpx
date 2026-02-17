import asyncio
import os
import signal
import sys
from contextlib import AsyncExitStack
from typing import Any, TextIO

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from mcpx.config import ConfigManager, ServerConfig

_NOISE_MARKERS = (
    "Traceback (most recent call last):",
    "asyncgen:",
    "an error occurred during closing of asynchronous generator",
    "BaseExceptionGroup:",
    "GeneratorExit",
    "RuntimeError: Attempted to exit cancel scope",
    "+-+--",
    "+----",
    "| ",
    "+-",
    'File "',
    "raise ",
    "~~~",
    "^^^",
    "...<",
    "During handling of the above exception",
    "anyio.create_task_group",
    "cancel_scope.__exit__",
)


class ServerError(Exception):
    """Clean error for MCP server failures."""


class _FilteredStderr:
    """Wraps real stderr, suppressing anyio/mcp internal tracebacks."""

    def __init__(self, real: TextIO) -> None:
        self._real = real
        self._suppressing = False

    def write(self, s: str) -> int:
        for line in s.splitlines(keepends=True):
            stripped = line.strip()
            if not stripped:
                continue
            if any(m in stripped for m in _NOISE_MARKERS):
                self._suppressing = True
                continue
            if self._suppressing and (stripped.startswith("|") or stripped.startswith("+")):
                continue
            self._suppressing = False
            self._real.write(line)
        return len(s)

    def flush(self) -> None:
        self._real.flush()

    def fileno(self) -> int:
        return self._real.fileno()

    def isatty(self) -> bool:
        return self._real.isatty()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class MCPClient:
    def __init__(self, server: ServerConfig, config_manager: ConfigManager) -> None:
        self._server = server
        self._env = config_manager.resolve_env(server)
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "MCPClient":
        env = {**os.environ, **self._env}
        params = StdioServerParameters(
            command=self._server.command,
            args=self._server.args,
            env=env,
        )
        try:
            read_stream, write_stream = await self._stack.enter_async_context(
                stdio_client(params, errlog=sys.stderr)
            )
        except Exception as e:
            raise ServerError(
                f"Failed to start server: {self._server.command} {' '.join(self._server.args)}\n{e}"
            ) from e
        self._session = ClientSession(read_stream, write_stream)
        await self._stack.enter_async_context(self._session)
        try:
            await asyncio.wait_for(
                self._session.initialize(),
                timeout=self._server.timeout,
            )
        except asyncio.TimeoutError:
            raise ServerError(
                f"Server did not respond within {self._server.timeout}s timeout."
            )
        except Exception as e:
            raise ServerError(f"Server connection failed: {e}") from e
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            await self._stack.aclose()
        except Exception:
            pass

    async def list_tools(self) -> list[Tool]:
        assert self._session
        result = await self._session.list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        assert self._session
        return await self._session.call_tool(name=name, arguments=arguments)


async def _run_with_client(
    server: ServerConfig,
    config_manager: ConfigManager,
    callback: Any,
) -> Any:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()

    def _signal_handler() -> None:
        if task and not task.done():
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        async with MCPClient(server, config_manager) as client:
            return await callback(client)
    except asyncio.CancelledError:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
    except ServerError:
        raise
    except Exception as e:
        raise ServerError(f"Unexpected error: {e}") from e
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def run_with_client(
    server: ServerConfig,
    config_manager: ConfigManager,
    callback: Any,
) -> Any:
    """Run callback(client) with filtered stderr to suppress anyio cleanup noise."""
    real_stderr = sys.stderr
    sys.stderr = _FilteredStderr(real_stderr)
    try:
        return asyncio.run(_run_with_client(server, config_manager, callback))
    finally:
        sys.stderr = real_stderr
