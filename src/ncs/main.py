"""Main entry point for the NCS application."""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from ncs.auth.providers import LocalAuthProvider
from ncs.auth.session import SessionManager
from ncs.config import ConfigManager
from ncs.core import NCSApplication
from ncs.devices import DeviceCatalog
from ncs.devices.backends import MockBackend
from ncs.project import ProjectService, create_welcome_project
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

    # Project service
    project_service = ProjectService.get_instance()
    services.register_instance(ProjectService, project_service)

    # Device catalog with mock backend
    device_catalog = DeviceCatalog.get_instance()
    services.register_instance(DeviceCatalog, device_catalog)

    logger.debug("Application services registered")


def _setup_devices() -> None:
    """Setup the device catalog with mock backend.

    Initializes the DeviceCatalog with a MockBackend containing
    simulated ophyd.sim devices for development and testing.
    """
    catalog = DeviceCatalog.get_instance()

    # Use mock backend with simulated devices
    backend = MockBackend(include_noisy=True)
    catalog.set_backend(backend)

    if catalog.connect():
        device_count = len(catalog.get_all_devices())
        logger.info("Device catalog initialized with {} simulated devices", device_count)
    else:
        logger.error("Failed to connect device catalog")


def _setup_first_launch(project_service: ProjectService) -> None:
    """Setup the welcome project for first launch.

    If no project is currently open (first launch or no recent project),
    creates and opens the welcome project with introductory content.

    Args:
        project_service: The ProjectService instance.
    """
    # Check if we have a recent project to restore
    # For now, always create welcome project (persistence comes later)
    if not project_service.has_project:
        logger.info("First launch detected, creating welcome project")
        welcome = create_welcome_project()
        project_service.open_project(welcome)


def _setup_default_panels(window: NCSMainWindow) -> None:
    """Setup default panels for the main window.

    Opens panels that should be visible by default on startup.

    Args:
        window: The main window instance.
    """
    # Open the logbook panel on the right side
    panel = window.add_panel(
        "ncs.panels.logbook",
        area=Qt.DockWidgetArea.RightDockWidgetArea,
    )
    if panel:
        logger.info("Opened default logbook panel")
    else:
        logger.warning("Failed to open default logbook panel")


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

    # Setup device catalog with mock backend
    _setup_devices()

    # Setup first launch (welcome project)
    project_service = ProjectService.get_instance()
    _setup_first_launch(project_service)

    # Create and set main window
    window = NCSMainWindow()
    window.set_config_manager(config)
    app.set_main_window(window)

    # Setup default panels (logbook)
    _setup_default_panels(window)

    # Run the application
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
