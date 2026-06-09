"""Tests for IPCService peer discovery (dedup helper + async gather)."""

from __future__ import annotations

import json

import pytest

from lightfall.ipc.service import IPCService


class TestDedupePeers:
    def test_dedups_by_instance_id_and_tags_self(self):
        replies = [
            {"instance_id": "host-1", "display_name": "Lightfall", "prefix": "als.7011"},
            {"instance_id": "host-2", "display_name": "Tsuchinoko", "prefix": "tsk"},
            {"instance_id": "host-1", "display_name": "Lightfall", "prefix": "als.7011"},
        ]
        peers = IPCService._dedupe_peers(replies, self_id="host-1")
        assert len(peers) == 2
        by_id = {p["instance_id"]: p for p in peers}
        assert by_id["host-1"]["is_self"] is True
        assert by_id["host-2"]["is_self"] is False

    def test_self_sorts_first_then_by_display_name(self):
        replies = [
            {"instance_id": "h-z", "display_name": "Zeta"},
            {"instance_id": "h-self", "display_name": "Me"},
            {"instance_id": "h-a", "display_name": "Alpha"},
        ]
        peers = IPCService._dedupe_peers(replies, self_id="h-self")
        assert [p["instance_id"] for p in peers] == ["h-self", "h-a", "h-z"]

    def test_skips_non_dict_and_missing_instance_id(self):
        replies = ["garbage", {"display_name": "no id"}, {"instance_id": "h-1"}]
        peers = IPCService._dedupe_peers(replies, self_id="x")
        assert [p["instance_id"] for p in peers] == ["h-1"]
        assert peers[0]["display_name"] == ""
        assert peers[0]["prefix"] == ""


class TestNatsUrlProperty:
    def test_nats_url_exposes_configured_url(self, qapp):
        svc = IPCService(nats_url="tls://bcgnats:4222", topic_prefix="als.test")
        assert svc.nats_url == "tls://bcgnats:4222"


class _FakeSub:
    def __init__(self):
        self.unsubscribed = False

    async def unsubscribe(self):
        self.unsubscribed = True


class _FakeNC:
    """Minimal async NATS stand-in for gather tests.

    On publish to ``_lightfall.discover`` it immediately delivers the canned
    replies to the inbox callback, simulating instant peer responses.
    """

    def __init__(self, canned_replies):
        self._canned = canned_replies
        self._inbox_cb = None
        self.published = []
        self.sub = _FakeSub()

    def new_inbox(self):
        return "_INBOX.test123"

    async def subscribe(self, subject, cb=None):
        self._inbox_cb = cb
        return self.sub

    async def publish(self, subject, payload, reply=None):
        self.published.append((subject, payload, reply))
        if subject == "_lightfall.discover" and self._inbox_cb is not None:
            for reply_dict in self._canned:
                msg = type("Msg", (), {"data": json.dumps(reply_dict).encode()})()
                await self._inbox_cb(msg)


@pytest.mark.asyncio
async def test_gather_peers_collects_and_dedupes():
    svc = IPCService.__new__(IPCService)
    svc._instance_id = "host-self"
    svc._nc = _FakeNC([
        {"instance_id": "host-self", "display_name": "Me", "prefix": "als"},
        {"instance_id": "host-2", "display_name": "Other", "prefix": "oth"},
    ])
    peers = await svc._gather_peers(timeout_s=0.01)
    assert [p["instance_id"] for p in peers] == ["host-self", "host-2"]
    assert peers[0]["is_self"] is True
    assert svc._nc.sub.unsubscribed is True
    assert svc._nc.published[0][0] == "_lightfall.discover"
    assert svc._nc.published[0][2] == "_INBOX.test123"  # reply inbox set


@pytest.mark.asyncio
async def test_gather_peers_survives_unsubscribe_error():
    class _RaisingSub:
        async def unsubscribe(self):
            raise RuntimeError("boom")

    nc = _FakeNC([{"instance_id": "host-2", "display_name": "Other"}])
    nc.sub = _RaisingSub()
    svc = IPCService.__new__(IPCService)
    svc._instance_id = "host-self"
    svc._nc = nc
    peers = await svc._gather_peers(timeout_s=0.01)
    assert [p["instance_id"] for p in peers] == ["host-2"]


@pytest.mark.asyncio
async def test_gather_peers_returns_empty_when_no_nc():
    svc = IPCService.__new__(IPCService)
    svc._instance_id = "x"
    svc._nc = None
    assert await svc._gather_peers(timeout_s=0.01) == []


def test_discover_peers_calls_back_empty_when_not_connected(qapp):
    svc = IPCService(nats_url="tls://x:4222", topic_prefix="t")
    received = []
    svc.discover_peers(received.append, timeout_ms=10)
    assert received == [[]]  # immediate empty callback, not connected
