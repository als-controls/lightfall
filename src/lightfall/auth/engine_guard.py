"""Permission enforcement at the acquisition engine, the one choke point every
queued plan passes through (GUI panels, agent plan tools, checkpoint
restores, IPC/remote submissions, plugin panels).

``install(engine)``:
- checks ``SCAN_RUN`` for every procedure queued (whoever the caller is, and
  regardless of ``skip_pre_submit``). The ``engine_permission_mode``
  preference decides what a failed check does:
  - ``"warn"`` (default): log a WARNING naming the plan and the user, and
    record ``permission_warning`` in the run's start document; the plan runs.
  - ``"enforce"``: ``engine.submit`` raises ``PermissionError``.
  Warn is the default while we learn who would be affected (guest sessions
  and offline mode, which only grants view permissions, both lack SCAN_RUN
  but run plans from the GUI today).
- stamps every run's start document with ``submitted_by``, the signed-in
  username when the plan starts, so runs are attributable.

Stopping is never gated here: pause/stop/abort/halt stay available to
everyone, because refusing to stop hardware is the unsafe failure.

This is a client-side guard against mistakes and over-eager automation, not a
security boundary: code running in the same process (the IPython console,
plugins) can still drive devices directly. Hard gating belongs outside the
client (IOC access security, gateways).
"""

from __future__ import annotations

from typing import Any

from lightfall.utils.logging import logger


def _session():
    from lightfall.auth.session import SessionManager

    return SessionManager.get_instance()


def authorize_submit(name: str, kwargs: dict[str, Any]) -> str | None:
    """Denial reason for queueing ``name``, or None when allowed."""
    from lightfall.auth.policy import Permission

    session = _session()
    if session.check_permission(Permission.SCAN_RUN):
        return None
    user = session.current_user
    who = getattr(user, "username", None) or "the current session"
    return (f"Permission denied: SCAN_RUN required to run '{name}' "
            f"({who} is not allowed to run plans; sign in with a user account)")


def submitter_metadata() -> dict[str, Any]:
    """Start-document metadata naming who is signed in."""
    session = _session()
    if not session.is_authenticated:
        return {"submitted_by": "anonymous"}
    user = session.current_user
    return {"submitted_by": getattr(user, "username", "") or "unknown"}


MODE_PREF = "engine_permission_mode"
MODES = ("warn", "enforce")


def permission_mode() -> str:
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        mode = str(PreferencesManager.get_instance().get(MODE_PREF, "warn") or "warn").lower()
    except Exception:  # noqa: BLE001
        mode = "warn"
    return mode if mode in MODES else "warn"


def _guard(name: str, kwargs: dict[str, Any]) -> str | None:
    reason = authorize_submit(name, kwargs)
    if reason is None:
        return None
    if permission_mode() == "enforce":
        return reason
    logger.warning("Permission check (warn mode, not enforced): {}", reason)
    kwargs["permission_warning"] = reason
    return None


def install(engine) -> None:
    """Check SCAN_RUN on ``engine.submit`` (warn or enforce, per the
    ``engine_permission_mode`` preference) and stamp runs with the submitter."""
    engine.set_authorizer(_guard)
    subscribe = getattr(engine, "subscribe_kwargs_callable", None)
    if callable(subscribe):
        subscribe(submitter_metadata)
    logger.info("Engine permission guard installed (mode: {})", permission_mode())
