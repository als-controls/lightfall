"""Static per-backend facts for the UI: name, capabilities, sign-in, models.

The settings page, model picker, cockpit, preamble and speaker strings ask
``backend_info()`` instead of branching on the backend kind. Nothing here needs
a live session. ``auth_status`` / ``list_models`` block (agy runs ``agy models``,
up to 30 s): call them off the GUI thread. ``cached_models`` never blocks.

This module must not import ``claude_agent_sdk``.
"""
from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass

from lightfall.agents.backends.select import AGY, CLAUDE_CODE, backend_kind
from lightfall.agents.tooling import tool_id


@dataclass(frozen=True)
class BackendCaps:
    cost: bool            # TurnResult carries real USD
    live_set_model: bool  # a model change applies without a respawn
    subagents: bool       # the backend runs SessionConfig.subagents
    live_set_effort: bool = False  # an effort change keeps the conversation (no rebuild)


@dataclass(frozen=True)
class AuthStatus:
    ok: bool
    detail: str


@dataclass(frozen=True)
class ModelOption:
    id: str     # "" = the backend's default
    label: str
    efforts: tuple[str, ...] = ()  # reasoning levels this model offers (agy), low..max


#: Reasoning levels, in order (agy ``--effort`` and Claude ``effort``).
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
_EFFORT_ID_RE = re.compile(r"^(.+)-(" + "|".join(EFFORT_LEVELS) + r")$")
_EFFORT_LABEL_RE = re.compile(r"\s*\((" + "|".join(EFFORT_LEVELS) + r")\)$", re.IGNORECASE)


def split_effort_variant(model_id: str) -> tuple[str, str]:
    """``"gemini-4-argon-medium"`` -> ``("gemini-4-argon", "medium")``; no suffix -> ``(id, "")``."""
    m = _EFFORT_ID_RE.match(model_id or "")
    return (m.group(1), m.group(2)) if m else (model_id or "", "")


def group_agy_models(rows: Iterable[tuple[str, str]]) -> list[ModelOption]:
    """Fold ``agy models`` rows (one per model *and* effort) into one option per base model.

    ``gemini-4-argon-high / Gemini 4 Argon (High)`` and its siblings become
    ``ModelOption("gemini-4-argon", "Gemini 4 Argon", ("low", "medium", "high"))``.
    An id without an effort suffix passes through with no levels. First-seen order.
    """
    out: dict[str, tuple[str, set[str]]] = {}
    for model_id, label in rows:
        base, level = split_effort_variant(model_id)
        if base not in out:
            out[base] = ((_EFFORT_LABEL_RE.sub("", label) if level else label) or base, set())
        if level:
            out[base][1].add(level)
    return [ModelOption(base, label, tuple(lv for lv in EFFORT_LEVELS if lv in levels))
            for base, (label, levels) in out.items()]


def effort_options_for(models: Iterable[ModelOption], model_id: str) -> list[str]:
    """``[""]`` plus the levels ``model_id`` offers in ``models`` (``[""]`` if unknown)."""
    for opt in models:
        if model_id and opt.id == model_id:
            return ["", *opt.efforts]
    return [""]


_CLAUDE_PROMPT_NOTES = """### Waiting

Do not use `ScheduleWakeup` to wait for the engine: it does not fire in Lightfall's
embedded session, so a scheduled wakeup silently drops and the conversation stalls
until the user nudges you. Use `lightfall_wait_for_idle` instead."""

# How the preamble tags and explains peers the backend can run as subagents
# (only used when ``caps.subagents``). The note continues a markdown bullet.
_CLAUDE_SUBAGENT_TAG = "Agent-tool delegable"
_CLAUDE_SUBAGENT_NOTE = (
    "Peers marked Agent-tool delegable also appear as agent types in your Agent tool for\n"
    "  background subagent runs (isolated, report-back; no session tab).")
_AGY_SUBAGENT_TAG = "subagent delegable"
_AGY_SUBAGENT_NOTE = (
    "Peers marked subagent delegable can be delegated to with your `invoke_subagent` tool\n"
    "  (TypeName = the peer's name) for background subagent runs (isolated, report-back;\n"
    "  no session tab).")


