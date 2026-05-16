"""Job-scoped Tiled API key minting.

Provides `mint_job_key()` and `revoke_job_key()` - thin wrappers over Tiled's
standard /api/v1/auth/apikey endpoint. Used by lucid-pipelines (and tsuchinoko
and any future headless workload) to obtain a short-lived API key that
outlives the user's Keycloak access token.

als-tiled grants `create:apikeys` / `revoke:apikeys` to authenticated users
(see Plan A 2026-05-16-user-scoped-api-keys.md in als-tiled).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import httpx
from loguru import logger


@dataclass(frozen=True)
class MintedJobKey:
    secret: str
    first_eight: str
    expires_at: Optional[str]
    scopes: List[str]
    note: Optional[str]


def mint_job_key(
    tiled_url: str,
    bearer_token: str,
    lifetime: int,
    scopes: List[str],
    note: str,
    *,
    timeout: float = 10.0,
) -> MintedJobKey:
    """Mint a user-scoped Tiled API key.

    Args:
        tiled_url: Base URL of the Tiled API (e.g. "https://bcgtiled.../api/v1").
        bearer_token: Caller's Keycloak access token.
        lifetime: TTL in seconds. Maps to Tiled's `expires_in` field.
        scopes: Scopes to grant. Must be a subset of the caller's scopes.
        note: Free-form audit string (shows up in Tiled's apikey table).

    Returns:
        MintedJobKey with the secret and metadata.

    Raises:
        httpx.HTTPStatusError on a 4xx/5xx from Tiled.
    """
    url = tiled_url.rstrip("/") + "/auth/apikey"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {bearer_token}"},
        json={"expires_in": lifetime, "scopes": scopes, "note": note},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    minted = MintedJobKey(
        secret=body["secret"],
        first_eight=body["first_eight"],
        expires_at=body.get("expiration_time"),
        scopes=body.get("scopes", scopes),
        note=body.get("note"),
    )
    logger.info("minted job key first_eight={} note='{}'", minted.first_eight, minted.note)
    return minted


def revoke_job_key(
    tiled_url: str,
    bearer_token: str,
    *,
    first_eight: str,
    timeout: float = 10.0,
) -> None:
    """Revoke a previously-minted job key. Best-effort; expiry is the backstop."""
    url = tiled_url.rstrip("/") + "/auth/apikey"
    response = httpx.delete(
        url,
        headers={"Authorization": f"Bearer {bearer_token}"},
        params={"first_eight": first_eight},
        timeout=timeout,
    )
    response.raise_for_status()
    logger.info("revoked job key first_eight={}", first_eight)
