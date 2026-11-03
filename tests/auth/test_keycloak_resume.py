"""Keycloak "Continue as <name>": saved SSO cookies, resume, sign in as someone else."""
from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from lightfall.auth import sso_stash
from lightfall.auth.providers import keycloak as kc
from lightfall.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig
from lightfall.auth.session import Session, User
from lightfall.auth.sso_stash import SsoStash, clear_stash, load_stash, save_stash

ISSUER = "http://keycloak.example/auth/realms/test"
COOKIES = [
    "KEYCLOAK_IDENTITY=abc; secure; HttpOnly; domain=keycloak.example; path=/auth/realms/test/",
    "KEYCLOAK_SESSION=def; secure; domain=keycloak.example; path=/auth/realms/test/",
]


@pytest.fixture
def stash_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "keycloak-sso.json"
    monkeypatch.setattr(sso_stash, "stash_path", lambda: path)
    return path


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    state = {"on": True}
    monkeypatch.setattr(kc, "resume_enabled", lambda: state["on"])
    monkeypatch.setattr(KeycloakAuthProvider, "_is_remote_display", staticmethod(lambda: False))
    return state


@pytest.fixture
def provider() -> KeycloakAuthProvider:
    return KeycloakAuthProvider(
        KeycloakConfig(
            server_url="http://keycloak.example/auth", realm="test", client_id="c"
        )
    )


def _session(username: str = "rp", name: str = "Ron Pandolfi") -> Session:
    return Session(user=User(username=username, display_name=name), id_token="idt")


def _fake_embedded(provider, monkeypatch, *, cookies, seen: dict):
    """Replace the login window: record what it was given, return a code."""

    def fake(auth_url, state, parent_widget=None, restore_cookies=None):
        seen["auth_url"] = auth_url
        seen["restore"] = list(restore_cookies or [])
        provider._captured_cookies = list(cookies)
        return "the-code"

    async def fake_exchange(code, redirect_uri=None):
        seen["code"] = code
        return _session()

    monkeypatch.setattr(provider, "_auth_with_embedded_browser", fake)
    monkeypatch.setattr(provider, "_exchange_code", fake_exchange)


# --- the stash file -------------------------------------------------------


def test_stash_round_trip(stash_file: Path) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron Pandolfi", COOKIES))
    got = load_stash(ISSUER)
    assert got is not None
    assert (got.username, got.label_name, got.cookies) == ("rp", "Ron Pandolfi", COOKIES)


def test_stash_for_other_realm_is_ignored(stash_file: Path) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    assert load_stash("http://keycloak.example/auth/realms/other") is None


def test_unreadable_stash_is_ignored(stash_file: Path) -> None:
    stash_file.write_text("{not json", encoding="utf-8")
    assert load_stash(ISSUER) is None
    stash_file.write_text('{"issuer": "x"}', encoding="utf-8")
    assert load_stash("x") is None


def test_clear_stash_is_idempotent(stash_file: Path) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    clear_stash()
    clear_stash()
    assert not stash_file.exists()


def test_label_falls_back_to_username() -> None:
    assert SsoStash(ISSUER, "rp", "", COOKIES).label_name == "rp"


# --- the provider ---------------------------------------------------------


def test_login_saves_sign_in(provider, stash_file, enabled, monkeypatch) -> None:
    seen: dict = {}
    _fake_embedded(provider, monkeypatch, cookies=COOKIES, seen=seen)

    session = asyncio.run(provider.authenticate())

    assert session is not None
    stash = load_stash(ISSUER)
    assert stash is not None and stash.cookies == COOKIES
    assert stash.display_name == "Ron Pandolfi"
    assert provider.resumable_stash() is not None


def test_login_with_feature_off_forgets_sign_in(provider, stash_file, enabled, monkeypatch) -> None:
    save_stash(SsoStash(ISSUER, "old", "Old User", COOKIES))
    enabled["on"] = False
    _fake_embedded(provider, monkeypatch, cookies=COOKIES, seen={})

    asyncio.run(provider.authenticate())

    assert not stash_file.exists()
    assert provider.resumable_stash() is None


def test_resume_restores_saved_cookies(provider, stash_file, enabled, monkeypatch) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    seen: dict = {}
    _fake_embedded(provider, monkeypatch, cookies=COOKIES, seen=seen)

    asyncio.run(provider.authenticate(resume=True))

    assert seen["restore"] == COOKIES
    assert "prompt" not in parse_qs(urlparse(seen["auth_url"]).query)


