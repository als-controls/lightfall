"""Main entry point for the LUCID application."""

from __future__ import annotations

import os
import sys

# Set Windows AppUserModelID BEFORE any Qt imports.
# This must happen before COM/Qt initialization for the taskbar icon to work.
# See: https://stackoverflow.com/a/1552105
if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gov.lbl.als.lucid")

from datetime import timedelta
from typing import TYPE_CHECKING


def _configure_remote_display() -> None:
    """Configure environment for remote displays BEFORE Qt is imported.

    Detects VNC/remote X11 and sets environment variables for software rendering.
    Must run before any Qt imports.
    """
    # Check for VNC-specific environment variables
    if os.environ.get("VNCDESKTOP") or os.environ.get("VNC_SESSION"):
        is_remote = True
    else:
        # Check DISPLAY for remote X11 or high display numbers (VNC)
        display = os.environ.get("DISPLAY", "")
        is_remote = False
        if display:
            # Remote X11: hostname:0
            if ":" in display and not display.startswith(":"):
                is_remote = True
            else:
                # High display numbers often indicate VNC (:1, :2, etc.)
                try:
                    display_num = int(display.split(":")[1].split(".")[0])
                    if display_num > 0:
                        is_remote = True
                except (IndexError, ValueError):
                    pass

    if is_remote:
        # Disable Vulkan at the loader level
        os.environ.setdefault("VK_ICD_FILENAMES", "")
        os.environ.setdefault("VK_DRIVER_FILES", "")
        # Force software rendering for Mesa/OpenGL
        os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        # Qt software rendering
        os.environ.setdefault("QT_QUICK_BACKEND", "software")
        os.environ.setdefault("QT_OPENGL", "software")
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
        # Chromium flags for WebEngine
        existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        remote_flags = [
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-vulkan",
            "--disable-features=Vulkan,VulkanFromANGLE,UseSkiaRenderer",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        for flag in remote_flags:
            if flag not in existing:
                existing = f"{existing} {flag}".strip()
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = existing


# Configure remote display BEFORE any Qt imports
_configure_remote_display()

from PySide6.QtCore import Qt  # noqa: E402

from lucid.acquire import get_engine
from lucid.acquire.plans import get_registry as get_plan_registry
from lucid.auth.providers import LocalAuthProvider
from lucid.auth.session import SessionManager
from lucid.config import ConfigManager
from lucid.core import NCSApplication
from lucid.devices import DeviceCatalog
from lucid.devices.backends import BCSBackend, HappiBackend, MockBackend
from lucid.project import ProjectService, create_welcome_project
from lucid.ui import NCSMainWindow
from lucid.ui.panels.registry import PanelRegistry
from lucid.ui.preferences import PreferencesManager
from lucid.ui.theme import ThemeManager
from lucid.ui.widgets.warning_banner import DismissableWarningBanner
from lucid.utils.editor_launcher import CodeEditor, is_editor_available
from lucid.utils.logging import logger
from lucid.utils.sentry import clear_user as sentry_clear_user
from lucid.utils.sentry import init_sentry
from lucid.utils.sentry import set_user as sentry_set_user

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

    # Initialize connection manager with settings
    from lucid.devices.connection_manager import DeviceConnectionManager

    connection_manager = DeviceConnectionManager.get_instance()
    connection_manager.load_settings()
    services.register_instance(DeviceConnectionManager, connection_manager)

    logger.debug("Application services registered")


def _setup_ca_tunnel() -> None:
    """Start the CA UDP-to-TCP tunnel if configured.

    When connecting to a remote CA Gateway through an SSH tunnel,
    CA clients need UDP for PV search but SSH only forwards TCP.
    This service bridges the gap by forwarding local UDP packets
    through the TCP tunnel.

    Must run BEFORE any EPICS/ophyd initialization.
    """
    prefs = PreferencesManager.get_instance()

    enabled = prefs.get("ca_tunnel_enabled", False)
    if not enabled:
        return

    gateway = prefs.get("ca_tunnel_gateway", "localhost:5099")

    from lucid.services.ca_tunnel import CATunnelService

    service = CATunnelService.get_instance()
    if service.start(gateway=gateway):
        logger.info("CA tunnel active: gateway={}", gateway)

        # Increase ophyd's default connection timeout for tunneled connections
        try:
            import ophyd

            ophyd.signal.EpicsSignalBase.set_defaults(
                connection_timeout=10.0, timeout=10.0
            )
            logger.info("Set ophyd signal timeouts to 10s for remote access")
        except Exception as e:
            logger.debug("Could not set ophyd timeouts: {}", e)
    else:
        logger.error("Failed to start CA tunnel for gateway={}", gateway)


def _setup_devices() -> None:
    """Setup the device catalog based on user preferences.

    Initializes the DeviceCatalog with all enabled backends.
    Multiple backends can be active simultaneously — their devices
    are merged into a single unified catalog.
    """
    catalog = DeviceCatalog.get_instance()
    prefs = PreferencesManager.get_instance()

    # Check which backends are enabled (with legacy compat)
    legacy_backend = prefs.get("device_backend", "mock")
    mock_enabled = prefs.get("device_mock_enabled", legacy_backend == "mock")
    bcs_enabled = prefs.get("device_bcs_enabled", legacy_backend == "bcs")
    happi_enabled = prefs.get("device_happi_enabled", False)

    # If nothing is explicitly enabled, fall back to mock
    if not any([mock_enabled, bcs_enabled, happi_enabled]):
        mock_enabled = True

    if mock_enabled:
        include_noisy = prefs.get("device_mock_include_noisy", True)
        catalog.add_backend(MockBackend(include_noisy=include_noisy))
        logger.info("Mock backend enabled (include_noisy={})", include_noisy)

    if bcs_enabled:
        host = prefs.get("device_bcs_host", "localhost")
        port = prefs.get("device_bcs_port", 5577)
        timeout_ms = prefs.get("device_bcs_timeout_ms", 5000)
        beamline = prefs.get("device_bcs_beamline") or None
        catalog.add_backend(BCSBackend(
            host=host, port=port, timeout_ms=timeout_ms, beamline=beamline,
        ))
        logger.info("BCS backend enabled ({}:{})", host, port)

    if happi_enabled:
        happi_path = prefs.get("device_happi_path", "") or None
        happi_beamline = prefs.get("device_happi_beamline", "") or None

        # Get instantiation mode (new setting) with fallback to legacy bool
        instantiate_mode = prefs.get("device_instantiate_mode", None)
        if instantiate_mode is None:
            # Legacy fallback: convert bool to mode string
            legacy_instantiate = prefs.get("device_happi_instantiate", False)
            instantiate_mode = "blocking" if legacy_instantiate else "none"

        # Get connection timeout
        connection_timeout = prefs.get("device_connection_timeout", 5.0)

        catalog.add_backend(HappiBackend(
            path=happi_path,
            beamline=happi_beamline,
            instantiate=instantiate_mode,
            connection_timeout=connection_timeout,
        ))
        logger.info(
            "Happi backend enabled (path={}, mode={}, timeout={}s)",
            happi_path or "default",
            instantiate_mode,
            connection_timeout,
        )

    if catalog.connect():
        device_count = len(catalog.get_all_devices())
        backends_str = ", ".join(catalog.backends.keys())
        logger.info("Device catalog initialized: {} devices from [{}]", device_count, backends_str)
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

    # Load user-defined plans from ~/lucid/plans/
    from lucid.acquire.plans import UserPlanService

    user_plan_service = UserPlanService.get_instance()
    results = user_plan_service.load_all_plans()
    user_plan_count = sum(1 for _, r in results if not isinstance(r, Exception))
    services.register_instance(UserPlanService, user_plan_service)

    logger.info(
        "Bluesky initialized: engine state={}, {} plans available ({} user plans)",
        engine.state_name,
        len(plan_registry),
        user_plan_count,
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
    from lucid.plugins import MCPToolPlugin, PluginLoader, PluginRegistry
    from lucid.plugins.builtin_manifest import builtin_manifest
    from lucid.plugins.controller_plugin import ControllerPlugin
    from lucid.plugins.engine_plugin import EnginePlugin
    from lucid.plugins.panel_plugin import PanelPlugin
    from lucid.plugins.settings_plugin import SettingsPlugin
    from lucid.plugins.skill_plugin import SkillPlugin
    from lucid.plugins.statusbar_plugin import StatusBarPlugin

    services = app.services

    # Create registry and loader
    registry = PluginRegistry()
    loader = PluginLoader(registry)

    # Register plugin types (theme must be first to load before appearance settings)
    from lucid.plugins.theme_plugin import ThemePlugin

    loader.register_plugin_type("theme", ThemePlugin)
    loader.register_plugin_type("settings", SettingsPlugin)
    loader.register_plugin_type("engine", EnginePlugin)
    loader.register_plugin_type("mcp_tool", MCPToolPlugin)
    loader.register_plugin_type("statusbar", StatusBarPlugin)
    loader.register_plugin_type("controller", ControllerPlugin)
    loader.register_plugin_type("panel", PanelPlugin)
    loader.register_plugin_type("skill", SkillPlugin)

    # Visualization plugin types
    from lucid.plugins.heuristic_plugin import HeuristicPlugin
    from lucid.plugins.visualization_plugin import VisualizationPlugin

    loader.register_plugin_type("visualization", VisualizationPlugin)
    loader.register_plugin_type("heuristic", HeuristicPlugin)

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


def _setup_user_plugins(app: NCSApplication) -> None:
    """Load user-defined plugins from ~/lucid/plugins/.

    User plugins are Python files that self-register with type-specific
    registries (PanelRegistry, SkillRegistry, etc.) on execution.

    Args:
        app: The LUCID application instance.
    """
    from lucid.plugins.user_plugins import UserPluginService

    services = app.services

    # Create and initialize the service
    service = UserPluginService.get_instance()

    # Load all user plugins
    results = service.load_all_plugins()
    user_plugin_count = sum(1 for _, err in results if err is None)

    # Enable hot-reload by default
    service.enable_hot_reload(True)

    # Register service
    services.register_instance(UserPluginService, service)

    if user_plugin_count > 0:
        logger.info("Loaded {} user plugin(s) from ~/lucid/plugins/", user_plugin_count)
    else:
        logger.debug("No user plugins loaded")


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


def _show_startup_login(window: NCSMainWindow) -> None:
    """Show login dialog on application startup.

    Creates an independent dialog (parent=None) so it gets its own
    taskbar entry on Windows, rather than being owned by the hidden
    main window.

    Args:
        window: The main window instance (unused, kept for API compatibility).
    """
    from lucid.resources import get_app_icon
    from lucid.ui.dialogs import LoginDialog
    from lucid.ui.dialogs.login_dialog import LoginResult

    # Create dialog without parent - gets its own taskbar entry
    dialog = LoginDialog(
        parent=None,
        title="Welcome to LUCID",
        allow_guest=True,
        show_on_expiry=False,
    )

    # Explicitly set icon for taskbar (required when parent=None)
    app_icon = get_app_icon()
    if not app_icon.isNull():
        dialog.setWindowIcon(app_icon)
        sizes = app_icon.availableSizes()
        logger.info("Login dialog icon set ({} sizes: {})", len(sizes), sizes)
    else:
        logger.warning("App icon is null for login dialog")

    # Verify icon was set on dialog
    dialog_icon = dialog.windowIcon()
    logger.info("Dialog windowIcon isNull: {}", dialog_icon.isNull())

    dialog.exec()
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

    # Connect to state changed to detect session expiry and update Sentry context
    def on_state_changed(new_state, old_state) -> None:
        from lucid.auth.session import AuthState

        if new_state == AuthState.AUTHENTICATED:
            # User logged in - set Sentry user context
            user = session_manager.current_user
            if user:
                sentry_set_user(
                    user_id=user.username,
                    username=user.username,
                    roles=user.roles,
                )
        elif old_state == AuthState.AUTHENTICATED and new_state == AuthState.UNAUTHENTICATED:
            # Session expired or was invalidated - clear Sentry user context
            sentry_clear_user()
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

    # Setup services (must be before Sentry so PreferencesManager has ConfigManager)
    _setup_services(app, config)

    # Initialize Sentry error reporting (after services so proxy settings are available)
    if init_sentry():
        logger.info("Sentry error reporting initialized")
    else:
        logger.warning("Sentry initialization failed or disabled")

    # Install error collector to capture recent errors for bug reporting
    from lucid.utils.error_collector import ErrorCollector

    ErrorCollector.get_instance().install()

    # Setup authentication
    _setup_auth(config)

    # Start CA tunnel if configured (must be before device setup)
    _setup_ca_tunnel()

    # Setup device catalog with mock backend
    _setup_devices()

    # Setup Bluesky RunEngine and plans
    _setup_bluesky(app)

    # Setup Tiled data catalog service
    _setup_tiled(app, config)

    # Setup plugin system and load preload plugins (before main window)
    _setup_plugins(app)

    # Load user plugins from ~/lucid/plugins/
    _setup_user_plugins(app)

    # Setup first launch (welcome project)
    project_service = ProjectService.get_instance()
    _setup_first_launch(project_service)

    # Create and set main window (after preload plugins applied theme)
    window = NCSMainWindow()
    window.set_config_manager(config)
    app.set_main_window(window)

    # Connect engine to menubar control
    engine = get_engine()
    window.set_engine(engine)

    # Setup default panel layout
    window.setup_default_layout()

    # Check editor protocol handler (shows warning if PyCharm selected but Toolbox missing)
    _check_editor_protocol(window)

    # Setup session expiry handler
    _setup_session_expiry_handler(window)

    # Show login dialog on startup
    _show_startup_login(window)

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
