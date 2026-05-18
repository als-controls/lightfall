"""Per-service API key minting.

Provides `mint_service_key()` and `revoke_service_key()` — thin wrappers over
the Tiled-shape /api/v1/auth/apikey endpoint contract documented in the auth-v2
spec. Used by SessionManager at login to obtain a per-(user, service) API key
that outlives the Keycloak access token.

Every service implementing the contract (als-tiled today, lucid-logbook next)
accepts the same request shape and returns the same response shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from loguru import logger


@dataclass(frozen=True)
class MintedKey:
    secret: str
    first_eight: str
    expires_at: datetime | None
    scopes: tuple[str, ...]
    note: str | None

    @property
    def is_expired(self) -> bool:
        """Return True if the key's expiry is in the past.

        A None expiry is treated as "no expiry known" and reports False — the
        server may have omitted the field, and a 401 will catch real expiry on
        next request.
        """
        if self.expires_at is None:
            return False
        return datetime.now(UTC) >= self.expires_at


def mint_service_key(
    service_url: str,
    bearer_token: str,
    *,
    expires_in: int,
    scopes: list[str],
    note: str,
    timeout: float = 10.0,
) -> MintedKey:
    """Mint a user-scoped API key for a LUCID-protected service.

    Args:
        service_url: Base URL of the service's API root
            (e.g. "https://bcgtiled.../api/v1").
        bearer_token: Caller's Keycloak access token. Used only for this one
            call; the server validates it and discards.
        expires_in: TTL in seconds. Maps to the request body's `expires_in`
            field. The auth-v2 spec calls for 604800 (1 week) by default.
        scopes: Scopes to grant. Subset of the caller's scopes; the server
            enforces "may only grant scopes you have."
        note: Free-form audit string surfaced in the service's apikey table /
            JWT claims.
        timeout: httpx timeout in seconds.

    Returns:
        MintedKey with secret and metadata.

    Raises:
        httpx.HTTPStatusError on a 4xx/5xx from the service.
    """
    url = service_url.rstrip("/") + "/auth/apikey"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {bearer_token}"},
        json={"expires_in": expires_in, "scopes": scopes, "note": note},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()

    expires_at: datetime | None = None
    raw_expires = body.get("expiration_time")
    if raw_expires:
        try:
            # ISO-8601 with trailing Z or +00:00; fromisoformat handles both
            # in 3.11+ when Z is replaced.
            expires_at = datetime.fromisoformat(raw_expires.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "could not parse expiration_time from mint response: {}",
                raw_expires,
            )

    minted = MintedKey(
        secret=body["secret"],
        first_eight=body["first_eight"],
        expires_at=expires_at,
        scopes=tuple(body.get("scopes", scopes)),
        note=body.get("note"),
    )
    logger.info("minted service key first_eight={} note='{}'", minted.first_eight, minted.note)
    return minted


def revoke_service_key(
    service_url: str,
    bearer_token: str,
    *,
    first_eight: str,
    timeout: float = 10.0,
) -> None:
    """Revoke a previously-minted service key.

    Best-effort: any error talking to the service is logged and swallowed
    (the key's TTL is the backstop). Callers can safely place this in a
    `finally` block without worrying about a transient revoke failure masking
    the original exception.
    """
    url = service_url.rstrip("/") + "/auth/apikey"
    try:
        response = httpx.delete(
            url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={"first_eight": first_eight},
            timeout=timeout,
        )
        response.raise_for_status()
        logger.info("revoked service key first_eight={}", first_eight)
    except (httpx.HTTPError, httpx.HTTPStatusError) as e:
        logger.warning("revoke failed first_eight={} err={}", first_eight, e)
