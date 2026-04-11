"""Tiled client utilities for the exporter."""

from __future__ import annotations

from typing import Any

from tiled.client import from_uri


def connect_tiled(url: str, token: str | None = None) -> Any:
    """Connect to a Tiled server and return the client.

    Args:
        url: Tiled server URL.
        token: Optional auth token (Bearer token for Keycloak).

    Returns:
        Tiled client instance.
    """
    kwargs: dict[str, Any] = {}
    if token:
        kwargs["api_key"] = token
    return from_uri(url, **kwargs)


def get_run(client: Any, uid: str) -> Any:
    """Look up a run by UID in a Tiled catalog.

    Args:
        client: Tiled client (catalog).
        uid: Run UID.

    Returns:
        Tiled run container.

    Raises:
        KeyError: If run not found.
    """
    return client[uid]