class ClaudeCodeInfo:
    kind = CLAUDE_CODE
    display_name = "Claude"
    # Effort is read at construction: a change rebuilds the agent (fresh session).
    caps = BackendCaps(cost=True, live_set_model=True, subagents=True, live_set_effort=False)
    prompt_notes = _CLAUDE_PROMPT_NOTES
    subagent_tag = _CLAUDE_SUBAGENT_TAG
    subagent_note = _CLAUDE_SUBAGENT_NOTE
    model_pref_key = "claude_model"
    effort_pref_key = "claude_effort"

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

    def effort_options(self, model_id: str) -> list[str]:
        """``""`` (SDK default) plus the levels; xhigh/max are Opus-only."""
        from lightfall.ui.preferences.claude_settings import EFFORT_OPTIONS

        if "opus" in (model_id or ""):
            return list(EFFORT_OPTIONS)
        return [lv for lv in EFFORT_OPTIONS if lv not in ("xhigh", "max")]

    def current_effort(self) -> str:
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

        return ClaudeSettingsProvider.get_effort()

    def live_model_id(self, preset: str) -> str | None:
        from lightfall.ui.preferences.claude_settings import resolve_model_alias

        return resolve_model_alias(preset)

    def sign_in_argv(self) -> list[str] | None:
        return None

    def sign_in_env(self) -> dict[str, str]:
        return {}

    def tool_ref(self, server: str, tool: str) -> str:
        return f"`{tool_id(server, tool)}`"

    def subagent_name_problem(self, name: str) -> str | None:
        return None  # SDK AgentDefinitions take any name


# `agy models` result per executable for 60 s: {agy: (monotonic, (rows, detail))}.
_AGY_TTL_S = 60.0
_AGY_CACHE: dict[str, tuple[float, tuple[list[tuple[str, str]] | None, str]]] = {}


def clear_caches() -> None:
    _AGY_CACHE.clear()


class AgyInfo:
    kind = AGY
    display_name = "Gemini"
    # Subagents: agent files in the session plugin, delegated with invoke_subagent.
    # Model and effort are spawn flags: a change respawns the child on the next
    # turn with --conversation, so the conversation is kept.
    caps = BackendCaps(cost=False, live_set_model=False, subagents=True, live_set_effort=True)
    prompt_notes = ""
    subagent_tag = _AGY_SUBAGENT_TAG
    subagent_note = _AGY_SUBAGENT_NOTE
    model_pref_key = "agy_model"
    effort_pref_key = "agy_effort"

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
        return [ModelOption("", "Default (agy)"), *group_agy_models(rows)]

    def cached_models(self) -> list[ModelOption]:
        out = [ModelOption("", "Default (agy)")]
        try:
            agy = self._executable()
        except Exception:  # noqa: BLE001
            return out
        hit = _AGY_CACHE.get(agy) if agy else None
        if hit is not None and hit[1][0]:
            out += group_agy_models(hit[1][0])
        return out

    def current_model(self) -> str:
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

        return ClaudeSettingsProvider.get_agy_model()

    def effort_options(self, model_id: str) -> list[str]:
        """The levels base model ``model_id`` offers (cache only; ``[""]`` if unknown)."""
        return effort_options_for(self.cached_models(), model_id)

    def current_effort(self) -> str:
        from lightfall.ui.preferences.claude_settings import ClaudeSettingsProvider

        return ClaudeSettingsProvider.get_agy_effort()

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

    def subagent_name_problem(self, name: str) -> str | None:
        """Why a peer named ``name`` cannot be a subagent here (None = it can)."""
        from lightfall.agents.backends.agy.home import subagent_name_problem

        return subagent_name_problem(name)


def _agy_rows(agy: str | list[str]):
    """``agy models`` rows (cached 60 s per executable; blocks on a miss).

    Raises when agy cannot list models. An argv list (a dev/test wrapper) is not cached.
    """
    if isinstance(agy, str):
        rows, detail = AgyInfo()._probe(agy, refresh=False)
    else:
        from lightfall.agents.backends.agy import discover
        from lightfall.agents.backends.agy.home import spec_home

        rows, detail = discover.agy_models(agy, spec_home(None))
    if rows is None:
        raise RuntimeError(detail)
    return rows


def agy_model_ids(agy: str | list[str]) -> frozenset[str]:
    """Model ids ``agy models`` lists (cached; raises when agy cannot list models)."""
    return frozenset(model_id for model_id, _ in _agy_rows(agy))


def agy_effort_levels(agy: str | list[str]) -> dict[str, tuple[str, ...]]:
    """``{base model id: effort levels}`` from ``agy models`` (cached like ``agy_model_ids``).

    Raises when agy cannot list models.
    """
    return {opt.id: opt.efforts for opt in group_agy_models(_agy_rows(agy))}


def backend_info(kind: str | None = None, *, agy_path: str | None = None):
    """Descriptor for ``kind`` (default: the ``agent_backend`` preference)."""
    if (kind or backend_kind()) == AGY:
        return AgyInfo(agy_path=agy_path)
    return ClaudeCodeInfo()
