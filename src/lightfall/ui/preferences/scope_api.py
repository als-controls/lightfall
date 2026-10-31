"""Scope-aware read API behind PreferencesManager (scope selector and forms use it).

Pure functions over a manager; the manager's methods of the same name delegate here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lightfall_settings import ScopeId, role_at_least
from lightfall_settings.scopes import KIND_RANK

from lightfall.scopes.keys import LOCAL_LAYER_KEYS, client_registry, console_only

if TYPE_CHECKING:
    from lightfall.ui.preferences.manager import PreferencesManager

LOCAL = "local"
_LOCAL_RANK = 2.5  # this console's layer sits between branch and user
_KIND_LABELS = {"facility": "Facility", "beamline": "Beamline", "branch": "Branch"}
_READ_ONLY_FILES = "Read-only: scope files on this console"
_READ_ONLY_FACILITY = "Read-only: facility settings are managed by facility admins"


@dataclass(frozen=True)
class ScopeChoice:
    scope: str             # "user:u1" | "local" | "branch:7.0.1.1" | "beamline:7.0.1" | "facility"
    kind: str              # "user" | "local" | "branch" | "beamline" | "facility"
    label: str
    role: str | None
    read_only_reason: str  # "" when the caller can write at least one key here


@dataclass(frozen=True)
class LockInfo:
    locked_at: str         # scope id that holds the lock
    locked_by: str
    reason: str


def _label(scope: str, facility_label: str = "") -> str:
    kind = ScopeId.parse(scope).kind
    name = scope.partition(":")[2]
    if kind == "user":
        return f"User — {name}"
    if kind == "facility":
        return f"{_KIND_LABELS['facility']} \u2014 {facility_label or 'facility'}"
    return f"{_KIND_LABELS[kind]} {name}"


def _local_choice() -> ScopeChoice:
    return ScopeChoice(LOCAL, LOCAL, "This console", None, "")


def scope_choices(prefs: PreferencesManager) -> list[ScopeChoice]:
    """Scopes the caller can address, most specific first."""
    info = prefs.scope_info
    scoped = prefs.scoped_backend
    fac = getattr(scoped, "facility_label", "") if info.mode == "server" and scoped else ""
    if info.mode == "off":
        return [_local_choice()]
    if info.mode == "local":
        return [_local_choice()] + [
            ScopeChoice(s, ScopeId.parse(s).kind, _label(s, fac), None, _READ_ONLY_FILES)
            for s in reversed(info.chain)
        ]
    out: list[ScopeChoice] = []
    users = [s for s in info.chain if ScopeId.parse(s).kind == "user"]
    for s in users:
        out.append(ScopeChoice(s, "user", _label(s), info.roles.get(s), ""))
    out.append(_local_choice())
    for s in reversed([s for s in info.chain if s not in users]):
        kind, role = ScopeId.parse(s).kind, info.roles.get(s)
        if kind == "facility":
            reason = _READ_ONLY_FACILITY
        elif role_at_least(role, "operator"):
            reason = ""
        else:
            reason = f"Read-only: you are a {role or 'not a member'} here"
        out.append(ScopeChoice(s, kind, _label(s, fac), role, reason))
    return out


def allowed_at(prefs: PreferencesManager, key: str, scope: str) -> bool:
    """Whether ``key`` may ever hold a value at ``scope`` (regardless of role)."""
    if console_only(key):
        return scope == LOCAL
    if scope == LOCAL:
        return key in LOCAL_LAYER_KEYS if prefs.scope_info.mode == "server" else True
    return client_registry().get(key).allows(ScopeId.parse(scope).kind)


def writable_at(prefs: PreferencesManager, key: str, scope: str) -> bool:
    """Whether the caller can write ``key`` at ``scope`` right now."""
    if console_only(key) or prefs.scope_info.mode == "local":
        return scope == LOCAL
    return scope in prefs.write_targets(key)


def lock_at(prefs: PreferencesManager, key: str, scope: str) -> LockInfo | None:
    """The lock that blocks edits of ``key`` at ``scope``, if held at a broader scope."""
    res: Any = prefs.resolution(key)
    if res is None or res.locked_at is None or res.lock is None:
        return None
    here = _LOCAL_RANK if scope == LOCAL else KIND_RANK[ScopeId.parse(scope).kind]
    if KIND_RANK[res.locked_at.kind] >= here:
        return None
    return LockInfo(str(res.locked_at), res.lock.locked_by, res.lock.reason)


def default_scope(prefs: PreferencesManager) -> str:
    """Where edits land by default: the user's scope when logged in, else this console."""
    scoped = prefs.scoped_backend
    if prefs.scope_info.mode == "server" and scoped is not None:
        return scoped.user_scope or LOCAL
    return LOCAL
