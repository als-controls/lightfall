"""Main entry point for the NCS application."""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import TYPE_CHECKING

from ncs.auth.providers import LocalAuthProvider
from ncs.auth.session import SessionManager
from ncs.config import ConfigManager
from ncs.core import NCSApplication
from ncs.ui import NCSMainWindow
from ncs.ui.panels.registry import PanelRegistry
from ncs.ui.preferences import PreferencesManager
from ncs.ui.theme import ThemeManager
from ncs.utils.logging import logger

if TYPE_CHECKING:
    pass


def _setup_auth(config: ConfigManager) -> None:
    """Setup authentication provider based on configuration."""
    session_manager = SessionManager.get_instance()

    auth_config = config.model.auth
    provider_type = auth_config.provider.type

    if provider_type == "keycloak" and auth_config.provider.server_url:
        # Use Keycloak if configured
        try:
            from ncs.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig

            kc_config = KeycloakConfig(
                server_url=auth_config.provider.server_url,
                realm=auth_config.provider.realm,
                client_id=auth_config.provider.client_id,
                client_secret=auth_config.provider.client_secret or None,
                redirect_uri=auth_config.provider.redirect_uri,
            )
            provider = KeycloakAuthProvider(kc_config)
            logger.info("Using Keycloak authentication provider")
        except ImportError:
            logger.warning("aiohttp not available, falling back to local auth")
            provider = LocalAuthProvider(
                session_duration=timedelta(minutes=auth_config.session_timeout_minutes)
            )
    else:
        # Use local auth provider for development
        provider = LocalAuthProvider(
            session_duration=timedelta(minutes=auth_config.session_timeout_minutes)
        )
        logger.info("Using local development authentication provider")

    session_manager.set_provider(provider)


def _setup_services(app: NCSApplication, config: ConfigManager) -> None:
    """Setup application services."""
    # Register additional services
    services = app.services

    # Theme manager
    services.register(ThemeManager, ThemeManager.get_instance)

    # Session manager
    services.register(SessionManager, SessionManager.get_instance)

    # Preferences manager
    prefs = PreferencesManager.get_instance()
    prefs.set_config_manager(config)
    services.register_instance(PreferencesManager, prefs)

    # Panel registry
    registry = PanelRegistry.get_instance()
    registry.discover_plugins()
    services.register_instance(PanelRegistry, registry)

    logger.debug("Application services registered")


def main() -> int:
    """Run the NCS application."""
    # Get/create the application singleton
    app = NCSApplication.get_instance()

    # Initialize with default settings
    app.initialize(log_level="DEBUG")

    # Get config manager
    config: ConfigManager = app.services.get(ConfigManager)

    # Setup services
    _setup_services(app, config)

    # Setup authentication
    _setup_auth(config)

    # Create and set main window
    window = NCSMainWindow()
    window.set_config_manager(config)
    app.set_main_window(window)

    # Run the application
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
