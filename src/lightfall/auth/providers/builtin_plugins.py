"""Built-in auth providers exposed as AuthProviderPlugins.

Wraps the existing Keycloak / local / PAM providers so they register in the
AuthProviderRegistry like any contributed provider. Config construction mirrors
what the login dialog and main._setup_auth did previously.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from loguru import logger

from lightfall.auth.providers.base import AuthProvider
from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin


def _session_duration() -> timedelta:
    from lightfall.ui.preferences.login_settings import LoginSettingsProvider

    return LoginSettingsProvider.get_session_duration()


class KeycloakAuthPlugin(AuthProviderPlugin):
    @property
    def name(self) -> str:
        return "keycloak"

    @property
    def display_name(self) -> str:
        return "Keycloak"

    @property
    def accent_color(self) -> str:
        return "#0066cc"

    @property
    def requires_username(self) -> bool:
        return False  # browser-based

    @property
    def requires_password(self) -> bool:
        return False

    @property
    def priority(self) -> int:
        return 10

    def create_provider(self) -> AuthProvider:
        from lightfall.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig
        from lightfall.config import ConfigManager
        from lightfall.core import LFApplication

        app = LFApplication.get_instance()
        cfg = app.services.get(ConfigManager).model.auth.provider
        return KeycloakAuthProvider(
            KeycloakConfig(
                server_url=cfg.server_url,
                realm=cfg.realm,
                client_id=cfg.client_id,
                client_secret=cfg.client_secret or None,
                redirect_uri=cfg.redirect_uri,
            )
        )


class LocalAuthPlugin(AuthProviderPlugin):
    @property
    def name(self) -> str:
        return "local"

    @property
    def display_name(self) -> str:
        return "Local Account"

    @property
    def requires_username(self) -> bool:
        return True

    @property
    def requires_password(self) -> bool:
        return True

    @property
    def priority(self) -> int:
        return 30

    def create_provider(self) -> AuthProvider:
        from lightfall.auth.providers.local import LocalAuthProvider

        return LocalAuthProvider(session_duration=_session_duration())


class PamAuthPlugin(AuthProviderPlugin):
    @property
    def name(self) -> str:
        return "pam"

    @property
    def display_name(self) -> str:
        return "Linux User"

    @property
    def accent_color(self) -> str:
        return "#2e7d32"

    @property
    def requires_username(self) -> bool:
        return False  # uses OS identity

    @property
    def requires_password(self) -> bool:
        return False

    @property
    def priority(self) -> int:
        return 20

    def create_provider(self) -> AuthProvider:
        from lightfall.auth.policy import Role
        from lightfall.auth.providers.pam import PamAuthProvider, PamConfig
        from lightfall.config import ConfigManager
        from lightfall.core import LFApplication

        pam_config = PamConfig(session_duration=_session_duration())
        try:
            app = LFApplication.get_instance()
            cfg = app.services.get(ConfigManager).model.auth.provider
            group_role_map = {}
            for group_name, role_str in cfg.pam_group_role_map.items():
                try:
                    group_role_map[group_name] = Role(role_str)
                except ValueError:
                    logger.warning("Unknown role '{}' in pam_group_role_map", role_str)
            if group_role_map:
                pam_config.group_role_map = group_role_map
        except Exception:
            logger.debug("Using default PamConfig (app config unavailable)")
        return PamAuthProvider(pam_config)


def _disabled_plugin_ids() -> set[str]:
    """Read the user's disabled-plugins preference (unique_id strings).

    Mirrors PluginLoader._get_disabled_plugin_ids so built-in auth providers
    honor the same Plugins-settings toggles as manifest-loaded providers.
    Returns an empty set if preferences aren't available (e.g. early startup
    or tests).
    """
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        disabled = PreferencesManager.get_instance().get("disabled_plugins", [])
        if isinstance(disabled, list):
            return set(disabled)
    except Exception as e:
        logger.debug("Could not load disabled plugins preference: {}", e)
    return set()


def register_builtin_auth_plugins(
    registry: Any, *, config: Any = None, include_pam: bool = True
) -> None:
    """Register the built-in auth provider plugins into the registry.

    Honors the user's ``disabled_plugins`` preference so providers turned off
    in the Plugins settings page aren't offered at login. The ``local``
    provider is always registered as the ultimate fallback.
    """
    disabled = _disabled_plugin_ids()
    optional = [KeycloakAuthPlugin()]
    if include_pam:
        optional.append(PamAuthPlugin())
    for plugin in optional:
        if f"auth_provider:{plugin.name}" in disabled:
            logger.info(
                "Built-in auth provider '{}' disabled, not registering", plugin.name
            )
            continue
        registry.register(plugin)
    # Local is the hard fallback used when no other provider is configured.
    registry.register(LocalAuthPlugin())
