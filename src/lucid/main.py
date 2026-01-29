"""Main entry point for the LUCID application."""

from __future__ import annotations

import sys
from datetime import timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from lucid.auth.providers import LocalAuthProvider
from lucid.auth.session import SessionManager
from lucid.config import ConfigManager
from lucid.core import NCSApplication
from lucid.acquire import get_engine
from lucid.acquire.plans import get_registry as get_plan_registry
from lucid.devices import DeviceCatalog
from lucid.devices.backends import BCSBackend, MockBackend
from lucid.project import ProjectService, create_welcome_project
from lucid.ui import NCSMainWindow
from lucid.ui.panels.registry import PanelRegistry
from lucid.ui.preferences import PreferencesManager
from lucid.ui.theme import ThemeManager
from lucid.ui.widgets.warning_banner import DismissableWarningBanner
from lucid.utils.editor_launcher import CodeEditor, is_editor_available
from lucid.utils.logging import logger

if TYPE_CHECKING:
    pass


def _setup_auth(config: ConfigManager) -> None:
    """Setup authentication provider based on configuration.

    Set environment variable NCS_AUTH=local to force local auth for development.
    """
    import os

    session_manager = SessionManager.get_instance()

    auth_config = config.model.auth
    provider_type = auth_config.provider.type

    # Allow environment variable override for development
    env_auth = os.environ.get("NCS_AUTH", "").lower()
    if env_auth == "local":
        provider_type = "local"
        logger.info("Using local auth (NCS_AUTH=local)")

    if provider_type == "keycloak" and auth_config.provider.server_url:
        # Use Keycloak if configured
        try:
            from lucid.auth.providers.keycloak import KeycloakAuthProvider, KeycloakConfig

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

    # Panel registry - panels are registered via plugin system (preload plugins)
    # External entry point panels can still use ncs.panels entry points via discover_plugins()
    registry = PanelRegistry.get_instance()
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


def _setup_tiled(app: NCSApplication, config: ConfigManager) -> None:
    """Setup Tiled service if configured.

    Initializes the TiledService singleton and connects to the
    Tiled server if enabled in preferences.

    Args:
        app: The NCS application instance.
        config: The configuration manager.
    """
    from lucid.services.tiled_service import TiledAuthMode, TiledService

    services = app.services
    prefs = PreferencesManager.get_instance()

    # Create service singleton
    service = TiledService.get_instance()

    # Configure from preferences
    enabled = prefs.get("tiled_enabled", False)
    url = prefs.get("tiled_url", "")
    api_key = prefs.get("tiled_api_key") or None

    # Determine auth mode
    # If tiled_auth_mode is set in preferences, use that
    # Otherwise, auto-detect: use KEYCLOAK if auth provider is keycloak
    auth_mode_pref = prefs.get("tiled_auth_mode", "")
    if auth_mode_pref:
        auth_mode = TiledAuthMode(auth_mode_pref)
    elif config.model.auth.provider.type == "keycloak":
        # Auto-detect: if using Keycloak auth, use Keycloak for Tiled too
        auth_mode = TiledAuthMode.KEYCLOAK
        logger.info("Auto-detected Keycloak auth mode for Tiled")
    elif api_key:
        auth_mode = TiledAuthMode.API_KEY
    else:
        auth_mode = TiledAuthMode.NONE

    if enabled and url:
        service.configure(url=url, api_key=api_key, enabled=True, auth_mode=auth_mode)

        if auth_mode == TiledAuthMode.KEYCLOAK:
            # For Keycloak, connect to SessionManager and defer connection
            service.connect_session_manager()
            logger.info(
                "Tiled service initialized with Keycloak auth, waiting for user login"
            )
        else:
            # For API key or no auth, connect immediately
            service.connect_async()
            logger.info("Tiled service initialized, connecting in background")
    else:
        service.configure(url=url, api_key=api_key, enabled=False, auth_mode=auth_mode)
        logger.debug("Tiled service initialized (disabled)")

    # Register service
    services.register(TiledService, TiledService.get_instance)


def _setup_plugins(app: NCSApplication) -> None:
    """Setup the plugin system and load preload plugins.

    This must be called BEFORE creating the main window to ensure
    preload plugins (like appearance/theme) can apply settings before
    any UI is shown.

    Args:
        app: The LUCID application instance.
    """
    from lucid.plugins import PluginLoader, PluginRegistry, MCPToolPlugin
    from lucid.plugins.builtin_manifest import builtin_manifest
    from lucid.plugins.controller_plugin import ControllerPlugin
    from lucid.plugins.engine_plugin import EnginePlugin
    from lucid.plugins.panel_plugin import PanelPlugin
    from lucid.plugins.settings_plugin import SettingsPlugin
    from lucid.plugins.statusbar_plugin import StatusBarPlugin

    services = app.services

    # Create registry and loader
    registry = PluginRegistry()
    loader = PluginLoader(registry)

    # Register plugin types
    loader.register_plugin_type("settings", SettingsPlugin)
    loader.register_plugin_type("engine", EnginePlugin)
    loader.register_plugin_type("mcp_tool", MCPToolPlugin)
    loader.register_plugin_type("statusbar", StatusBarPlugin)
    loader.register_plugin_type("controller", ControllerPlugin)
    loader.register_plugin_type("panel", PanelPlugin)

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


