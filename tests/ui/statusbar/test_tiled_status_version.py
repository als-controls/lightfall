"""Tests for Tiled client/server version-mismatch detection in the status bar."""
from __future__ import annotations

from lightfall.services.tiled_service import TiledService
from lightfall.ui.statusbar.plugins.tiled_status import _versions_mismatch


def test_mismatch_true_when_versions_differ():
    # The real bug: client 0.2.11 vs server 0.2.5.
    assert _versions_mismatch("0.2.11", "0.2.5") is True


def test_no_mismatch_when_equal():
    assert _versions_mismatch("0.2.11", "0.2.11") is False


def test_no_mismatch_when_either_unknown():
    assert _versions_mismatch(None, "0.2.5") is False
    assert _versions_mismatch("0.2.11", None) is False
    assert _versions_mismatch(None, None) is False


def test_server_version_none_when_no_client():
    svc = TiledService.get_instance()
    prev = svc._client
    svc._client = None
    try:
        assert svc.server_version is None
    finally:
        svc._client = prev
