"""Tests for TrustManager and TrustState."""

from __future__ import annotations

import pytest

from lightfall.ipc.trust import TrustManager, TrustState


# ---------------------------------------------------------------------------
# TestTrustState
# ---------------------------------------------------------------------------


class TestTrustState:
    def setup_method(self):
        self.mgr = TrustManager()

    def test_initial_state_empty(self):
        assert not self.mgr.is_trusted("myapp")
        assert not self.mgr.is_denied("myapp")

    def test_approve_adds_to_trusted(self):
        self.mgr.approve("myapp")
        assert self.mgr.is_trusted("myapp")
        assert not self.mgr.is_denied("myapp")

    def test_deny_adds_to_denied(self):
        self.mgr.deny("myapp")
        assert self.mgr.is_denied("myapp")
        assert not self.mgr.is_trusted("myapp")

    def test_revoke_removes_from_trusted(self):
        self.mgr.approve("myapp")
        assert self.mgr.is_trusted("myapp")
        self.mgr.revoke("myapp")
        assert not self.mgr.is_trusted("myapp")
        assert not self.mgr.is_denied("myapp")

    def test_trusted_apps_returns_set(self):
        self.mgr.approve("app1")
        self.mgr.approve("app2")
        result = self.mgr.trusted_apps
        assert result == {"app1", "app2"}

    def test_clear_resets_all(self):
        self.mgr.approve("app1")
        self.mgr.deny("app2")
        self.mgr.clear()
        assert not self.mgr.is_trusted("app1")
        assert not self.mgr.is_denied("app2")
        assert self.mgr.trusted_apps == set()


# ---------------------------------------------------------------------------
# TestTrustDecision
# ---------------------------------------------------------------------------


class TestTrustDecision:
    def setup_method(self):
        self.mgr = TrustManager()

    def test_already_trusted_returns_approved(self):
        self.mgr.approve("myapp")
        assert self.mgr.check("myapp") == TrustState.APPROVED

    def test_already_denied_returns_denied(self):
        self.mgr.deny("myapp")
        assert self.mgr.check("myapp") == TrustState.DENIED

    def test_unknown_app_returns_unknown(self):
        assert self.mgr.check("unknownapp") == TrustState.UNKNOWN