def _show_startup_login(window: NCSMainWindow, config: ConfigManager) -> None:
    """Show login dialog on application startup if using Keycloak auth.

    Args:
        window: The main window instance.
        config: The configuration manager.
    """
    from lucid.ui.dialogs import LoginDialog
    from lucid.ui.dialogs.login_dialog import LoginResult

    auth_type = config.model.auth.provider.type

    # Only show login dialog for Keycloak auth
    if auth_type != "keycloak":
        logger.debug("Skipping startup login (not using Keycloak)")
        return

    dialog = LoginDialog(
        parent=window,
        title="Welcome to LUCID",
        allow_guest=True,
        show_on_expiry=False,
    )

    result = dialog.exec()
    login_result = dialog.login_result

    if login_result == LoginResult.AUTHENTICATED:
        logger.info("User authenticated at startup")
    elif login_result == LoginResult.GUEST:
        logger.info("User chose guest mode at startup")
    else:
        logger.info("User cancelled login dialog")


def _setup_session_expiry_handler(window: NCSMainWindow) -> None:
    """Setup handler to show login dialog when session expires.

    Args:
        window: The main window instance.
    """
    from lucid.ui.dialogs import LoginDialog

    session_manager = SessionManager.get_instance()

    def on_session_expired() -> None:
        """Show login dialog when session expires."""
        dialog = LoginDialog(
            parent=window,
            title="Session Expired",
            allow_guest=True,
            show_on_expiry=True,
        )
        dialog.exec()

    # Connect to state changed to detect session expiry
    def on_state_changed(new_state, old_state) -> None:
        from lucid.auth.session import AuthState

        if old_state == AuthState.AUTHENTICATED and new_state == AuthState.UNAUTHENTICATED:
            # Session expired or was invalidated
            logger.info("Session ended, showing login dialog")
            on_session_expired()

    session_manager.state_changed.connect(on_state_changed)


def _check_editor_protocol(main_window: NCSMainWindow) -> None:
    """Check if the configured editor's protocol handler is available.

    Shows a warning banner if PyCharm is selected but JetBrains Toolbox
    is not installed (jetbrains:// protocol not registered).

    Args:
        main_window: The main window to show the banner in.
    """
    prefs = PreferencesManager.get_instance()

    # Only check for PyCharm
    editor = prefs.get("code_editor", CodeEditor.VSCODE.value)
    if editor != CodeEditor.PYCHARM.value:
        return

    # Check if user has suppressed the warning
    if prefs.get("suppress_pycharm_warning", False):
        return

    # Check if jetbrains:// protocol is registered
    if is_editor_available(CodeEditor.PYCHARM):
        return

    # Show warning banner
    logger.warning("JetBrains Toolbox not detected, PyCharm code links may not work")

    from PySide6.QtCore import QTimer

    def show_banner() -> None:
        banner = DismissableWarningBanner(
            warning_id="jetbrains_toolbox",
            title="JetBrains Toolbox Required",
            message=(
                "Install JetBrains Toolbox for PyCharm code links to work. "
                "Toolbox registers the jetbrains:// protocol handler."
            ),
            parent=main_window.centralWidget(),
        )
        banner.permanently_dismissed.connect(
            lambda wid: prefs.set("suppress_pycharm_warning", True)
        )

        # Position at top of central widget
        banner.setFixedWidth(main_window.centralWidget().width() - 20)
        banner.move(10, 10)
        banner.show()
        banner.raise_()

    # Delay slightly to ensure window is fully shown
    QTimer.singleShot(500, show_banner)


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
        "lucid.panels.claude",
        area=Qt.DockWidgetArea.LeftDockWidgetArea,
    )
    if claude_panel:
        claude_dock = window._panel_docks.get("lucid.panels.claude")

    # Open Bluesky panel on the left
    window.add_panel(
        "lucid.panels.bluesky",
        area=Qt.DockWidgetArea.LeftDockWidgetArea,
    )
    bluesky_dock = window._panel_docks.get("lucid.panels.bluesky")

    # Open Devices panel - add to left then tabify with Bluesky
    window.add_panel(
        "lucid.panels.devices",
        area=Qt.DockWidgetArea.LeftDockWidgetArea,
    )
    devices_dock = window._panel_docks.get("lucid.panels.devices")

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
        "lucid.panels.logbook",
        area=Qt.DockWidgetArea.RightDockWidgetArea,
    )
    logbook_dock = window._panel_docks.get("lucid.panels.logbook")

    # Use splitDockWidget to ensure left-right layout
    first_left_dock = claude_dock or bluesky_dock
    if first_left_dock and logbook_dock:
        window.splitDockWidget(first_left_dock, logbook_dock, Qt.Orientation.Horizontal)

    panels_opened = ["Bluesky", "Devices", "Logbook"]
    if claude_dock:
        panels_opened.insert(0, "Claude")
    logger.info("Opened default panels: {} (left), Logbook (right)", "+".join(panels_opened[:-1]))


def main() -> int:
    """Run the LUCID application.

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

    # Setup Tiled data catalog service
    _setup_tiled(app, config)

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

    # Check editor protocol handler (shows warning if PyCharm selected but Toolbox missing)
    _check_editor_protocol(window)

    # Setup session expiry handler
    _setup_session_expiry_handler(window)

    # Show login dialog on startup (for Keycloak auth)
    _show_startup_login(window, config)

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
