"""Settings -> Assistant: the ClaudeSettingsPlugin form page and ClaudeSettingsProvider.

ClaudeSettingsProvider reads the assistant preferences (with their defaults) for
the rest of the app; ClaudeSettingsPlugin is the scope-aware Preferences page.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from lightfall_settings import SettingSpec

from lightfall.plugins.settings_plugin import SettingsPlugin
from lightfall.ui.preferences.form.schema import (
    Action,
    Choice,
    Custom,
    FormValues,
    Info,
    Integer,
    Secret,
    Section,
    SettingField,
    Text,
    Toggle,
    Url,
)
from lightfall.ui.preferences.manager import PreferencesManager
from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon


def check_oauth_status() -> tuple[bool, str]:
    """Check if Claude Code CLI has OAuth credentials.

    Returns:
        A tuple of (is_authenticated, status_message).
    """
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    if not credentials_path.exists():
        return False, "Not logged in"

    try:
        with open(credentials_path) as f:
            creds = json.load(f)
        if "claudeAiOauth" in creds:
            return True, "Logged in via OAuth (subscription)"
        return False, "No OAuth credentials"
    except (OSError, json.JSONDecodeError):
        return False, "Could not read credentials"


# Preset API endpoints
API_ENDPOINTS = {
    "anthropic": ("Anthropic API", "https://api.anthropic.com"),
    "cborg": ("LBNL Cloud (cborg)", "https://api.cborg.lbl.gov"),
    "custom": ("Custom...", ""),
}

# Model combo presets. "" = let the CLI pick its default model.
MODEL_OPTIONS = [
    "",  # Default (CLI default)
    "claude-opus",
    "claude-sonnet",
    "claude-haiku",
]

# Combo preset -> model string the Claude Code CLI accepts. Anything not in
# this map (custom entry, full id) passes through unchanged.
_MODEL_ALIASES = {
    "claude-opus": "opus",
    "claude-sonnet": "sonnet",
    "claude-haiku": "haiku",
}

# Reasoning effort. "" = leave the SDK default (high). xhigh/max are Opus-only.
EFFORT_OPTIONS = ["", "low", "medium", "high", "xhigh", "max"]


def resolve_model_alias(name: str) -> str:
    """Resolve a model combo preset to a CLI model string (passthrough else)."""
    if not name:
        return ""
    return _MODEL_ALIASES.get(name, name)


# Session cache of discovered models, keyed by base_url. Only successful
# lookups are cached (a failed probe should retry next time).
_MODELS_CACHE: dict[str, list[str]] = {}


def fetch_available_models(
    base_url: str | None,
    api_key: str | None,
    *,
    timeout: float = 5.0,
) -> list[str] | None:
    """Query a backend's ``GET /v1/models`` and return the model ids.

    Backend-agnostic: Anthropic and OpenAI-compatible gateways (e.g. cborg)
    both return ``{"data": [{"id": ...}, ...]}``. Tries Anthropic auth
    (``x-api-key`` + ``anthropic-version``), retrying once with
    ``Authorization: Bearer`` on 401. Routes ``*.lbl.gov`` (cborg) through the
    configured SOCKS proxy. Never raises — returns ``None`` on any failure so
    callers fall back to the free-text picker.
    """
    if not base_url or not api_key:
        return None

    import httpx

    url = f"{base_url.rstrip('/')}/v1/models"
    client_kwargs: dict = {"timeout": timeout}
    try:
        from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider
        proxy_url = ProxySettingsProvider.should_use_proxy_for_url(base_url)
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
    except Exception:  # noqa: BLE001 - proxy is best-effort
        pass

    anthropic_headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    bearer_headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(url, headers=anthropic_headers)
            if resp.status_code == 401:
                resp = client.get(url, headers=bearer_headers)
            if resp.status_code != 200:
                return None
            data = resp.json().get("data")
            if not isinstance(data, list):
                return None
            ids = [
                m["id"] for m in data
                if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]
            ]
            return ids or None
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        logger.debug("fetch_available_models failed for {}: {}", base_url, exc)
        return None


def get_cached_models(
    base_url: str | None,
    api_key: str | None,
    *,
    refresh: bool = False,
) -> list[str] | None:
    """Session-cached ``fetch_available_models``. Only successes are cached."""
    if not base_url:
        return None
    if not refresh and base_url in _MODELS_CACHE:
        return _MODELS_CACHE[base_url]
    models = fetch_available_models(base_url, api_key)
    if models:
        _MODELS_CACHE[base_url] = models
    return models


def cached_model_ids(base_url: str | None) -> list[str] | None:
    """Model ids already discovered for ``base_url`` (never fetches; None if unknown)."""
    return _MODELS_CACHE.get(base_url) if base_url else None


# Permission mode options
PERMISSION_MODES = {
    "default": "Requires confirmation for actions",
    "acceptEdits": "Auto-accepts edits",
    "bypassPermissions": "No confirmations (automation)",
}


AGENT_BACKENDS = ("claude-code", "agy")


class ClaudeSettingsProvider:
    """Provides Claude configuration from settings/env vars.

    This helper class provides a simple interface for ClaudePanel and
    other components to retrieve Claude configuration, with proper
    fallback to environment variables.
    """

    @staticmethod
    def get_api_key() -> str | None:
        """Get API key from preferences, falling back to env vars.

        Returns:
            The API key string, or None if not configured.
        """
        prefs = PreferencesManager.get_instance()
        key = prefs.get("claude_api_key", "")
        if key:
            return key
        return os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")

    @staticmethod
    def get_base_url() -> str | None:
        """Get base URL from preferences, falling back to env vars.

        Returns:
            The base URL string, or None if using default.
        """
        prefs = PreferencesManager.get_instance()
        endpoint = prefs.get("claude_endpoint", "anthropic")
        if endpoint == "anthropic":
            return "https://api.anthropic.com"
        elif endpoint == "cborg":
            return "https://api.cborg.lbl.gov"
        else:  # custom
            url = prefs.get("claude_custom_url", "")
            if url:
                return url
        return os.getenv("ANTHROPIC_BASE_URL") or os.getenv("ANTHROPIC_API_URL")

    @staticmethod
    def get_model() -> str:
        """Get the configured model name.

        Default is "" meaning "do not force a model" — lightfall omits the
        ``--model`` option and lets the configured backend/CLI use its own
        default. This is the only backend-agnostic choice: the Anthropic API,
        the LBL cborg gateway, and an Azure-hosted backend each accept
        different model identifiers, so forcing a hardcoded model string
        breaks every backend except the one it was written for.

        Returns:
            The model name string, or "" to use the backend default.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_model", "")

    @staticmethod
    def get_agent_backend() -> str:
        """Agent backend: ``"claude-code"`` (default) or ``"agy"``."""
        prefs = PreferencesManager.get_instance()
        value = prefs.get("agent_backend", "claude-code")
        if value in AGENT_BACKENDS:
            return value
        logger.warning("unknown agent_backend {!r}; using claude-code", value)
        return "claude-code"

    @staticmethod
    def get_agy_path() -> str:
        """Explicit agy executable path ('' = PATH / default install)."""
        prefs = PreferencesManager.get_instance()
        return prefs.get("agy_path", "") or ""

    @staticmethod
    def get_agy_project() -> str:
        """GCP project agy bills Gemini Enterprise usage to."""
        prefs = PreferencesManager.get_instance()
        default = "lbl-gemini-enterprise-access"
        return prefs.get("agy_gcp_project", default) or default

    @staticmethod
    def get_agy_location() -> str:
        """GCP location for agy."""
        prefs = PreferencesManager.get_instance()
        return prefs.get("agy_gcp_location", "global") or "global"

    @staticmethod
    def get_agy_model() -> str:
        """agy base model id ('' = agy's default).

        Migration: agy lists one id per model *and* effort. A saved variant id
        (``gemini-4-argon-medium``, from before effort was its own setting) is
        split here, on read, into ``agy_model=gemini-4-argon`` and
        ``agy_effort=medium``. A base id has no suffix, so this runs once.
        """
        from lightfall.agents.backends.info import split_effort_variant

        prefs = PreferencesManager.get_instance()
        value = prefs.get("agy_model", "") or ""
        base, level = split_effort_variant(value)
        if level:
            # Deliberate: a variant typed into the editable model combo wins over
            # whatever the Effort combo held; the user named that level explicitly.
            logger.info("agy_model {!r} is an effort variant; saving model {!r}, effort {!r}",
                        value, base, level)
            prefs.set("agy_effort", level)
            prefs.set("agy_model", base)
            return base
        return value

    @staticmethod
    def get_agy_effort() -> str:
        """agy reasoning effort ('' = the model's default)."""
        prefs = PreferencesManager.get_instance()
        return prefs.get("agy_effort", "") or ""

    @staticmethod
    def _find_agy() -> str | None:
        from lightfall.agents.backends.agy import discover

        return discover.find_agy(ClaudeSettingsProvider.get_agy_path())

    @staticmethod
    def get_max_turns() -> int:
        """Get the maximum number of conversation turns.

        Returns:
            The max turns value.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_max_turns", 20)

    @staticmethod
    def get_permission_mode() -> str:
        """Get the permission mode for tool execution.

        Returns:
            The permission mode string.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_permission_mode", "default")

    @staticmethod
    def get_effort() -> str:
        """Get the configured reasoning effort ('' = SDK default)."""
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_effort", "")

    @staticmethod
    def get_auto_restore() -> bool:
        """Whether to auto-restore the last session on launch."""
        prefs = PreferencesManager.get_instance()
        return bool(prefs.get("claude_auto_restore", False))

    @staticmethod
    def _last_session_key(kind: str | None = None) -> str:
        # Per backend: a Claude session id means nothing to agy (and vice versa).
        # ``kind`` pins the backend (an agent built for one backend must keep
        # writing its own key after the pref is switched); None = the live pref.
        if kind is None:
            kind = ClaudeSettingsProvider.get_agent_backend()
        if kind == "agy":
            return "agy_last_session_id"
        return "claude_last_session_id"

    @staticmethod
    def get_last_session_id(kind: str | None = None) -> str:
        """Get the persisted last-session id ('' if none).

        ``kind`` selects the backend; None means the active (pref) backend.
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get(ClaudeSettingsProvider._last_session_key(kind), "") or ""

    @staticmethod
    def set_last_session_id(session_id: str, kind: str | None = None) -> None:
        """Persist a session id for auto-restore.

        ``kind`` selects the backend; None means the active (pref) backend.
        """
        prefs = PreferencesManager.get_instance()
        prefs.set(ClaudeSettingsProvider._last_session_key(kind), session_id or "")

    @staticmethod
    def is_oauth_authenticated() -> bool:
        """Check if Claude Code CLI has OAuth authentication.

        Returns:
            True if OAuth credentials are available.
        """
        authenticated, _ = check_oauth_status()
        return authenticated

    @staticmethod
    def get_auth_status() -> tuple[bool, str]:
        """Get detailed authentication status of the active backend.

        Blocks for up to 30 s on agy (``agy models``); not for the GUI thread.

        Returns:
            Tuple of (is_authenticated, status_message).
        """
        from lightfall.agents.backends.info import backend_info

        status = backend_info().auth_status()
        return status.ok, status.detail

    @staticmethod
    def is_configured() -> bool:
        """Check if the active backend can start.

        Returns:
            True if API key or OAuth credentials are available (Claude), or
            the agy executable is found (agy backend).
        """
        if ClaudeSettingsProvider.get_agent_backend() == "agy":
            return ClaudeSettingsProvider._find_agy() is not None
        api_key = ClaudeSettingsProvider.get_api_key()
        if api_key:
            return True
        return ClaudeSettingsProvider.is_oauth_authenticated()

    @staticmethod
    def is_using_proxy() -> bool:
        """Check if a proxy endpoint is configured.

        Returns:
            True if using cborg or custom endpoint (not direct Anthropic API).
        """
        prefs = PreferencesManager.get_instance()
        endpoint = prefs.get("claude_endpoint", "anthropic")
        return endpoint != "anthropic"

    @staticmethod
    def get_disable_betas() -> bool:
        """Check if beta headers should be disabled.

        Returns:
            True if beta headers should be disabled (for proxy compatibility).
        """
        prefs = PreferencesManager.get_instance()
        return prefs.get("claude_disable_betas", False)


# --- Settings -> Assistant (scope-aware form) -----------------------------------

_SITE_SCOPES = frozenset({"facility", "beamline", "branch", "user"})
_USER_SCOPES = frozenset({"user"})

# Bounds of the agy sign-in poll (worker thread): check every 5 s for up to 5 min.
AGY_SIGN_IN_TIMEOUT_S = 300.0
AGY_SIGN_IN_POLL_S = 5.0

_CLAUDE_NOT_FOUND = "Claude CLI not found. Install with: npm i -g @anthropic-ai/claude-code"
_AGY_NOT_FOUND = "agy (Antigravity CLI) not found — install it or set its path"
_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def test_connection(base_url: str, api_key: str | None, model: str) -> str:
    """Send a one-token request to ``base_url``'s messages endpoint; return the status text.

    Blocking (up to 10 s): call it off the GUI thread. The API key never appears
    in the returned text or in a log line.
    """
    if not api_key:
        return "No API key configured"
    import httpx

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    data = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "Hi"}]}
    client_kwargs: dict = {"timeout": 10.0}
    try:
        from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

        proxy_url = ProxySettingsProvider.should_use_proxy_for_url(base_url)
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
    except Exception:  # noqa: BLE001 - proxy is best-effort
        pass
    try:
        with httpx.Client(**client_kwargs) as client:
            response = client.post(f"{base_url}/v1/messages", json=data, headers=headers)
    except httpx.TimeoutException:
        return "Connection timeout"
    except httpx.ConnectError:
        return "Could not connect to server"
    except Exception as exc:  # noqa: BLE001 - shown to the user
        text = _redact(str(exc), api_key)
        logger.error("Test connection error: {}", text)
        return f"Error: {text}"
    if response.status_code == 200:
        return "Connected!"
    if response.status_code == 401:
        return "Invalid API key"
    if response.status_code == 403:
        return "Access denied"
    return f"Error: HTTP {response.status_code}"


test_connection.__test__ = False  # not a pytest test, despite the name


def _is_agy(values: FormValues) -> bool:
    return values.effective("agent_backend", "claude-code") == "agy"


def _is_claude(values: FormValues) -> bool:
    return not _is_agy(values)


def _is_custom_endpoint(values: FormValues) -> bool:
    return values.effective("claude_endpoint", "anthropic") == "custom"


def _agy_path(values: FormValues) -> str | None:
    return (values.effective("agy_path", "") or "").strip() or None


def _effective_api_key(values: FormValues) -> str | None:
    key = (values.effective("claude_api_key", "") or "").strip()
    if key:
        return key
    return os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")


def _effective_base_url(values: FormValues) -> str:
    endpoint = values.effective("claude_endpoint", "anthropic")
    if endpoint in ("anthropic", "cborg"):
        return API_ENDPOINTS[endpoint][1]
    if endpoint == "custom":
        url = (values.effective("claude_custom_url", "") or "").strip()
        if url:
            return url.rstrip("/")
    return API_ENDPOINTS["anthropic"][1]


def _check_status(values: FormValues) -> str:
    """Auth status of the chosen backend (worker thread; agy's check can take 30 s).

    Served from the backend's 60 s auth cache (``refresh=False``): the action
    API cannot tell a click from a run_on_show / refresh_on run, and the
    automatic runs must not re-run agy every time the page opens. Sign-in
    polls with ``refresh=True``, which refreshes that cache.
    """
    from lightfall.agents.backends import info

    kind = values.effective("agent_backend", "claude-code")
    try:
        status = info.backend_info(kind, agy_path=_agy_path(values)).auth_status(refresh=False)
    except Exception as exc:  # noqa: BLE001 - shown as the action's error
        raise RuntimeError(f"Couldn't check: {exc}") from exc
    return ("✓ " if status.ok else "") + status.detail


def _oauth_text(values: FormValues) -> str:
    _ok, message = check_oauth_status()
    return f"OAuth: {message}"


def _auth_source_text(values: FormValues) -> str:
    """Which credential the assistant would use (never the credential itself)."""
    if (values.effective("claude_api_key", "") or "").strip():
        return "Using API key from settings"
    for name in _ENV_KEYS:
        if os.getenv(name):
            return f"Using env var {name}"
    authenticated, _ = check_oauth_status()
    if authenticated:
        return "Using OAuth (no API key needed)"
    return "No authentication configured"


def _claude_login(values: FormValues) -> str:
    """Start ``claude login`` (it opens the browser); worker thread."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["claude", "login"], shell=True)
        else:
            subprocess.Popen(["claude", "login"])
    except FileNotFoundError:
        return _CLAUDE_NOT_FOUND
    except Exception as exc:  # noqa: BLE001 - shown as the action's error
        logger.error("OAuth login error: {}", exc)
        raise RuntimeError(f"Error: {exc}") from exc
    return "Browser opened — complete login there"


def _test_connection_action(values: FormValues) -> str:
    model = values.effective("claude_model", "") or "claude-sonnet"
    return test_connection(_effective_base_url(values), _effective_api_key(values), model)


def _wait(seconds: float) -> bool:
    """Sleep ``seconds`` in short steps; False if the worker thread was asked to stop."""
    from qtpy.QtCore import QThread

    end = time.monotonic() + max(0.0, seconds)
    while True:
        thread = QThread.currentThread()
        if thread is not None and thread.isInterruptionRequested():
            return False
        left = end - time.monotonic()
        if left <= 0:
            return True
        time.sleep(min(0.1, left))


def _agy_sign_in(values: FormValues) -> str:
    """Open agy's Google sign-in in a console, then poll until signed in (worker thread).

    Polls every ``AGY_SIGN_IN_POLL_S`` for up to ``AGY_SIGN_IN_TIMEOUT_S``; never touches widgets.
    """
    from lightfall.agents.backends import info as backend_info_mod

    info = backend_info_mod.backend_info("agy", agy_path=_agy_path(values))
    argv = info.sign_in_argv()
    if argv is None:
        return _AGY_NOT_FOUND
    kwargs: dict = {"env": {**os.environ, **info.sign_in_env()}}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    try:
        subprocess.Popen(argv, **kwargs)
    except Exception as exc:  # noqa: BLE001 - shown as the action's error
        raise RuntimeError(f"Couldn't start agy: {exc}") from exc
    deadline = time.monotonic() + AGY_SIGN_IN_TIMEOUT_S
    while _wait(min(AGY_SIGN_IN_POLL_S, max(0.0, deadline - time.monotonic()))):
        status = info.auth_status(refresh=True)
        if status.ok:
            return "✓ " + status.detail
        if time.monotonic() >= deadline:
            break
    return "Not signed in — try again"


def _claude_model_editor(field: SettingField):
    # assistant_editors imports this module: import it when the editor is built.
    from lightfall.ui.preferences.assistant_editors import claude_model_editor

    return claude_model_editor(field)


def _agy_model_editor(field: SettingField):
    from lightfall.ui.preferences.assistant_editors import agy_model_editor

    return agy_model_editor(field)


_PERMISSION_DESCRIPTION = (
    "How the assistant handles tool execution. "
    "default asks for confirmation before actions. "
    "acceptEdits auto-accepts file edits. "
    "bypassPermissions asks for no confirmations (use for automation)."
)

_SPECS: tuple[tuple[str, type, frozenset[str], str], ...] = (
    ("agent_backend", str, _SITE_SCOPES, "Assistant backend"),
    ("claude_endpoint", str, _SITE_SCOPES, "API endpoint"),
    ("claude_custom_url", str, _SITE_SCOPES, "Custom URL"),
    ("claude_disable_betas", bool, _SITE_SCOPES, "Disable beta features"),
    ("agy_gcp_project", str, _SITE_SCOPES, "GCP project"),
    ("agy_gcp_location", str, _SITE_SCOPES, "GCP location"),
    ("claude_model", str, _USER_SCOPES, "Claude model"),
    ("claude_effort", str, _USER_SCOPES, "Effort"),
    ("claude_max_turns", int, _USER_SCOPES, "Max turns"),
    ("claude_permission_mode", str, _USER_SCOPES, "Permission mode"),
    ("claude_auto_restore", bool, _USER_SCOPES, "Restore last session on launch"),
    ("agy_model", str, _USER_SCOPES, "agy model"),
    ("agy_effort", str, _USER_SCOPES, "agy effort"),
)


class ClaudeSettingsPlugin(SettingsPlugin):
    """Settings -> Assistant: backend, Claude endpoint and sign-in, Gemini (agy), model, behavior.

    A form page: the site keys (backend, endpoint, agy project) may be set from the
    facility down to the user, the personal ones (model, effort, limits, behavior)
    per user only, and the API key and agy path at This console only.
    """

    @property
    def name(self) -> str:
        """Return unique identifier for this settings plugin."""
        return "claude"

    @property
    def display_name(self) -> str:
        """Return human-readable name for preferences sidebar."""
        return "Assistant"

    @property
    def icon(self) -> QIcon | None:
        """Return optional icon for sidebar."""
        return None

    @property
    def category(self) -> str:
        """Return category for grouping."""
        return "plugins"

    @property
    def priority(self) -> int:
        """Return sort order (lower = higher in list)."""
        return 60

    def specs(self) -> Sequence[SettingSpec]:
        # claude_api_key (secret) and agy_path (LOCAL_ONLY_KEYS) are console-only: no spec.
        return [SettingSpec(key=key, type=tp, scopes=scopes, label=label) for key, tp, scopes, label in _SPECS]

    def sections(self) -> Sequence[Section]:
        from lightfall.ui.preferences.agent_backend_section import BACKEND_KINDS

        return [
            Section("Backend", [
                SettingField("agent_backend", Choice(list(BACKEND_KINDS)), label="Assistant backend"),
                Action("Check status", run=_check_status, run_on_show=True, refresh_on=("agent_backend",)),
            ]),
            Section("Claude endpoint", [
                SettingField(
                    "claude_endpoint", Choice([(k, label) for k, (label, _url) in API_ENDPOINTS.items()]),
                    label="API endpoint", visible_when=_is_claude,
                ),
                SettingField(
                    "claude_custom_url", Url(blank_is_unset=True), label="Custom URL", placeholder="https://your-api-server.com",
                    visible_when=_is_claude, enabled_when=_is_custom_endpoint,
                ),
                SettingField(
                    "claude_disable_betas", Toggle(), label="Disable beta features",
                    description="For proxy servers that reject beta headers "
                                "(recommended for cborg and custom endpoints).",
                    visible_when=_is_claude,
                ),
            ]),
            Section("Claude sign-in", [
                Info(_oauth_text, visible_when=_is_claude),
                Action("Log in with browser", run=_claude_login, visible_when=_is_claude),
                SettingField(
                    "claude_api_key", Secret(), label="API key",
                    placeholder="sk-ant-... (optional if using OAuth)", visible_when=_is_claude,
                ),
                Info(_auth_source_text, visible_when=_is_claude),
                Action("Test connection", run=_test_connection_action, visible_when=_is_claude),
            ]),
            Section("Gemini (agy)", [
                SettingField(
                    "agy_path", Text(blank_is_unset=True), label="agy executable",
                    placeholder="auto (PATH or %LOCALAPPDATA%\\agy\\bin)", visible_when=_is_agy,
                ),
                SettingField(
                    "agy_gcp_project", Text(blank_is_unset=True), label="GCP project",
                    placeholder="lbl-gemini-enterprise-access", visible_when=_is_agy,
                ),
                SettingField(
                    "agy_gcp_location", Text(blank_is_unset=True), label="GCP location", placeholder="global", visible_when=_is_agy,
                ),
                SettingField(
                    "agy_model", Custom(_agy_model_editor), label="Model", companions=("agy_effort",),
                    visible_when=_is_agy,
                ),
                Action("Sign in…", run=_agy_sign_in, visible_when=_is_agy),
            ]),
            Section("Model and limits", [
                SettingField(
                    "claude_model", Custom(_claude_model_editor), label="Claude model", visible_when=_is_claude,
                ),
                SettingField(
                    "claude_effort", Choice([(lv, lv) for lv in EFFORT_OPTIONS if lv]), label="Effort",
                    description="Reasoning effort. xhigh and max are Opus-only and fall back to high "
                                "on other models. Changing it restarts the conversation.",
                    visible_when=_is_claude,
                ),
                SettingField(
                    "claude_max_turns", Integer(1, 10000), label="Max turns", placeholder="20",
                    description="Maximum number of conversation turns before stopping.",
                ),
            ]),
            Section("Behavior", [
                SettingField(
                    "claude_permission_mode",
                    Choice([(k, f"{k} - {d}") for k, d in PERMISSION_MODES.items()]),
                    label="Permission mode", description=_PERMISSION_DESCRIPTION,
                ),
                SettingField(
                    "claude_auto_restore", Toggle(), label="Restore last session on launch",
                    description="Resume the most recent conversation when the panel opens.",
                ),
            ]),
        ]

    def validate_values(self, values: FormValues) -> list[str]:
        if _is_custom_endpoint(values) and not (values.effective("claude_custom_url", "") or "").strip():
            return ["Custom URL is required when using custom endpoint"]
        return []
