from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import CONNECTION_CLOSED

from voice_agent.config import McpServerSpec

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
TOOL_TIMEOUTS_S: Mapping[str, float] = {"code__run_python": 130.0}
_ENV_REF = re.compile(r"\$\{([^}]*)\}")
_CLOSED_NAMES = {"ClosedResourceError", "BrokenResourceError", "EndOfStream"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    server: str
    tool: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    text: str
    is_error: bool
    duration_s: float


class _Unavailable(Exception):
    pass


def _expand_env(env: Mapping[str, str]) -> dict[str, str]:
    def repl(m: re.Match[str]) -> str:
        var = m.group(1)
        if var not in os.environ:
            raise _Unavailable(f"environment variable {var} is not set")
        return os.environ[var]

    return {key: _ENV_REF.sub(repl, value) for key, value in env.items()}


def _is_dead(exc: BaseException) -> bool:
    if isinstance(exc, McpError):
        return exc.error.code == CONNECTION_CLOSED
    return isinstance(exc, _Unavailable | OSError | EOFError) or type(exc).__name__ in _CLOSED_NAMES


def _reason(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timed out starting"
    if isinstance(exc, _Unavailable):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"[:200]


def _render(result: Any) -> str:
    return "\n".join(
        block.text if block.type == "text" else f"[{block.type} omitted]"
        for block in result.content
    )


class _Server:
    """One stdio server, owned by one task so its anyio scopes open and close together."""

    def __init__(self, name: str, spec: McpServerSpec, start_timeout_s: float) -> None:
        self.name = name
        self.spec = spec
        self.start_timeout_s = start_timeout_s
        self.session: ClientSession | None = None
        self.lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _run(self, params: StdioServerParameters, ready: asyncio.Future[list[Any]]) -> None:
        try:
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools: list[Any] = []
                cursor: str | None = None
                while True:
                    page = await session.list_tools(cursor)
                    tools.extend(page.tools)
                    cursor = page.nextCursor
                    if not cursor:
                        break
                self.session = session
                if not ready.done():
                    ready.set_result(tools)
                await self._stop.wait()
        except Exception as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            self.session = None
            if not ready.done():
                ready.set_exception(_Unavailable("cancelled"))

    async def start(self) -> list[Any]:
        env = _expand_env(self.spec.env)
        params = StdioServerParameters(
            command=self.spec.command, args=list(self.spec.args), env=env or None
        )
        ready: asyncio.Future[list[Any]] = asyncio.get_running_loop().create_future()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(params, ready))
        try:
            return await asyncio.wait_for(asyncio.shield(ready), self.start_timeout_s)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        task, self._task = self._task, None
        self.session = None
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), 5.0)
        except BaseException:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def restart(self) -> bool:
        await self.stop()
        try:
            await self.start()
        except Exception as exc:
            log.warning("mcp server %s restart failed: %s", self.name, _reason(exc))
            return False
        return True

    async def call(self, tool: str, arguments: Mapping[str, Any], timeout_s: float) -> Any:
        session = self.session
        if session is None:
            raise _Unavailable(self.name)
        return await session.call_tool(
            tool, dict(arguments), read_timeout_seconds=timedelta(seconds=timeout_s)
        )


class ToolRegistry:
    def __init__(
        self,
        specs: Mapping[str, ToolSpec],
        unavailable: Mapping[str, str],
        servers: Mapping[str, _Server],
    ) -> None:
        self._specs = dict(specs)
        self._unavailable = dict(unavailable)
        self._servers = servers

    @property
    def tools(self) -> list[ToolSpec]:
        return [self._specs[n] for n in sorted(self._specs)]

    @property
    def unavailable(self) -> dict[str, str]:
        return dict(self._unavailable)

    def subset(self, prefixes: Sequence[str], *, exclude: Sequence[str] = ()) -> ToolRegistry:
        keep = {
            n: s
            for n, s in self._specs.items()
            if n.startswith(tuple(prefixes)) and n not in exclude
        }
        return ToolRegistry(keep, self._unavailable, self._servers)

    def anthropic_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self.tools
        ]

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, timeout_s: float | None = None
    ) -> ToolOutcome:
        t0 = time.monotonic()

        def out(text: str, is_error: bool) -> ToolOutcome:
            return ToolOutcome(text, is_error, time.monotonic() - t0)

        spec = self._specs.get(name)
        if spec is None:
            return out(f"unknown tool {name}", True)
        if timeout_s is None:
            timeout_s = TOOL_TIMEOUTS_S.get(name, DEFAULT_TIMEOUT_S)
        server = self._servers[spec.server]
        async with server.lock:
            for attempt in range(2):
                try:
                    result = await asyncio.wait_for(
                        server.call(spec.tool, arguments, timeout_s), timeout_s
                    )
                except TimeoutError:
                    return out("timed out", True)
                except Exception as exc:
                    if not _is_dead(exc):
                        return out(f"{type(exc).__name__}: {exc}"[:500], True)
                else:
                    return out(_render(result), bool(result.isError))
                if attempt == 0 and not await server.restart():
                    break
            return out(f"server {spec.server} unavailable", True)


class McpClientManager:
    def __init__(
        self, servers: Mapping[str, McpServerSpec], *, start_timeout_s: float = 10.0
    ) -> None:
        self._specs = dict(servers)
        self._start_timeout_s = start_timeout_s
        self._servers: dict[str, _Server] = {}

    async def __aenter__(self) -> ToolRegistry:
        specs: dict[str, ToolSpec] = {}
        unavailable: dict[str, str] = {}
        self._servers = {n: _Server(n, s, self._start_timeout_s) for n, s in self._specs.items()}

        async def boot(server: _Server) -> None:
            try:
                tools = await server.start()
            except Exception as exc:
                reason = _reason(exc)
                unavailable[server.name] = reason
                log.warning("mcp server %s unavailable: %s", server.name, reason)
                return
            for t in tools:
                full = f"{server.name}__{t.name}"
                assert full not in specs, f"duplicate tool {full}"
                specs[full] = ToolSpec(
                    name=full,
                    server=server.name,
                    tool=t.name,
                    description=t.description or "",
                    input_schema=dict(t.inputSchema),
                )

        try:
            await asyncio.gather(*(boot(s) for s in self._servers.values()))
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return ToolRegistry(specs, unavailable, self._servers)

    async def __aexit__(self, *exc: object) -> None:
        await asyncio.gather(*(s.stop() for s in self._servers.values()), return_exceptions=True)
