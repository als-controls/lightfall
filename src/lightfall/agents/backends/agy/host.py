"""In-process tool host for agy sessions: Streamable HTTP MCP plus a ``/hook`` endpoint.

Each Lightfall :class:`ToolServer` is served by its own low-level MCP server at
``/mcp/<key>/`` (stateless, JSON responses). ``POST /hook`` forwards agy's
PreToolUse hook payload to ``hook_handler`` and returns its decision as JSON.
Every path requires ``Authorization: Bearer <token>``; the listener binds
``127.0.0.1`` only.

The host runs on the *current* asyncio loop (the AgentWorker's loop) as a
background task, so tool handlers execute on the same loop as the backend.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import hmac
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import uvicorn
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from lightfall.agents.tooling import ToolServer, json_schema
from lightfall.utils.logging import logger

HookHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_START_TIMEOUT_S = 5.0
_STOP_TIMEOUT_S = 3.0


def _to_content(block: dict[str, Any]) -> types.TextContent | types.ImageContent:
    kind = block.get("type")
    if kind == "image":
        return types.ImageContent(type="image", data=block["data"], mime_type=block["mimeType"])
    if kind == "text" or "text" in block:
        return types.TextContent(type="text", text=str(block.get("text", "")))
    # Say what was dropped rather than hand the model an empty string.
    return types.TextContent(type="text",
                             text=f"[{kind!r} content block not supported by the agy backend]")


def _error_result(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def _build_mcp_server(key: str, server: ToolServer) -> Server:
    specs = {s.name: s for s in server.tools}

    async def _list_tools(_ctx: ServerRequestContext,
                          _params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=s.name, description=s.description,
                       input_schema=copy.deepcopy(json_schema(s)))
            for s in server.tools
        ])

    async def _call_tool(_ctx: ServerRequestContext,
                         params: types.CallToolRequestParams) -> types.CallToolResult:
        # mcp 2 dropped the low-level server's input validation; we never used it.
        name, arguments = params.name, params.arguments
        spec = specs.get(name)
        if spec is None:
            return _error_result(f"Unknown tool: {name}")
        try:
            result = await spec.handler(arguments or {})
            if not isinstance(result, dict):
                raise TypeError("tool handler returned no result")
            content = [_to_content(b) for b in (result or {}).get("content", [])]
        except Exception as e:  # noqa: BLE001 - every tool failure becomes an MCP error result
            logger.opt(exception=e).debug("agy tool {}/{} raised", key, name)
            return _error_result(f"{type(e).__name__}: {e}")
        return types.CallToolResult(content=content, is_error=bool(result.get("is_error")))

    return Server(f"lightfall_{key}", version=server.version,
                  on_list_tools=_list_tools, on_call_tool=_call_tool)


class _BearerAuth:
    """Pure-ASGI middleware: 401 JSON unless the request carries the session token."""

    def __init__(self, app: ASGIApp, token: str,
                 on_request: Callable[[], None] | None = None) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()
        self._on_request = on_request

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        supplied = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                supplied = value
                break
        if hmac.compare_digest(supplied, self._expected):
            if self._on_request is not None:
                self._on_request()
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})


class _Server(uvicorn.Server):
    """uvicorn server that never touches process signal handlers (we are embedded)."""

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield


class ToolHost:
    """Serve ``servers`` over bearer-authenticated HTTP MCP on 127.0.0.1."""

    def __init__(self, servers: list[tuple[str, ToolServer]], hook_handler: HookHandler,
                 token: str) -> None:
        self._servers = list(servers)
        self._hook_handler = hook_handler
        self._token = token
        self._sock: socket.socket | None = None
        self._uvicorn: _Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._port = 0
        self._address: tuple[str, int] | None = None
        self._request_count = 0

    @property
    def request_count(self) -> int:
        """Authenticated requests served so far (/hook and /mcp/*): evidence that
        the agy child reached Lightfall, e.g. before deciding to resend a prompt."""
        return self._request_count

    def _count_request(self) -> None:
        self._request_count += 1

    @property
    def port(self) -> int:
        return self._port

    @property
    def address(self) -> tuple[str, int] | None:
        """The bound ``(host, port)`` while running."""
        return self._address

    def _build_app(self) -> ASGIApp:
        managers = [
            (key, StreamableHTTPSessionManager(app=_build_mcp_server(key, srv),
                                               stateless=True, json_response=True))
            for key, srv in self._servers
        ]

        def _mcp_endpoint(manager: StreamableHTTPSessionManager) -> ASGIApp:
            async def _asgi(scope: Scope, receive: Receive, send: Send) -> None:
                await manager.handle_request(scope, receive, send)
            return _asgi

        async def _hook(request: Request) -> JSONResponse:
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("hook body must be a JSON object")
                result = await self._hook_handler(body)
                if not isinstance(result, dict):
                    raise TypeError("hook handler returned a non-dict")
                return JSONResponse(result)
            except Exception as e:  # noqa: BLE001 - /hook is fail-closed on any error
                logger.warning("agy /hook failed closed: {}: {}", type(e).__name__, e)
                return JSONResponse({"decision": "deny",
                                     "reason": f"{type(e).__name__}: {e}"})

        @contextlib.asynccontextmanager
        async def _lifespan(_app: Starlette) -> AsyncIterator[None]:
            async with contextlib.AsyncExitStack() as stack:
                for _key, manager in managers:
                    await stack.enter_async_context(manager.run())
                yield

        routes = [Route("/hook", _hook, methods=["POST"])]
        routes += [Mount(f"/mcp/{key}", app=_mcp_endpoint(m)) for key, m in managers]
        return _BearerAuth(Starlette(routes=routes, lifespan=_lifespan), self._token,
                           self._count_request)

    async def start(self) -> int:
        """Bind 127.0.0.1:0, serve on the running loop in the background; return the port."""
        if self._task is not None:
            if not self._task.done():
                return self._port
            # Not reached today: AgyCliBackend.connect builds a new ToolHost each
            # time. A restart binds a new port, so a caller reusing one must also
            # rewrite the session config (LF_AGY_PORT) and respawn the agy child.
            logger.warning("agy tool host had stopped; restarting it")
            await self.stop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("127.0.0.1", 0))
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self._address = sock.getsockname()[:2]
        self._port = self._address[1]
        config = uvicorn.Config(self._build_app(), log_level="warning", lifespan="on", ws="none",
                                timeout_graceful_shutdown=1)
        self._uvicorn = _Server(config)
        self._task = asyncio.get_running_loop().create_task(
            self._uvicorn.serve(sockets=[sock]), name=f"agy-tool-host:{self._port}")

        try:
            async with asyncio.timeout(_START_TIMEOUT_S):
                while not self._uvicorn.started:
                    if self._task.done():
                        break
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pass
        if not self._uvicorn.started:
            task = self._task
            cause = (task.exception() if task.done() and not task.cancelled() else None)
            await self.stop()
            raise RuntimeError("agy tool host failed to start") from cause
        return self._port

    async def stop(self) -> None:
        """Shut down the server; bounded to a few seconds. Idempotent."""
        task, server, sock = self._task, self._uvicorn, self._sock
        self._task = self._uvicorn = self._sock = None
        self._address = None
        if task is not None and server is not None:
            server.should_exit = True
            done, _ = await asyncio.wait({task}, timeout=_STOP_TIMEOUT_S)
            if not done:
                server.force_exit = True
                task.cancel()
                await asyncio.wait({task}, timeout=0.5)
            if task.done() and not task.cancelled() and task.exception() is not None:
                logger.opt(exception=task.exception()).warning("agy tool host exited with error")
        if sock is not None:
            sock.close()
