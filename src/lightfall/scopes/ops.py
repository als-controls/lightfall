"""Blocking scoped-settings operations, run on worker threads (no Qt here)."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from lightfall_settings import SpecRegistry

from lightfall.scopes.client import ScopesClient, ScopesError
from lightfall.scopes.keys import is_local_only
from lightfall.utils.logging import logger


def push_write(client: ScopesClient, scope: str, key: str, op: str, value: Any,
               version: int | None) -> int:
    """PUT or DELETE one value. Returns the new version (0 after a delete).

    A 412 means our version is stale (the user wrote from another machine). User
    scope has one writer, so refetch the version once and retry: last write wins.
    """
    for attempt in (1, 2):
        try:
            if op == "delete":
                client.delete_setting(scope, key)
                return 0
            return client.put_setting(scope, key, value, version=version)
        except ScopesError as e:
            if e.status != 412 or attempt == 2:
                raise
            snap = client.snapshot(scope)
            version = snap.versions.get(key) if snap is not None else None
    raise AssertionError("unreachable")


def _json_ok(key: str, value: Any) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError) as e:
        logger.warning("Not migrating {}: value is not JSON ({})", key, e)
        return False
    return True


def migrate_user_prefs(client: ScopesClient, user_scope: str, prefs: Mapping[str, Any],
                       registry: SpecRegistry) -> list[str]:
    """Upload this machine's YAML preferences to an empty user scope (spec section 5).

    Skips local-only and secret keys, keys whose spec forbids user scope, None and
    non-JSON values. Per-key server rejections are logged and skipped; a transport
    failure propagates so the caller retries the whole migration later.
    """
    uploaded: list[str] = []
    for key in sorted(prefs):
        value = prefs[key]
        if value is None or is_local_only(key) or not registry.get(key).allows("user"):
            continue
        if not _json_ok(key, value):
            continue
        try:
            client.put_setting(user_scope, key, value, version=None)
        except ScopesError as e:
            if e.status is None:
                raise
            logger.warning("Not migrating {}: {}", key, e)
            continue
        uploaded.append(key)
    return uploaded


def replay_journal(client: ScopesClient, user_scope: str,
                   entries: Mapping[str, Mapping[str, Any]],
                   versions: Mapping[str, int | None]
                   ) -> tuple[list[str], list[tuple[str, str]]]:
    """Replay offline writes. Returns (applied keys, [(dropped key, reason)]).

    Stops at the first transport failure; unreplayed entries stay journaled.
    """
    done: list[str] = []
    dropped: list[tuple[str, str]] = []
    for key, entry in entries.items():
        if is_local_only(key):
            dropped.append((key, "local-only key"))
            continue
        try:
            push_write(client, user_scope, key, str(entry.get("op")), entry.get("value"),
                       versions.get(key))
        except ScopesError as e:
            if e.status is None:
                break
            dropped.append((key, str(e)))
            continue
        done.append(key)
    return done, dropped
