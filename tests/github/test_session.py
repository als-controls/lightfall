"""Tests for the session-bound in-memory GitHub credential."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from lightfall.auth.session import AuthState
from lightfall.github.session import GitHubCredential, GitHubSession, scrub

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


class FakeAuth(QObject):
    state_changed = Signal(object, object)
    user_changed = Signal(object)

    def __init__(self, username="u1"):
        super().__init__()
        self.current_user = SimpleNamespace(username=username)


def make_cred(user="u1", expires_at=None, token="github_pat_SECRET"):
    return GitHubCredential(
        token=token, kind="pat", login="octo", expires_at=expires_at, lightfall_user=user
    )


@pytest.fixture
def auth(qapp):
    return FakeAuth()


@pytest.fixture
def gh(auth):
    return GitHubSession(auth_session=auth, now=lambda: NOW)


def test_set_and_get(gh):
    seen = []
    gh.changed.connect(lambda: seen.append(1))
    gh.set_credential(make_cred())
    assert gh.credential().login == "octo"
    assert len(seen) == 1


def test_repr_hides_token():
    assert "github_pat_SECRET" not in repr(make_cred())


def test_masked():
    assert make_cred().masked == "signed in as octo"
    exp = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
    assert make_cred(expires_at=exp).masked == f"signed in as octo until {exp.astimezone():%H:%M}"


def test_cleared_on_logout(gh, auth):
    gh.set_credential(make_cred())
    auth.state_changed.emit(AuthState.UNAUTHENTICATED, AuthState.AUTHENTICATED)
    assert gh.credential() is None


def test_kept_on_authenticated(gh, auth):
    gh.set_credential(make_cred())
    auth.state_changed.emit(AuthState.AUTHENTICATED, AuthState.AUTHENTICATING)
    assert gh.credential() is not None


def test_cleared_on_user_change(gh, auth):
    gh.set_credential(make_cred())
    auth.user_changed.emit(SimpleNamespace(username="u2"))
    assert gh.credential() is None


def test_other_user_sees_nothing(gh, auth):
    gh.set_credential(make_cred(user="u1"))
    auth.current_user.username = "u2"
    assert gh.credential() is None


def test_expired_credential_is_none(gh):
    gh.set_credential(make_cred(expires_at=NOW - timedelta(seconds=1)))
    assert gh.credential() is None


def test_sign_out(gh):
    seen = []
    gh.set_credential(make_cred())
    gh.changed.connect(lambda: seen.append(1))
    gh.sign_out()
    assert gh.credential() is None
    assert seen == [1]


def test_scrub(gh):
    assert scrub("x github_pat_ABC y", "github_pat_ABC") == "x *** y"
    assert scrub("abc", "") == "abc"


def test_scrub_defaults_to_held_token(gh, monkeypatch):
    monkeypatch.setattr("lightfall.github.session.get_github_session", lambda: gh)
    gh.set_credential(make_cred(expires_at=NOW - timedelta(seconds=1)))
    assert scrub("a github_pat_SECRET b") == "a *** b"


def _in_worker(func):
    import threading

    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("value", func()))
    thread.start()
    thread.join(5)
    return box["value"]


def test_session_made_on_worker_still_clears_on_logout(auth):
    from PySide6.QtCore import QCoreApplication

    gh = _in_worker(lambda: GitHubSession(auth_session=auth, now=lambda: NOW))
    assert gh.thread() is QCoreApplication.instance().thread()
    gh.set_credential(make_cred())
    auth.state_changed.emit(AuthState.UNAUTHENTICATED, AuthState.AUTHENTICATED)
    assert gh.credential() is None
    gh.set_credential(make_cred())
    auth.user_changed.emit(SimpleNamespace(username="u1"))
    assert gh.credential() is None


def test_singleton_made_on_worker_clears_on_logout(auth, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    from lightfall.auth.session import SessionManager
    from lightfall.github import session as session_mod

    monkeypatch.setattr(SessionManager, "get_instance", classmethod(lambda cls: auth))
    session_mod._reset_for_tests()
    try:
        gh = _in_worker(session_mod.get_github_session)
        assert gh.thread() is QCoreApplication.instance().thread()
        gh.set_credential(make_cred())
        auth.state_changed.emit(AuthState.UNAUTHENTICATED, AuthState.AUTHENTICATED)
        assert gh.credential() is None
        # Same user logs back in: the old token must not come back.
        auth.state_changed.emit(AuthState.AUTHENTICATED, AuthState.AUTHENTICATING)
        assert gh.credential() is None
    finally:
        session_mod._reset_for_tests()
