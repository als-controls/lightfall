"""Apply sign-on and Tiled configuration from the current config.

Startup calls ``configure_auth`` and ``configure_tiled`` (from main._setup_auth and
main._setup_tiled); the setup wizard calls ``reconfigure_services`` after loading a
facility bootstrap. IPC and the logbook URL are read when used or at the next launch.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from lightfall.utils.logging import logger

if TYPE_CHECKING:
    from lightfall.config.manager import ConfigManager


def configure_auth(config: ConfigManager, *, register: bool = True,
                   keep_session: bool = False) -> None:
    """Pick the startup auth provider from config.

    NCS_AUTH=local|pam|keycloak overrides the configured type for development.
    ``register=False`` skips plugin registration: the built-in plugins read config when
    they create a provider, so registering again is not needed. ``keep_session=True``
    leaves a signed-in session's provider alone; the next sign-in uses the new config.
    """
    from lightfall.auth.provider_registry import AuthProviderRegistry
    from lightfall.auth.providers.builtin_plugins import (
        register_builtin_auth_plugins,
        seed_default_disabled_plugins,
    )
    from lightfall.auth.session import SessionManager

    session_manager = SessionManager.get_instance()
    if keep_session and session_manager.is_authenticated:
        logger.info("Signed in: keeping the current auth provider until the next sign-in")
        return

    auth_config = config.model.auth
    provider_type = auth_config.provider.type

    # Allow environment variable override for development
    env_auth = os.environ.get("NCS_AUTH", "").lower()
    if env_auth in ("local", "pam", "keycloak"):
        provider_type = env_auth
        logger.info("Using {} auth (NCS_AUTH={})", provider_type, env_auth)

    registry = AuthProviderRegistry.get_instance()
    if register:
        # Apply ship-disabled-by-default plugin state once, before registration so
        # the disabled ones (e.g. local) are honored here and by the plugin loader.
        seed_default_disabled_plugins()
        register_builtin_auth_plugins(
            registry, config=config, include_pam=(sys.platform != "win32")
        )

    # Pick the startup default provider (used for token refresh before any
    # dialog login). The dialog will call set_provider again on actual login.
    default_name = provider_type if registry.has(provider_type) else "local"
    # Keycloak needs a configured server_url; otherwise fall back to local.
    if default_name == "keycloak" and not auth_config.provider.server_url:
        default_name = "local"
    default_plugin = registry.get(default_name) or registry.get("local")

    provider = None
    if default_plugin is not None:
        try:
            provider = default_plugin.create_provider()
            logger.info("Default auth provider: {}", default_plugin.name)
        except Exception as exc:
            logger.warning(
                "Failed to create '{}' auth provider ({}); falling back to local",
                default_plugin.name, exc,
            )
    if provider is None:
        # The chosen provider may be unregistered/disabled (e.g. local ships disabled
        # by default and nothing else is configured). Use a direct LocalAuthProvider
        # purely for pre-login session plumbing; it adds no login button.
        from lightfall.auth.providers.local import LocalAuthProvider
        from lightfall.ui.preferences.login_settings import LoginSettingsProvider

        provider = LocalAuthProvider(
            session_duration=LoginSettingsProvider.get_session_duration()
        )
        logger.info("Default auth provider: local (direct fallback)")

    session_manager.set_provider(provider)


def configure_tiled(config: ConfigManager) -> None:
    """Configure the TiledService singleton from preferences. Idempotent."""
    from lightfall.auth.session import SessionManager
    from lightfall.services.tiled_service import (
        TILED_DEFAULTS,
        TiledAuthMode,
        TiledConfig,
        TiledConnectionState,
        TiledService,
    )
    from lightfall.ui.preferences.manager import PreferencesManager

    prefs = PreferencesManager.get_instance()
    service = TiledService.get_instance()

    enabled = bool(prefs.get("tiled_enabled", TILED_DEFAULTS["tiled_enabled"]))
    url = prefs.get("tiled_url", TILED_DEFAULTS["tiled_url"]) or ""
    api_key = prefs.get("tiled_api_key") or None

    # tiled_auth_mode wins; otherwise Keycloak sign-on implies Keycloak for Tiled.
    auth_mode_pref = prefs.get("tiled_auth_mode", TILED_DEFAULTS["tiled_auth_mode"])
    explicit_mode = None
    if auth_mode_pref:
        try:
            explicit_mode = TiledAuthMode(auth_mode_pref)
        except ValueError:
            logger.warning("Ignoring invalid tiled_auth_mode {!r}; auto-detecting", auth_mode_pref)
    if explicit_mode is not None:
        auth_mode = explicit_mode
    elif config.model.auth.provider.type == "keycloak":
        auth_mode = TiledAuthMode.KEYCLOAK
        logger.info("Auto-detected Keycloak auth mode for Tiled")
    elif api_key:
        auth_mode = TiledAuthMode.API_KEY
    else:
        auth_mode = TiledAuthMode.NONE

    active = bool(enabled and url)
    wanted = TiledConfig(url=url, api_key=api_key, enabled=active, auth_mode=auth_mode)
    if service.config == wanted:
        # Unchanged, but a failed or dropped connection is retried (a wizard re-run after
        # moving networks); a live or in-flight one is left alone.
        failed = service.state in (TiledConnectionState.ERROR, TiledConnectionState.DISCONNECTED)
        signed_in = SessionManager.get_instance().is_authenticated
        if active and failed and (auth_mode != TiledAuthMode.KEYCLOAK or signed_in):
            logger.info("Tiled configuration unchanged; retrying the connection")
            service.connect_async()
        else:
            logger.debug("Tiled configuration unchanged")
        return

    service.configure(url=url, api_key=api_key, enabled=active, auth_mode=auth_mode)
    if not active:
        logger.debug("Tiled service initialized (disabled)")
        return
    if auth_mode == TiledAuthMode.KEYCLOAK:
        # Connect on login (and now, if already signed in).
        service.connect_session_manager()
        if SessionManager.get_instance().is_authenticated:
            service.connect_async()
        else:
            logger.info("Tiled service initialized with Keycloak auth, waiting for user login")
    else:
        service.connect_async()
        logger.info("Tiled service initialized, connecting in background")


# Preference keys configure_tiled reads. A change to any of them re-applies Tiled.
TILED_PREFERENCE_KEYS = ("tiled_enabled", "tiled_url", "tiled_auth_mode", "tiled_api_key")


def watch_tiled_preferences(config: ConfigManager) -> Callable[[], None]:
    """Re-run ``configure_tiled`` whenever a Tiled preference changes.

    Startup configures Tiled before login, when server scopes are not active yet, so
    values set at facility/beamline/branch/user scope read as defaults then. Those
    values arrive as per-key changes once ``activate_scopes`` resolves the chain
    (and again on refresh/deactivation). Changes are coalesced into one
    ``configure_tiled`` call per event-loop pass so a half-applied chain (new
    ``tiled_enabled``, old ``tiled_url``) never connects. Returns the flush callable.
    """
    from PySide6.QtCore import QTimer

    from lightfall.ui.preferences.manager import PreferencesManager

    pending = False

    def flush() -> None:
        nonlocal pending
        pending = False
        try:
            configure_tiled(config)
        except Exception:  # noqa: BLE001 - a bad pref must not break the slot chain
            logger.exception("Re-applying Tiled configuration failed")

    def on_change(_value: object) -> None:
        nonlocal pending
        if not pending:
            pending = True
            QTimer.singleShot(0, flush)

    prefs = PreferencesManager.get_instance()
    for key in TILED_PREFERENCE_KEYS:
        prefs.subscribe(key, on_change)  # plain closure: held strongly, lives with prefs
    return flush


def reconfigure_services(config: ConfigManager) -> list[str]:
    """Re-apply auth and Tiled configuration. Idempotent; logs and returns errors."""
    errors: list[str] = []
    steps: tuple[tuple[str, Callable[[], None]], ...] = (
        ("sign-on", lambda: configure_auth(config, register=False, keep_session=True)),
        ("Tiled", lambda: configure_tiled(config)),
    )
    for label, apply in steps:
        try:
            apply()
        except Exception as exc:  # noqa: BLE001 - reported to the wizard, never raised
            logger.exception("Reconfiguring {} failed", label)
            errors.append(f"{label}: {exc}")
    return errors
