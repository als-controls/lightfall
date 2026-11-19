"""An unanswered trust dialog must not deny the app for the whole session.

Before: a TrustDialog that timed out called TrustManager.deny(), so every
later auth.request from that app was refused instantly with no prompt, and
nothing short of a Lightfall restart cleared it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lightfall.core import application
from lightfall.core.application import LFApplication
from lightfall.ipc.trust import TrustManager, TrustState


class _Timer:
    """QTimer stand-in; ``active`` says whether the 60 s timeout has not fired."""

    active = True

    def setSingleShot(self, _):
        pass

    def start(self, _):
        pass

    def isActive(self):
        return _Timer.active

    @property
    def timeout(self):
        return SimpleNamespace(connect=lambda _cb: None)


def _dialog_returning(code):
    class _Dialog:
        class DialogCode:
            Accepted = 1
            Rejected = 0

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return code

        def reject(self):
            pass

    return _Dialog


class _Ipc:
    def __init__(self, trust):
        self.trust = trust
        self.replies = []

    def evaluate_trust(self, app_name):
        return self.trust.check(app_name)

    def build_auth_response(self, approved, reason=None):
        return {"status": "approved" if approved else "denied", "reason": reason}

    def reply(self, subject, data):
        self.replies.append(data)


@pytest.fixture
def app(monkeypatch):
    import PySide6.QtCore as qtcore

    monkeypatch.setattr(qtcore, "QTimer", _Timer)
    trust = TrustManager()
    ipc = _Ipc(trust)
    fake = SimpleNamespace(
        _auth_dialog_active=False,
        _main_window=None,
        _services=SimpleNamespace(
            get=lambda cls: trust if cls is TrustManager else ipc
        ),
        _approved_auth_reply=lambda ipc_, name: {"status": "approved"},
    )
    return fake, trust, ipc


def _request(fake):
    LFApplication._handle_ipc_auth_request(
        fake, "x.auth.request", {"app_name": "claude-code-bridge"}, "reply"
    )


def test_timeout_does_not_deny_for_the_session(app, monkeypatch):
    fake, trust, ipc = app
    monkeypatch.setattr(application, "TrustDialog", _dialog_returning(0))
    monkeypatch.setattr(_Timer, "active", False)  # the 60 s timer fired
    _request(fake)
    assert ipc.replies[-1] == {"status": "denied", "reason": "timeout"}
    assert trust.check("claude-code-bridge") == TrustState.UNKNOWN


def test_explicit_deny_is_remembered(app, monkeypatch):
    fake, trust, ipc = app
    monkeypatch.setattr(application, "TrustDialog", _dialog_returning(0))
    monkeypatch.setattr(_Timer, "active", True)  # user clicked Deny in time
    _request(fake)
    assert ipc.replies[-1] == {"status": "denied", "reason": "denied"}
    assert trust.check("claude-code-bridge") == TrustState.DENIED