def test_someone_else_never_restores_and_forces_login(
    provider, stash_file, enabled, monkeypatch
) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    seen: dict = {}
    other = ["KEYCLOAK_IDENTITY=zzz; domain=keycloak.example; path=/"]
    _fake_embedded(provider, monkeypatch, cookies=other, seen=seen)

    asyncio.run(provider.authenticate(fresh_login=True))

    assert seen["restore"] == []
    assert parse_qs(urlparse(seen["auth_url"]).query)["prompt"] == ["login"]
    # The new login replaces the old one's saved sign-in.
    assert load_stash(ISSUER).cookies == other


def test_plain_login_does_not_restore(provider, stash_file, enabled, monkeypatch) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    seen: dict = {}
    _fake_embedded(provider, monkeypatch, cookies=COOKIES, seen=seen)

    asyncio.run(provider.authenticate())

    assert seen["restore"] == []


def test_no_resume_offer_on_remote_display(provider, stash_file, enabled, monkeypatch) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    monkeypatch.setattr(KeycloakAuthProvider, "_is_remote_display", staticmethod(lambda: True))
    assert provider.resumable_stash() is None


def test_logout_forgets_sign_in(provider, stash_file, enabled, monkeypatch) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    # No id_token: logout returns before any network call.
    asyncio.run(provider.logout(Session(user=User(username="rp"))))
    assert not stash_file.exists()


def test_failed_exchange_keeps_previous_stash(provider, stash_file, enabled, monkeypatch) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    _fake_embedded(provider, monkeypatch, cookies=["X=1"], seen={})

    async def failed(code, redirect_uri=None):
        return None

    monkeypatch.setattr(provider, "_exchange_code", failed)
    assert asyncio.run(provider.authenticate()) is None
    assert load_stash(ISSUER).cookies == COOKIES


def test_resume_without_embedded_browser_forces_password(
    provider, stash_file, enabled, monkeypatch
) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES))
    monkeypatch.setattr(provider, "_auth_with_embedded_browser", lambda *a, **k: None)
    seen: dict = {}

    async def fake_external(auth_url, state):
        seen["auth_url"] = auth_url
        return None

    monkeypatch.setattr(provider, "_auth_with_external_browser", fake_external)
    asyncio.run(provider.authenticate(resume=True))
    assert parse_qs(urlparse(seen["auth_url"]).query)["prompt"] == ["login"]


# --- the brokered identity provider (login.lbl.gov behind "LBL Login") -----


def _fake_embedded_idp(provider, monkeypatch, *, cookies, idp, seen: dict, name="Ron"):
    def fake(auth_url, state, parent_widget=None, restore_cookies=None):
        seen["auth_url"] = auth_url
        seen["restore"] = list(restore_cookies or [])
        provider._captured_cookies = list(cookies)
        provider._captured_idp = idp
        return "the-code"

    async def fake_exchange(code, redirect_uri=None):
        return _session("rp", name)

    monkeypatch.setattr(provider, "_auth_with_embedded_browser", fake)
    monkeypatch.setattr(provider, "_exchange_code", fake_exchange)


def test_login_saves_identity_provider(provider, stash_file, enabled, monkeypatch) -> None:
    _fake_embedded_idp(provider, monkeypatch, cookies=COOKIES, idp="lbl-idp", seen={})
    asyncio.run(provider.authenticate())
    assert load_stash(ISSUER).identity_provider == "lbl-idp"


def test_resume_hints_saved_identity_provider(provider, stash_file, enabled, monkeypatch) -> None:
    save_stash(SsoStash(ISSUER, "rp", "Ron", COOKIES, identity_provider="lbl-idp"))
    seen: dict = {}
    _fake_embedded_idp(provider, monkeypatch, cookies=COOKIES, idp="", seen=seen)

    asyncio.run(provider.authenticate(resume=True))

    query = parse_qs(urlparse(seen["auth_url"]).query)
    assert query["kc_idp_hint"] == ["lbl-idp"]
    # Keycloak's session finished it (no broker visit): the alias is kept.
    assert load_stash(ISSUER).identity_provider == "lbl-idp"


def test_someone_else_gets_no_hint_and_drops_old_alias(
    provider, stash_file, enabled, monkeypatch
) -> None:
    save_stash(SsoStash(ISSUER, "other", "Other", COOKIES, identity_provider="lbl-idp"))
    seen: dict = {}
    _fake_embedded_idp(provider, monkeypatch, cookies=COOKIES, idp="", seen=seen)

    asyncio.run(provider.authenticate(fresh_login=True))

    assert "kc_idp_hint" not in parse_qs(urlparse(seen["auth_url"]).query)
    # A different person: the previous person's alias must not carry over.
    assert load_stash(ISSUER).identity_provider == ""


def test_old_stash_without_alias_still_loads(stash_file: Path) -> None:
    import json

    stash_file.write_text(
        json.dumps({"issuer": ISSUER, "username": "rp", "display_name": "Ron", "cookies": ["A=1"]}),
        encoding="utf-8",
    )
    assert load_stash(ISSUER).identity_provider == ""
