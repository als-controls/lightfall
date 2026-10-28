"""Regression tests for the 2026-10 bug hunt: auth, services, claude, monitor."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from http.server import HTTPServer
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from lightfall.auth.policy import Permission
from lightfall.auth.providers.keycloak import KeycloakAuthProvider, _OAuthCallbackHandler
from lightfall.auth.providers.local import LocalAuthProvider
from lightfall.auth.session import SessionManager
from lightfall.claude.markdown import render_markdown
from lightfall.services.caproxy_lease_service import CaproxyLeaseService

# --- LocalAuthProvider: users from a file can log in ------------------------


def test_users_file_passwords_authenticate(tmp_path):
    users = tmp_path / "users.yaml"
    users.write_text(
        "users:\n"
        "  alice:\n"
        f"    password_hash: \"{hashlib.sha256(b's3cret').hexdigest()}\"\n"
        "    roles: [user]\n"
        "  bob:\n"
        "    password: plain-pw\n",
        encoding="utf-8",
    )
    provider = LocalAuthProvider(users_file=users)

    def login(user, pw):
        try:
            return asyncio.run(provider.authenticate(username=user, password=pw))
        except Exception:  # noqa: BLE001 - a refused login may raise instead
            return None

    assert login("alice", "s3cret") is not None
    assert login("bob", "plain-pw") is not None
    assert login("alice", "wrong") is None
    # Programmatic users keep working (salted hashes).
    assert login("admin", "admin") is not None


# --- SessionManager.require_permission in offline mode -----------------------


def test_offline_require_permission_allows_view_permissions(qapp, monkeypatch):
    sm = SessionManager.get_instance()
    engine = MagicMock()
    engine.check_permission.return_value = True
    monkeypatch.setattr(sm, "_policy_engine", engine)
    monkeypatch.setattr(sm, "_offline_mode", True)

    sm.require_permission(Permission.DEVICE_VIEW)  # must not raise
    with pytest.raises(PermissionError):
        sm.require_permission(Permission.SCAN_RUN)


# --- Keycloak external-browser callback server ------------------------------


def test_callback_server_survives_stray_requests():
    _OAuthCallbackHandler.callback_result = None
    _OAuthCallbackHandler.error = None
    server = HTTPServer(("localhost", 0), _OAuthCallbackHandler)
    server.timeout = 0.5
    stop = threading.Event()
    thread = threading.Thread(
        target=KeycloakAuthProvider._serve_until_callback, args=(server, stop), daemon=True
    )
    thread.start()
    port = server.server_address[1]
    try:
        # A browser probe before the redirect used to consume the only accept.
        with pytest.raises(HTTPError):
            urlopen(f"http://localhost:{port}/favicon.ico", timeout=5)
        urlopen(f"http://localhost:{port}/callback?code=abc&state=xyz", timeout=5).read()
        thread.join(5)
        assert not thread.is_alive()
        assert _OAuthCallbackHandler.callback_result == {"code": "abc", "state": "xyz"}
    finally:
        stop.set()
        server.server_close()
        _OAuthCallbackHandler.callback_result = None


# --- CaproxyLeaseService: auth headers refreshed every poll ----------------


def test_poll_loop_rereads_auth_headers_each_cycle(qapp):
    CaproxyLeaseService.reset()
    service = CaproxyLeaseService.get_instance()
    seen = []
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client

    def _get(url, headers=None):
        seen.append(dict(headers or {}))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = []
        return resp

    fake_client.get.side_effect = _get
    stop = threading.Event()
    tokens = iter([{"Authorization": "Bearer one"}, {"Authorization": "Bearer two"}])
    try:
        with patch("httpx.Client", return_value=fake_client), patch(
            "lightfall.services.caproxy_lease_service._auth_headers",
            side_effect=lambda: next(tokens),
        ), patch("lightfall.services.caproxy_lease_service.POLL_INTERVAL_S", 0.0):
            loop = service._poll_loop("http://caproxy", stop)
            next(loop)
            next(loop)
            stop.set()
    finally:
        CaproxyLeaseService.reset()
    assert [h["Authorization"] for h in seen] == ["Bearer one", "Bearer two"]


# --- Markdown code blocks with punctuation in the language id --------------


def test_code_block_language_with_punctuation_is_highlighted():
    out = render_markdown("```c++\nint x = 1;\n```")
    assert 'class="language-c++"' not in out
    assert "int" in out


# --- ThreadedTiledWriter.flush honours its timeout --------------------------


def test_flush_times_out_instead_of_hanging(qapp):
    from lightfall.services.threaded_tiled_writer import ThreadedTiledWriter

    release = threading.Event()

    def slow_writer(name, doc):
        release.wait(5)

    writer = ThreadedTiledWriter(slow_writer)
    try:
        writer("start", {})
        assert writer.flush(timeout=0.3) is False
        release.set()
        assert writer.flush(timeout=5.0) is True
    finally:
        release.set()
        writer.stop()


# --- Permission card escapes tool arguments ---------------------------------


def test_permission_widget_escapes_tool_arguments(qapp):
    from lightfall.claude.widgets.permission_request import PermissionRequestWidget

    w = PermissionRequestWidget("req-1", "click_widget", {"widget_name": "<unnamed_QPushButton>"})
    try:
        assert "&lt;unnamed_QPushButton&gt;" in w.info_label.text()
    finally:
        w.deleteLater()
