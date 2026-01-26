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
from ncs.acquire import get_engine
from ncs.acquire.plans import get_registry as get_plan_registry
from ncs.devices import DeviceCatalog
from ncs.devices.backends import BCSBackend, MockBackend
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
    """Setup the device catalog based on user preferences.

    Initializes the DeviceCatalog with the backend configured in
    preferences (Mock or BCS). Falls back to Mock if not configured.
    """
    catalog = DeviceCatalog.get_instance()
    prefs = PreferencesManager.get_instance()

    # Get backend configuration from preferences
    backend_type = prefs.get("device_backend", "mock")

    if backend_type == "bcs":
        # BCS backend - connect to BCS server via ZMQ
        host = prefs.get("device_bcs_host", "localhost")
        port = prefs.get("device_bcs_port", 5577)
        timeout_ms = prefs.get("device_bcs_timeout_ms", 5000)
        beamline = prefs.get("device_bcs_beamline") or None

        backend = BCSBackend(
            host=host,
            port=port,
            timeout_ms=timeout_ms,
            beamline=beamline,
        )
        logger.info("Using BCS backend ({}:{})", host, port)
    else:
        # Mock backend (default)
        include_noisy = prefs.get("device_mock_include_noisy", True)
        backend = MockBackend(include_noisy=include_noisy)
        logger.info("Using Mock backend (include_noisy={})", include_noisy)

    catalog.set_backend(backend)

    if catalog.connect():
        device_count = len(catalog.get_all_devices())
        logger.info("Device catalog initialized with {} devices", device_count)
    else:
        logger.error("Failed to connect device catalog")


def _setup_bluesky(app: NCSApplication) -> None:
    """Setup Bluesky engine and plan registry.

    Initializes the BlueskyEngine singleton and plan registry
    for use by the BlueskyPanel.
    """
    services = app.services

    # Get/create engine singleton
    engine = get_engine()
    services.register_instance(type(engine), engine)

    # Get/create plan registry with standard bluesky plans
    plan_registry = get_plan_registry()
    services.register_instance(type(plan_registry), plan_registry)

    logger.info(
        "Bluesky initialized: engine state={}, {} plans available",
        engine.state_name,
        len(plan_registry),
    )


def _setup_plugins(app: NCSApplication) -> None:
    """Setup the plugin system and load preload plugins.

    This must be called BEFORE creating the main window to ensure
    preload plugins (like appearance/theme) can apply settings before
    any UI is shown.

    Args:
        app: The NCS application instance.
    """
    from ncs.plugins import PluginLoader, PluginRegistry, MCPToolPlugin
    from ncs.plugins.builtin_manifest import builtin_manifest
    from ncs.plugins.engine_plugin import EnginePlugin
    from ncs.plugins.settings_plugin import SettingsPlugin

    services = app.services

    # Create registry and loader
    registry = PluginRegistry()
    loader = PluginLoader(registry)

    # Register plugin types
    loader.register_plugin_type("settings", SettingsPlugin)
    loader.register_plugin_type("engine", EnginePlugin)
    loader.register_plugin_type("mcp_tool", MCPToolPlugin)

    # Load built-in manifest first
    loader.load_manifest(builtin_manifest)

    # Discover external manifests from entry points
    loader.discover_manifests()

    # Load preload plugins synchronously BEFORE creating main window
    # This ensures theme is applied before any UI appears
    preload_ok, preload_failed = loader.load_preload_plugins()
    if preload_ok > 0 or preload_failed > 0:
        logger.info(
            "Preload plugins: {} successful, {} failed",
            preload_ok,
            preload_failed,
        )

    # Register services
    services.register_instance(PluginRegistry, registry)
    services.register_instance(PluginLoader, loader)

    # Start background loading for remaining plugins
    loader.start_loading()

    logger.debug("Plugin system initialized")


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
    Layout: Claude, Bluesky, and Devices tabbed on left, Logbook on right.

    Args:
        window: The main window instance.
    """
    # Clear any saved window state to ensure our layout is applied
    from PySide6.QtCore import QSettings
    settings = QSettings("ALS", "NCS")
    settings.remove("mainwindow/geometry")
    settings.remove("mainwindow/state")

    # Open Claude panel on the left first (will be a tab)
    claude_dock = None
    claude_panel = window.add_panel(
        "ncs.panels.claude",
        area=Qt.DockWidgetArea.LeftDockWidgetArea,
    )
    if claude_panel:
        claude_dock = window._panel_docks.get("ncs.panels.claude")

    # Open Bluesky panel on the left
    window.add_panel(
        "ncs.panels.bluesky",
        area=Qt.DockWidgetArea.LeftDockWidgetArea,
    )
    bluesky_dock = window._panel_docks.get("ncs.panels.bluesky")

    # Open Devices panel - add to left then tabify with Bluesky
    window.add_panel(
        "ncs.panels.devices",
        area=Qt.DockWidgetArea.LeftDockWidgetArea,
    )
    devices_dock = window._panel_docks.get("ncs.panels.devices")

    # Tabify Claude, Bluesky and Devices (stack as tabs)
    if claude_dock and bluesky_dock:
        window.tabifyDockWidget(claude_dock, bluesky_dock)
    if bluesky_dock and devices_dock:
        window.tabifyDockWidget(bluesky_dock, devices_dock)

    # Raise Bluesky as the default visible tab
    if bluesky_dock:
        bluesky_dock.raise_()

    # Open Logbook panel on the right
    window.add_panel(
        "ncs.panels.logbook",
        area=Qt.DockWidgetArea.RightDockWidgetArea,
    )
    logbook_dock = window._panel_docks.get("ncs.panels.logbook")

    # Use splitDockWidget to ensure left-right layout
    first_left_dock = claude_dock or bluesky_dock
    if first_left_dock and logbook_dock:
        window.splitDockWidget(first_left_dock, logbook_dock, Qt.Orientation.Horizontal)

    panels_opened = ["Bluesky", "Devices", "Logbook"]
    if claude_dock:
        panels_opened.insert(0, "Claude")
    logger.info("Opened default panels: {} (left), Logbook (right)", "+".join(panels_opened[:-1]))


def main() -> int:
    """Run the NCS application.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
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

    # Setup Bluesky RunEngine and plans
    _setup_bluesky(app)

    # Setup plugin system and load preload plugins (before main window)
    _setup_plugins(app)

    # Setup first launch (welcome project)
    project_service = ProjectService.get_instance()
    _setup_first_launch(project_service)

    # Create and set main window (after preload plugins applied theme)
    window = NCSMainWindow()
    window.set_config_manager(config)
    app.set_main_window(window)

    # Connect engine to toolbar control
    engine = get_engine()
    window.set_engine(engine)

    # Setup default panel layout
    window.setup_default_layout()

    # Run the application
    return app.run()


def cli() -> None:
    """Entry point for console scripts.

    This wrapper ensures the process exit code is set correctly
    when running via installed entry points (e.g., `ncs` command).
    """
    sys.exit(main())


if __name__ == "__main__":
    cli()
