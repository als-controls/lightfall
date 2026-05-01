"""Shared httpx.Auth adapter that pulls the Bearer token fresh from the
SessionManager on every request.

Used by any client that talks to a Keycloak-protected service: LogbookClient,
UserSettingsClient, etc. Reading per-request keeps refreshed tokens working
during long-running operations.
"""
from __future__ import annotations

import httpx


class SessionAuth(httpx.Auth):
    """httpx auth that injects the current Bearer token per request.

    Optionally also sets ``X-User-Id`` for dev/testing when a fixed user
    id is desired (e.g., when Keycloak is disabled on the server).
    """

    def __init__(self, user_id: str | None = None) -> None:
        self._user_id = user_id

    def sync_auth_flow(self, request):
        try:
            from lucid.auth.session import SessionManager
            session = SessionManager.get_instance().session
            if session and session.token:
                request.headers["Authorization"] = f"Bearer {session.token}"
        except Exception:
            pass
        if self._user_id:
            request.headers["X-User-Id"] = self._user_id
        yield request
