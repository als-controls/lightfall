# tests/agents/agy/test_host.py
from __future__ import annotations

import asyncio
import base64
import socket
import struct
import zlib
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lightfall.agents.tooling import ToolServer, ToolSpec, json_schema

TOKEN = "s3cret-token"


def _png_1x1() -> bytes:
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


PNG_B64 = base64.b64encode(_png_1x1()).decode()


async def _echo(args):
    return {"content": [{"type": "text", "text": f"echo {args.get('msg')}"}]}


async def _snap(args):
    return {"content": [{"type": "image", "data": PNG_B64, "mimeType": "image/png"}]}


async def _boom(args):
    raise ValueError("bad")


def _servers():
    echo = ToolSpec("echo", "Echo a message", {"msg": str}, _echo)
    snap = ToolSpec("snap", "Take a snapshot", {}, _snap)
    boom = ToolSpec("boom", "Always fails", {}, _boom)
    move = ToolSpec("move", "Move a motor",
                    {"type": "object", "properties": {"motor": {"type": "string"}},
                     "required": ["motor"]}, _echo)
    return [("qt", ToolServer("qt", "1.0.0", [echo, snap, boom])),
            ("bus", ToolServer("bus", "1.0.0", [move]))]


async def _hook(payload):
    return {"decision": "allow", "reason": "ok", "seen": payload}


@pytest.fixture
async def host():
    from lightfall.agents.backends.agy.host import ToolHost

    h = ToolHost(_servers(), _hook, TOKEN)
    await h.start()
    try:
        yield h
    finally:
        await h.stop()


@asynccontextmanager
async def _session(port: int, key: str, token: str | None = TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp/{key}/",
                                          http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def test_list_tools_uses_json_schema(host):
    async with _session(host.port, "qt") as s:
        tools = (await s.list_tools()).tools
    assert [t.name for t in tools] == ["echo", "snap", "boom"]
    spec = _servers()[0][1].tools[0]
    assert tools[0].description == "Echo a message"
    assert tools[0].inputSchema == json_schema(spec)

    async with _session(host.port, "bus") as s:
        (move,) = (await s.list_tools()).tools
    assert move.name == "move"
    assert move.inputSchema == json_schema(_servers()[1][1].tools[0])


async def test_call_tool_text_and_image(host):
    async with _session(host.port, "qt") as s:
        r = await s.call_tool("echo", {"msg": "hi"})
        assert not r.isError
        assert [(c.type, c.text) for c in r.content] == [("text", "echo hi")]

        r = await s.call_tool("snap", {})
        assert not r.isError
        (img,) = r.content
        assert (img.type, img.mimeType, img.data) == ("image", "image/png", PNG_B64)


async def test_tool_error_becomes_iserror(host):
    async with _session(host.port, "qt") as s:
        r = await s.call_tool("boom", {})
        assert r.isError
        assert "ValueError: bad" in r.content[0].text
        r = await s.call_tool("echo", {"msg": "still alive"})
        assert not r.isError
        assert r.content[0].text == "echo still alive"


async def test_is_error_dict_becomes_iserror():
    from lightfall.agents.backends.agy.host import ToolHost

    async def _declined(args):
        return {"content": [{"type": "text", "text": "User declined"}], "is_error": True}

    srv = ToolServer("ask", "1.0.0", [ToolSpec("ask_user", "Ask", {}, _declined)])
    h = ToolHost([("ask", srv)], _hook, TOKEN)
    await h.start()
    try:
        async with _session(h.port, "ask") as s:
            r = await s.call_tool("ask_user", {})
        assert r.isError
        assert r.content[0].text == "User declined"
    finally:
        await h.stop()


async def test_hook_requires_token(host):
    url = f"http://127.0.0.1:{host.port}/hook"
    body = {"toolCall": {"name": "view_file", "args": {"path": "x"}}}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(url, json=body)
        assert r.status_code == 401
        r = await c.post(url, json=body, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        r = await c.post(url, json=body, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert r.json() == {"decision": "allow", "reason": "ok", "seen": body}


async def test_mcp_requires_token(host):
    for token in (None, "wrong"):
        with pytest.raises(BaseException):  # noqa: B017 - transport wraps the 401 in an ExceptionGroup
            async with asyncio.timeout(10):
                async with _session(host.port, "qt", token=token):
                    pass

    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}}}
    hdrs = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=10) as c:
        url = f"http://127.0.0.1:{host.port}/mcp/qt/"
        assert (await c.post(url, json=init, headers=hdrs)).status_code == 401
        bad = {**hdrs, "Authorization": "Bearer wrong"}
        assert (await c.post(url, json=init, headers=bad)).status_code == 401
        ok = {**hdrs, "Authorization": f"Bearer {TOKEN}"}
        assert (await c.post(url, json=init, headers=ok)).status_code == 200


async def test_binds_localhost_only(host):
    assert host.port > 0
    assert host.address == ("127.0.0.1", host.port)


async def test_stop_releases_port():
    from lightfall.agents.backends.agy.host import ToolHost

    h = ToolHost(_servers(), _hook, TOKEN)
    port = await h.start()
    assert port == h.port
    with socket.create_connection(("127.0.0.1", port), timeout=2):
        pass
    await h.stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
    await h.stop()  # idempotent


async def test_hook_fails_closed_on_bad_body_and_handler_error():
    from lightfall.agents.backends.agy.host import ToolHost

    async def boom(payload):
        raise RuntimeError("kaput")

    h = ToolHost(_servers(), boom, TOKEN)
    await h.start()
    try:
        url = f"http://127.0.0.1:{h.port}/hook"
        auth = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(timeout=10) as c:
            for kwargs in ({"json": {"a": 1}}, {"content": b"not json"}, {"json": [1, 2]}):
                r = await c.post(url, headers=auth, **kwargs)
                assert r.status_code == 200
                assert r.json()["decision"] == "deny"
            r = await c.post(url, headers=auth, json={"a": 1})
            assert r.json()["reason"] == "RuntimeError: kaput"
    finally:
        await h.stop()


async def test_auth_rejects_unauthenticated_websocket_scope():
    from lightfall.agents.backends.agy.host import _BearerAuth

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    mw = _BearerAuth(inner, TOKEN)
    sent = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "websocket.connect"}

    await mw({"type": "websocket", "headers": []}, receive, send)
    assert reached == []
    assert sent and sent[0]["type"] == "websocket.close"

    await mw({"type": "websocket",
              "headers": [(b"authorization", f"Bearer {TOKEN}".encode())]}, receive, send)
    await mw({"type": "lifespan"}, receive, send)
    assert reached == ["websocket", "lifespan"]
