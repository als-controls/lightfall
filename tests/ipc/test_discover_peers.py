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
