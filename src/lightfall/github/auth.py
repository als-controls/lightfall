"""GitHub sign-in calls: OAuth device flow (GitHub App) and fine-grained PAT.

No token is logged or placed in an exception message; transport errors are
scrubbed before they leave this module. Refresh tokens are never kept.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from lightfall.github import DEFAULT_CLIENT_ID
from lightfall.github.session import GitHubCredential, scrub

DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
EXPIRY_HEADER = "github-authentication-token-expiration"
_TIMEOUT = 15.0


class GitHubAuthError(Exception):
    """Sign-in failure. ``reason`` is expired, denied, cancelled, invalid or network."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


@dataclass(frozen=True)
class DeviceCode:
    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


@dataclass(frozen=True)
class TokenGrant:
    access_token: str = field(repr=False)
    expires_in: int | None


def client_id() -> str:
    """Effective GitHub App client id: the site preference, else the built-in one."""
    try:
        from lightfall.ui.preferences import PreferencesManager

        value = PreferencesManager.get_instance().get("github_app_client_id", "")
    except Exception:
        return DEFAULT_CLIENT_ID
    return str(value).strip() if value and str(value).strip() else DEFAULT_CLIENT_ID


def _default_client(url: str) -> httpx.Client:
    kwargs: dict[str, Any] = {"timeout": _TIMEOUT}
    try:
        from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

        proxy_url = ProxySettingsProvider.should_use_proxy_for_url(url)
        if proxy_url:
            kwargs["proxy"] = proxy_url
    except Exception:
        pass
    return httpx.Client(**kwargs)


@contextmanager
def _use_client(client: httpx.Client | None, url: str) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
        return
    owned = _default_client(url)
    try:
        yield owned
    finally:
        owned.close()


def _network_error(exc: Exception, *tokens: str) -> GitHubAuthError:
    return GitHubAuthError("network", scrub(f"GitHub unreachable: {exc}", *tokens))


def _post_json(
    client: httpx.Client | None, url: str, data: dict[str, str], *secrets: str
) -> dict[str, Any]:
    try:
        with _use_client(client, url) as c:
            resp = c.post(url, data=data, headers={"Accept": "application/json"})
        body = resp.json()
    except httpx.HTTPError as exc:
        raise _network_error(exc, *secrets) from None
    except ValueError:
        raise GitHubAuthError("invalid", "Unexpected response from GitHub") from None
    if not isinstance(body, dict):
        raise GitHubAuthError("invalid", "Unexpected response from GitHub")
    return body


def request_device_code(client_id: str, *, client: httpx.Client | None = None) -> DeviceCode:
    body = _post_json(client, DEVICE_CODE_URL, {"client_id": client_id})
    try:
        return DeviceCode(
            device_code=str(body["device_code"]),
            user_code=str(body["user_code"]),
            verification_uri=str(body["verification_uri"]),
            interval=int(body.get("interval", 5)),
            expires_in=int(body.get("expires_in", 900)),
        )
    except (KeyError, TypeError, ValueError):
        reason = scrub(str(body.get("error_description") or body.get("error") or "bad response"))
        raise GitHubAuthError("invalid", reason) from None


def poll_for_token(
    client_id: str,
    code: DeviceCode,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    cancelled: Callable[[], bool] = lambda: False,
) -> TokenGrant:
    """Poll until the user authorizes. ``refresh_token`` in the response is ignored."""
    interval = code.interval
    start = monotonic()
    form = {
        "client_id": client_id,
        "device_code": code.device_code,
        "grant_type": DEVICE_GRANT_TYPE,
    }
    while True:
        if cancelled():
            raise GitHubAuthError("cancelled", "Sign-in cancelled")
        if monotonic() - start >= code.expires_in:
            raise GitHubAuthError("expired", "Device code expired")
        body = _post_json(client, ACCESS_TOKEN_URL, form, code.device_code)
        token = body.get("access_token")
        if token:
            expires_in = body.get("expires_in")
            return TokenGrant(str(token), int(expires_in) if expires_in is not None else None)
        error = body.get("error")
        if error == "authorization_pending":
            pass
        elif error == "slow_down":
            interval = int(body["interval"]) if body.get("interval") else interval + 5
        elif error == "expired_token":
            raise GitHubAuthError("expired", "Device code expired")
        elif error == "access_denied":
            raise GitHubAuthError("denied", "Authorization denied")
        else:
            detail = scrub(str(body.get("error_description") or error or "bad response"))
            raise GitHubAuthError("invalid", detail)
        sleep(interval)


def validate_pat(token: str) -> None:
    if not token.strip().startswith("github_pat_"):
        raise GitHubAuthError("invalid", "Only fine-grained tokens (github_pat_...) are accepted")


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("UTC"):
        text = text[:-3].rstrip() + " +0000"
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
    return parsed.astimezone(UTC)


def fetch_identity(
    token: str, *, client: httpx.Client | None = None
) -> tuple[str, datetime | None]:
    """Return ``(login, expires_at)``; ``expires_at`` is timezone-aware or None."""
    token = token.strip()
    try:
        with _use_client(client, USER_URL) as c:
            resp = c.get(
                USER_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
    except httpx.HTTPError as exc:
        raise _network_error(exc, token) from None
    if resp.status_code == 401:
        raise GitHubAuthError("invalid", "GitHub rejected the token")
    if resp.status_code != 200:
        raise GitHubAuthError("invalid", f"GitHub returned HTTP {resp.status_code}")
    try:
        login = str(resp.json()["login"])
    except (ValueError, KeyError, TypeError):
        raise GitHubAuthError("invalid", "Unexpected response from GitHub") from None
    return login, _parse_expiry(resp.headers.get(EXPIRY_HEADER))


def credential_from_grant(
    grant: TokenGrant,
    lightfall_user: str,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    client: httpx.Client | None = None,
) -> GitHubCredential:
    login, header_expiry = fetch_identity(grant.access_token, client=client)
    if grant.expires_in is not None:
        expires_at: datetime | None = now() + timedelta(seconds=grant.expires_in)
    else:
        expires_at = header_expiry
    return GitHubCredential(grant.access_token, "app", login, expires_at, lightfall_user)


def credential_from_pat(
    token: str, lightfall_user: str, *, client: httpx.Client | None = None
) -> GitHubCredential:
    validate_pat(token)
    token = token.strip()
    login, expires_at = fetch_identity(token, client=client)
    return GitHubCredential(token, "pat", login, expires_at, lightfall_user)
