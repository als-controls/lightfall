"""Headless reference client for the Lightfall remote-control contract (v1).

Deliberately raw nats-py with NO lightfall imports: this file is the
contract's reference consumer and the starting point for Spec #2's
pystxmcontrol ``LightfallClient``.

Flow:
    client = LightfallRemoteClient("nats://127.0.0.1:4222", "als.test", "myapp")
    await client.connect()
    auth = await client.authenticate()        # -> approved reply w/ session_token
    reply = await client.call("commands.plan.list", {})
    await client.subscribe_event("runs.new", cb)
    await client.close()
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import nats

CONTRACT_VERSION = 1


class RemoteError(Exception):
    """Raised when the server replies with a structured error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class LightfallRemoteClient:
    """Minimal remote-control client: handshake + capability-channel calls."""

    def __init__(self, nats_url: str, prefix: str, app_name: str, app_version: str = "0.0") -> None:
        self._nats_url = nats_url
        self._prefix = prefix
        self._app_name = app_name
        self._app_version = app_version
        self._nc: nats.NATS | None = None
        self.session_token: str | None = None
        self.tiled_url: str | None = None
        self.tiled_token: str | None = None

    async def connect(self) -> None:
        self._nc = await nats.connect(self._nats_url)

    async def authenticate(self, timeout: float = 90.0) -> dict:
        """Run the auth.request handshake; store session_token on approval.

        The 90 s default outlives Lightfall's 60 s trust-dialog timeout.
        """
        msg = await self._nc.request(
            f"{self._prefix}.auth.request",
            json.dumps({"app_name": self._app_name, "app_version": self._app_version}).encode(),
            timeout=timeout,
        )
        reply = json.loads(msg.data.decode())
        if reply.get("status") == "approved":
            self.session_token = reply["session_token"]
            self.tiled_url = reply.get("tiled_url")
            self.tiled_token = reply.get("tiled_token")
        return reply

    async def call(self, suffix: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
        """Request/reply on the capability channel; raise RemoteError on errors."""
        if self.session_token is None:
            raise RuntimeError("Not authenticated — call authenticate() first")
        subject = f"{self._prefix}.session.{self.session_token}.{suffix}"
        body = dict(payload or {})
        body.setdefault("contract_version", CONTRACT_VERSION)
        msg = await self._nc.request(subject, json.dumps(body).encode(), timeout=timeout)
        reply = json.loads(msg.data.decode())
        if reply.get("status") == "error":
            raise RemoteError(reply.get("code", "unknown"), reply.get("message", ""))
        return reply

    async def call_bare(self, suffix: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
        """Request on the bare (non-channel) subject — used to prove rejection."""
        msg = await self._nc.request(
            f"{self._prefix}.{suffix}", json.dumps(payload or {}).encode(), timeout=timeout
        )
        return json.loads(msg.data.decode())

    async def subscribe_event(self, suffix: str, callback: Callable[[dict], Any]) -> None:
        """Subscribe a broadcast event (public prefixed subject)."""

        async def _cb(msg) -> None:
            callback(json.loads(msg.data.decode()))

        await self._nc.subscribe(f"{self._prefix}.{suffix}", cb=_cb)

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None
