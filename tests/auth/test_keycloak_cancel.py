"""KeycloakAuthProvider external-browser auth must be cancellable.

The login runs on a QThreadFuture that blocks waiting for the OAuth redirect.
When the user cancels, the thread must unblock promptly via an interrupt hook
(provider.cancel()) — otherwise cancel() has nothing to interrupt and the
thread lingers until the callback timeout (or, historically, gets
force-terminated and crashes the process with 0xC0000005).
"""
from __future__ import annotations

import asyncio
import threading
import time
import webbrowser

import pytest

from lightfall.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig


@pytest.fixture
def provider() -> KeycloakAuthProvider:
    config = KeycloakConfig(
        server_url="http://keycloak.example/auth",
        realm="test",
        client_id="test-client",
    )
    # Long timeout so a *prompt* return can only be the result of cancel(),
    # not the callback timing out on its own.
    return KeycloakAuthProvider(config, callback_timeout=60)


def test_cancel_unblocks_external_browser_auth(
    provider: KeycloakAuthProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Don't actually launch a browser during the test.
    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: True)

    result: dict[str, object] = {}

    def run() -> None:
        result["session"] = asyncio.run(
            provider._auth_with_external_browser(
                "http://keycloak.example/auth/realms/test/protocol/openid-connect/auth?x=1",
                "state-123",
            )
        )

    worker = threading.Thread(target=run)
    worker.start()
    # Let it bind the callback server and begin waiting.
    time.sleep(0.5)

    provider.cancel()

    worker.join(timeout=5)
    assert not worker.is_alive(), (
        "external-browser auth did not return promptly after cancel() — "
        "cancellation has nothing to unblock the callback wait"
    )
    assert result["session"] is None
