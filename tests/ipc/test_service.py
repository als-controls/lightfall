"""Tests for Lightfall's wiring onto IPCService's auth handshake.

The pure IPCService tests (topic builder, connection lifecycle, pub/sub,
data classes, message handler, trust evaluation, request/reply, instance
identity) moved to lightfall-utils along with the implementation.
"""

from __future__ import annotations

from unittest.mock import MagicMock


class TestAuthHandshake:
    def test_approved_auth_reply_uses_cached_tiled_key(self):
        """Approved reply carries the cached Tiled API key under the
        legacy ``tiled_token`` field name (auth-v2), supplied by
        LFApplication rather than the shared service."""
        from datetime import UTC, datetime, timedelta

        from lightfall.auth.service_key import MintedKey
        from lightfall.auth.session import SessionManager
        from lightfall.core.application import LFApplication
        from lightfall.ipc.service import IPCService

        SessionManager.reset()
        try:
            sm = SessionManager.get_instance()
            sm._service_keys["tiled"] = MintedKey(
                secret="tiled-apikey-xyz",
                first_eight="tiled-ap",
                expires_at=datetime.now(UTC) + timedelta(seconds=3600),
                scopes=("read:metadata",),
                note="test",
            )

            ipc = MagicMock(spec=IPCService)
            ipc.build_auth_response.return_value = {
                "status": "approved",
                "tiled_token": "tiled-apikey-xyz",
                "tiled_url": "https://tiled.example.com",
                "session_id": "user-abc-123",
                "contract_version": 1,
            }

            mock_session = MagicMock()
            mock_session.user.attributes = {"sub": "user-abc-123"}

            app = LFApplication.__new__(LFApplication)
            app._get_current_session = lambda: mock_session
            app._get_tiled_url = lambda: "https://tiled.example.com"

            resp = app._approved_auth_reply(ipc, "pystxm")

            kwargs = ipc.build_auth_response.call_args.kwargs
            assert kwargs["approved"] is True
            assert kwargs["app_name"] == "pystxm"
            assert kwargs["extra"] == {
                "tiled_token": "tiled-apikey-xyz",
                "tiled_url": "https://tiled.example.com",
                "session_id": "user-abc-123",
            }
            assert resp == {
                "status": "approved",
                "tiled_token": "tiled-apikey-xyz",
                "tiled_url": "https://tiled.example.com",
                "session_id": "user-abc-123",
                "contract_version": 1,
            }
        finally:
            SessionManager.reset()
