"""Static per-backend facts for the UI: name, capabilities, sign-in, models.

The settings page, model picker, cockpit, preamble and speaker strings ask
``backend_info()`` instead of branching on the backend kind. Nothing here needs
a live session. ``auth_status`` / ``list_models`` block (agy runs ``agy models``,
up to 30 s): call them off the GUI thread. ``cached_models`` never blocks.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from lightfall.agents.backends.select import AGY, CLAUDE_CODE, backend_kind
from lightfall.agents.tooling import tool_id


@dataclass(frozen=True)
class BackendCaps:
    cost: bool            # TurnResult carries real USD
    live_set_model: bool  # a model change applies without a respawn
    subagents: bool       # the backend runs SessionConfig.subagents


@dataclass(frozen=True)
class AuthStatus:
    ok: bool
    detail: str


@dataclass(frozen=True)
class ModelOption:
    id: str     # "" = the backend's default
    label: str


_CLAUDE_PROMPT_NOTES = """### Waiting

Do not use `ScheduleWakeup` to wait for the engine: it does not fire in Lightfall's
embedded session, so a scheduled wakeup silently drops and the conversation stalls
until the user nudges you. Use `lightfall_wait_for_idle` instead."""


class ClaudeCodeInfo:
    kind = CLAUDE_CODE
    display_name = "Claude"
    caps = BackendCaps(cost=True, live_set_model=True, subagents=True)
    prompt_notes = _CLAUDE_PROMPT_NOTES
    model_pref_key = "claude_model"

    def auth_status(self, refresh: bool = False) -> AuthStatus:
        from lightfall.ui.preferences.claude_settings import (
            ClaudeSettingsProvider,
            check_oauth_status,
        )

        try:
            if ClaudeSettingsProvider.get_api_key():
                return AuthStatus(True, "Using API key")
            ok, msg = check_oauth_status()
            return AuthStatus(ok, msg)
        except Exception as exc:  # noqa: BLE001 - status is best-effort
            return AuthStatus(False, f"Couldn't check: {exc}")

    def list_models(self, refresh: bool = False) -> list[ModelOption]:
        from lightfall.ui.preferences.claude_settings import (
            ClaudeSettingsProvider,
            get_cached_models,
        )

        get_cached_models(ClaudeSettingsProvider.get_base_url(),
                          ClaudeSettingsProvider.get_api_key(), refresh=refresh)
        return self.cached_models()

    def cached_models(self) -> list[ModelOption]:
        from lightfall.ui.preferences import claude_settings as cs

        found = cs._MODELS_CACHE.get(cs.ClaudeSettingsProvider.get_base_url() or "")
        ids = found if found else [m for m in cs.MODEL_OPTIONS if m]
        return [ModelOption("", "Default (CLI)")] + [ModelOption(m, m) for m in ids]

    def current_model(self) -> str:
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

        return ClaudeSettingsProvider.get_model()

    def live_model_id(self, preset: str) -> str | None:
        from lightfall.ui.preferences.claude_settings import resolve_model_alias

        return resolve_model_alias(preset)

    def sign_in_argv(self) -> list[str] | None:
        return None

    def sign_in_env(self) -> dict[str, str]:
        return {}

    def tool_ref(self, server: str, tool: str) -> str:
        return f"`{tool_id(server, tool)}`"


# `agy models` result per executable for 60 s: {agy: (monotonic, (rows, detail))}.
_AGY_TTL_S = 60.0
_AGY_CACHE: dict[str, tuple[float, tuple[list[tuple[str, str]] | None, str]]] = {}


def clear_caches() -> None:
    _AGY_CACHE.clear()


class AgyInfo:
    kind = AGY
    display_name = "Gemini"
    caps = BackendCaps(cost=False, live_set_model=False, subagents=False)
    prompt_notes = ""
    model_pref_key = "agy_model"

    def __init__(self, agy_path: str | None = None) -> None:
        # None = use the agy_path preference (the settings page passes its field).
        self._agy_path = agy_path

    def _executable(self) -> str | None:
        from lightfall.agents.backends.agy import discover

        pref = self._agy_path
        if pref is None:
            from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

            pref = ClaudeSettingsProvider.get_agy_path()
        return discover.find_agy(pref or None)

    def _probe(self, agy: str, refresh: bool) -> tuple[list[tuple[str, str]] | None, str]:
        hit = _AGY_CACHE.get(agy)
        if not refresh and hit is not None and time.monotonic() - hit[0] < _AGY_TTL_S:
            return hit[1]
        from lightfall.agents.backends.agy import discover
        from lightfall.agents.backends.agy.home import spec_home

        result = discover.agy_models(agy, spec_home(None))
        _AGY_CACHE[agy] = (time.monotonic(), result)
        return result

    def auth_status(self, refresh: bool = False) -> AuthStatus:
        try:
            agy = self._executable()
            if agy is None:
                return AuthStatus(False, "agy (Antigravity CLI) not found")
            rows, detail = self._probe(agy, refresh)
        except Exception as exc:  # noqa: BLE001 - status is best-effort
            return AuthStatus(False, f"Couldn't check: {exc}")
        if rows:
            return AuthStatus(True, f"Signed in ({len(rows)} models)")
        return AuthStatus(False, f"Not signed in: {detail}")

    def list_models(self, refresh: bool = False) -> list[ModelOption]:
        agy = self._executable()
        if agy is None:
            raise FileNotFoundError("agy (Antigravity CLI) not found")
        rows, detail = self._probe(agy, refresh)
        if rows is None:
            raise RuntimeError(detail)
        return [ModelOption("", "Default (agy)")] + [ModelOption(i, lbl) for i, lbl in rows]

    def cached_models(self) -> list[ModelOption]:
        out = [ModelOption("", "Default (agy)")]
        try:
            agy = self._executable()
        except Exception:  # noqa: BLE001
            return out
        hit = _AGY_CACHE.get(agy) if agy else None
        if hit is not None and hit[1][0]:
            out += [ModelOption(i, lbl) for i, lbl in hit[1][0]]
        return out

    def current_model(self) -> str:
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

        return ClaudeSettingsProvider.get_agy_model()

    def live_model_id(self, preset: str) -> str | None:
        return preset or None

    def sign_in_argv(self) -> list[str] | None:
        agy = self._executable()
        return [agy] if agy else None

    def sign_in_env(self) -> dict[str, str]:
        from lightfall.agents.backends.agy.home import spec_home

        home = str(spec_home(None))
        return {"USERPROFILE": home, "HOME": home}

    def tool_ref(self, server: str, tool: str) -> str:
        # agy calls MCP tools as call_mcp_tool(ServerName, ToolName); plugin servers
        # are named lightfall_<key>.
        return f"`lightfall_{server}` / `{tool}`"


def backend_info(kind: str | None = None, *, agy_path: str | None = None):
    """Descriptor for ``kind`` (default: the ``agent_backend`` preference)."""
    if (kind or backend_kind()) == AGY:
        return AgyInfo(agy_path=agy_path)
    return ClaudeCodeInfo()
