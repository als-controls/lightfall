"""Keycloak device-flow login + bcgtiled apikey mint for integration tests.

The full chain produces a 7-day Tiled API key that the executor and
client can both use to talk to bcgtiled. Day 1 prompts the operator for
a browser-side login (Keycloak device flow); day 2-7 reuse the cached
key.

This is standalone code (no SessionManager / Qt dependency) so it can
run from a plain pytest session.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx


_CACHE_PATH = Path.home() / ".cache" / "lightfall-pipelines" / "integration-key.json"
_RENEW_BUFFER = timedelta(days=1)


def _load_proxy() -> Optional[str]:
    """Match Lightfall's *.lbl.gov SOCKS5 routing for local dev boxes."""
    return os.environ.get("LIGHTFALL_INTEGRATION_PROXY") or "socks5h://localhost:1080"


def _cached_key(tiled_url: str) -> Optional[Dict[str, Any]]:
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("tiled_url") != tiled_url:
        return None
    try:
        expires_at = datetime.fromisoformat(data["expires_at"])
    except (KeyError, ValueError):
        return None
    if expires_at - datetime.now(timezone.utc) < _RENEW_BUFFER:
        return None
    return data


def _write_cache(tiled_url: str, secret: str, expires_at: str) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps({
        "tiled_url": tiled_url,
        "secret": secret,
        "expires_at": expires_at,
    }))


def _device_flow(server_url: str, realm: str, client_id: str) -> Dict[str, Any]:
    """OAuth2 device authorization grant against Keycloak. Blocks until
    the user completes browser login or the device_code expires."""
    base = f"{server_url.rstrip('/')}/realms/{realm}/protocol/openid-connect"
    proxy = _load_proxy() if "lbl.gov" in server_url else None

    with httpx.Client(proxy=proxy, timeout=30.0) as cli:
        r = cli.post(
            f"{base}/auth/device",
            data={"client_id": client_id, "scope": "openid"},
        )
        r.raise_for_status()
        init = r.json()

        verify_uri = init.get("verification_uri_complete") or init["verification_uri"]
        print()
        print("=" * 70)
        print("Open this URL in a browser and authenticate with Keycloak:")
        print()
        print(f"    {verify_uri}")
        print()
        print(f"  user_code: {init['user_code']}")
        print(f"  expires in {init['expires_in']}s")
        print("=" * 70)
        print()

        deadline = time.monotonic() + init["expires_in"]
        interval = max(int(init.get("interval", 5)), 1)

        while time.monotonic() < deadline:
            time.sleep(interval)
            poll = cli.post(
                f"{base}/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": init["device_code"],
                    "client_id": client_id,
                },
            )
            if poll.status_code == 200:
                return poll.json()
            err = poll.json().get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 2
                continue
            raise RuntimeError(f"device-flow polling error: {err} ({poll.text})")

    raise TimeoutError("device-flow expired before user completed login")


def _mint_api_key(tiled_url: str, bearer: str) -> Dict[str, Any]:
    """POST to bcgtiled /auth/apikey with a 7-day TTL."""
    proxy = _load_proxy() if "lbl.gov" in tiled_url else None
    url = urljoin(tiled_url.rstrip("/") + "/", "api/v1/auth/apikey")
    with httpx.Client(proxy=proxy, timeout=30.0) as cli:
        r = cli.post(
            url,
            headers={"Authorization": f"Bearer {bearer}"},
            params={"expires_in": 7 * 24 * 60 * 60},
            json={"scopes": ["inherit"]},
        )
    r.raise_for_status()
    return r.json()


def cached_or_login(
    *,
    tiled_url: str,
    server_url: str,
    realm: str,
    client_id: str,
) -> Dict[str, Any]:
    """Return a usable apikey payload (`{"secret": ..., "expires_at": ...}`).

    Reads from the on-disk cache when the key is still good for at least
    a day; otherwise runs the device flow and re-mints.
    """
    cached = _cached_key(tiled_url)
    if cached is not None:
        return cached

    tokens = _device_flow(server_url, realm, client_id)
    minted = _mint_api_key(tiled_url, tokens["access_token"])

    secret = minted["secret"]
    expires_at = minted.get("expires_at")
    if expires_at is None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=7)
        ).isoformat()

    _write_cache(tiled_url, secret, expires_at)
    return {"secret": secret, "expires_at": expires_at, "tiled_url": tiled_url}
