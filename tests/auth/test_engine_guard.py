"""Engine permission guard: SCAN_RUN check (warn / enforce) and submitter stamp."""

from __future__ import annotations

import pytest

from lightfall.acquire.engine.mock import MockEngine
from lightfall.auth import engine_guard
from lightfall.auth.policy import Role
from lightfall.auth.session import Session, SessionManager, User


@pytest.fixture
def session():
    SessionManager.reset()
    yield SessionManager.get_instance()
    SessionManager.reset()


@pytest.fixture
def mode(monkeypatch):
    state = {"mode": "warn"}
    monkeypatch.setattr(engine_guard, "permission_mode", lambda: state["mode"])
    return state


def _sign_in(session, role=Role.USER):
    session.attach_session(Session(user=User(username="ada", roles={role})))


def test_signed_in_user_passes_and_is_stamped(session, mode):
    _sign_in(session)
    assert engine_guard.authorize_submit("scan", {}) is None
    assert engine_guard.submitter_metadata() == {"submitted_by": "ada"}


def test_anonymous_session_is_reported(session):
    reason = engine_guard.authorize_submit("scan", {})
    assert reason and "SCAN_RUN" in reason and "scan" in reason
    assert engine_guard.submitter_metadata() == {"submitted_by": "anonymous"}


def test_warn_mode_runs_the_plan_and_records_the_warning(session, mode):
    kwargs: dict = {}
    assert engine_guard._guard("scan", kwargs) is None
    assert "SCAN_RUN" in kwargs["permission_warning"]


def test_enforce_mode_refuses_at_submit(session, mode, qtbot):
    mode["mode"] = "enforce"
    engine = MockEngine()
    engine_guard.install(engine)
    with pytest.raises(PermissionError):
        engine.submit("anything", name="scan", skip_pre_submit=True)
    _sign_in(session)
    assert engine.submit("anything", name="scan", skip_pre_submit=True) is not None


def test_skip_pre_submit_does_not_bypass_authorization(qtbot):
    engine = MockEngine()
    engine.set_authorizer(lambda name, kwargs: "no")
    with pytest.raises(PermissionError):
        engine.submit("x", name="scan", skip_pre_submit=True)


def test_bare_engine_has_no_check(qtbot):
    assert MockEngine().submit("x", name="scan") is not None


def test_mode_preference_defaults_to_warn(monkeypatch):
    assert engine_guard.permission_mode() in engine_guard.MODES
