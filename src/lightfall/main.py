"""Main entry point for the Lightfall application."""

from __future__ import annotations

import os
import sys

# Set Windows AppUserModelID BEFORE any Qt imports.
# This must happen before COM/Qt initialization for the taskbar icon to work.
# See: https://stackoverflow.com/a/1552105
if sys.platform == "win32":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("gov.lbl.als.lightfall")

# Install crash diagnostics before QApplication is constructed so
# faulthandler is enabled and Qt environment knobs (QT_LOGGING_RULES) are
# in place when Qt's logging system is first read. PySide6 is transitively
# imported by lightfall.utils, but importing the package does not construct a
# QCoreApplication — env vars are still in time.
from lightfall.utils import crash_diagnostics  # noqa: E402

crash_diagnostics.install()

from typing import TYPE_CHECKING, Any  # noqa: E402


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

from lightfall.acquire import get_engine  # noqa: E402
from lightfall.acquire.plans import get_registry as get_plan_registry  # noqa: E402
from lightfall.auth.session import AuthState, SessionManager  # noqa: E402
from lightfall.config import ConfigManager  # noqa: E402
from lightfall.core import LFApplication  # noqa: E402
from lightfall.devices import DeviceCatalog  # noqa: E402
from lightfall.devices.backends import BCSBackend, HappiBackend, MockBackend  # noqa: E402
from lightfall.ui import LFMainWindow  # noqa: E402
from lightfall.ui.panels.registry import PanelRegistry  # noqa: E402
from lightfall.ui.preferences import PreferencesManager  # noqa: E402
from lightfall.ui.theme import ThemeManager  # noqa: E402
from lightfall.ui.widgets.warning_banner import DismissableWarningBanner  # noqa: E402
from lightfall.utils.data_migration import migrate_legacy_data_dir  # noqa: E402
from lightfall.utils.editor_launcher import CodeEditor, is_editor_available  # noqa: E402
from lightfall.utils.logging import logger  # noqa: E402
from lightfall.utils.sentry import clear_user as sentry_clear_user  # noqa: E402
from lightfall.utils.sentry import init_sentry  # noqa: E402
from lightfall.utils.sentry import set_user as sentry_set_user  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from lightfall.plugins import PluginLoader


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
    if env_auth in ("local", "pam", "keycloak"):
        provider_type = env_auth
        logger.info("Using {} auth (NCS_AUTH={})", provider_type, env_auth)

    from lightfall.auth.provider_registry import AuthProviderRegistry
    from lightfall.auth.providers.builtin_plugins import (
        register_builtin_auth_plugins,
        seed_default_disabled_plugins,
    )

    # Apply ship-disabled-by-default plugin state once, before registration so
    # the disabled ones (e.g. local) are honored here and by the plugin loader.
    seed_default_disabled_plugins()

    registry = AuthProviderRegistry.get_instance()
    register_builtin_auth_plugins(
        registry, config=config, include_pam=(sys.platform != "win32")
    )

    # Pick the startup default provider (used for token refresh before any
    # dialog login). The dialog will call set_provider again on actual login.
    default_name = provider_type if registry.has(provider_type) else "local"
    # Keycloak needs a configured server_url; otherwise fall back to local
    # (parity with the previous _setup_auth guard).
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
        # The chosen provider may be unregistered/disabled (e.g. local ships
        # disabled by default and nothing else is configured). Use a direct
        # LocalAuthProvider purely for pre-login session plumbing — this does
        # not add a login button (those come from the registry).
        from lightfall.auth.providers.local import LocalAuthProvider
        from lightfall.ui.preferences.login_settings import LoginSettingsProvider

        provider = LocalAuthProvider(
            session_duration=LoginSettingsProvider.get_session_duration()
        )
        logger.info("Default auth provider: local (direct fallback)")

    session_manager.set_provider(provider)


def _setup_services(app: LFApplication, config: ConfigManager) -> None:
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
    # External entry point panels can still use lightfall.panels entry points via discover_plugins()
    registry = PanelRegistry.get_instance()
    services.register_instance(PanelRegistry, registry)

    # Device catalog with mock backend
    device_catalog = DeviceCatalog.get_instance()
    services.register_instance(DeviceCatalog, device_catalog)

    # Initialize connection manager with settings
    from lightfall.devices.connection_manager import DeviceConnectionManager

    connection_manager = DeviceConnectionManager.get_instance()
    connection_manager.load_settings()
    services.register_instance(DeviceConnectionManager, connection_manager)

    # Pipeline client - lazy singleton so IPCService and Tiled URL settings
    # are read at first request, not at startup.
    from lightfall.acquire.triggers.manager import TriggerManager
    from lightfall.pipelines import PipelineClient

    def _build_pipeline_client() -> PipelineClient:
        import socket

        from lightfall.core.services import ServiceRegistry
        from lightfall.ipc.service import IPCService
        from lightfall.services.tiled_service import get_tiled_base_url

        ipc = ServiceRegistry.get_instance().get(IPCService)
        session_manager = SessionManager.get_instance()
        return PipelineClient(
            ipc=ipc,
            host=socket.gethostname(),
            tiled_url=get_tiled_base_url().rstrip("/") + "/api/v1",
            key_provider=session_manager.get_minted_key,
        )

    services.register(PipelineClient, _build_pipeline_client)

    def _build_trigger_manager() -> TriggerManager:
        from lightfall.core.services import ServiceRegistry

        def _submit_via_pipeline_client(
            *,
            pipeline: str,
            run_uid: str,
            parameters: dict,
            input_access_blob: dict,
        ) -> None:
            client = ServiceRegistry.get_instance().get(PipelineClient, None)
            if client is None:
                logger.warning(
                    "TriggerManager: no PipelineClient registered; "
                    "dropping fire for pipeline={} run_uid={}",
                    pipeline, run_uid,
                )
                return
            session = SessionManager.get_instance()
            user_id = session.current_user.attributes.get(
                "preferred_username", ""
            )
            try:
                client.submit(
                    pipeline=pipeline,
                    input_run_uid=run_uid,
                    parameters=parameters,
                    input_access_blob=input_access_blob,
                    user_id=user_id,
                )
            except Exception as exc:
                logger.error(
                    "TriggerManager: submit failed for pipeline={} run_uid={}: {}",
                    pipeline, run_uid, exc,
                )

        return TriggerManager(
            engine=get_engine(),
            submit_callable=_submit_via_pipeline_client,
        )

    services.register(TriggerManager, _build_trigger_manager)

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

    from lightfall.services.ca_tunnel import CATunnelService

    service = CATunnelService.get_instance()
    if service.start(gateway=gateway):
        logger.info("CA tunnel active: gateway={}", gateway)

        # Increase connection timeout for tunneled connections.
        # ophyd passes timeout=1.0 to caproto's PV.wait_for_connection,
        # which is too tight for initial SSH-tunneled connections.
        # Use 3s minimum — enough for the tunnel round-trip without
        # making the UI sluggish for unconnected PVs.
        _tunnel_min_timeout = 3.0
        try:
            from caproto.threading.pyepics_compat import PV as _CaprotoPV

            _orig_wait = _CaprotoPV.wait_for_connection
            _CaprotoPV._orig_wait_for_connection = _orig_wait

            def _patched_wait(self, timeout=None):
                if timeout is None or timeout < _tunnel_min_timeout:
                    timeout = _tunnel_min_timeout
                return _orig_wait(self, timeout=timeout)

            _CaprotoPV.wait_for_connection = _patched_wait
            logger.info(
                "Patched caproto PV.wait_for_connection minimum timeout to {}s",
                _tunnel_min_timeout,
            )
        except Exception as e:
            logger.debug("Could not patch caproto timeout: {}", e)
        # Schedule exponential backoff retries for failed device connections.
        # The tunnel may not be fully working when devices first try to connect.
        # Retry at 2s, 4s, 8s, 16s, 32s, 64s after device setup completes.
        def _schedule_retries():
            from PySide6.QtCore import QTimer

            from lightfall.utils.threads import QThreadFuture

            delays = [2000, 4000, 8000, 16000, 32000, 64000]  # ms

            def _do_reconnect():
                from lightfall.devices import DeviceCatalog

                catalog = DeviceCatalog.get_instance()
                return catalog.reconnect_failed_devices(timeout=5.0)

            def _on_done(result):
                connected, failed = result
                if connected > 0:
                    logger.info(
                        "CA tunnel retry: {} devices reconnected, {} still failed",
                        connected,
                        failed,
                    )
                if failed == 0 or not delays:
                    logger.info("CA tunnel retry: all reachable devices connected")
                    return
                # Schedule next retry
                if delays:
                    next_delay = delays.pop(0)
                    QTimer.singleShot(next_delay, _start_retry)

            def _start_retry():
                thread = QThreadFuture(
                    _do_reconnect,
                    callback_slot=_on_done,
                    name="ca-tunnel-retry",
                )
                thread.start()

            if delays:
                first_delay = delays.pop(0)
                QTimer.singleShot(first_delay, _start_retry)

        # Store for later — will be called after _setup_devices()
        app_module = __import__(__name__)
        app_module._ca_tunnel_schedule_retries = _schedule_retries
    else:
        logger.error("Failed to start CA tunnel for gateway={}", gateway)


_UNSET = object()


def _resolve_enabled_backends(get: Any) -> tuple[bool, bool, bool]:
    """Decide which built-in device backends are enabled, from a prefs ``get``.

    ``get`` is a ``prefs.get(key, default)``-style callable. Returns
    ``(mock_enabled, bcs_enabled, happi_enabled)``.

    Rules:
    * Explicit ``device_{mock,bcs,happi}_enabled`` flags win verbatim.
    * Absent flags fall back to legacy ``device_backend`` ("mock"/"bcs").
    * The mock fallback fires ONLY for a fresh config where the user has
      expressed no backend preference at all (no flags AND no legacy key) — so a
      deliberate all-off (e.g. relying solely on a plugin-contributed backend
      like the CMS happi backend) is honored instead of being forced back to
      mock. This is the bug fix: previously an explicit all-false re-enabled mock.
    """
    legacy_backend = get("device_backend", _UNSET)
    legacy_is_mock = legacy_backend == "mock" if legacy_backend is not _UNSET else False
    legacy_is_bcs = legacy_backend == "bcs" if legacy_backend is not _UNSET else False
    mock_enabled = bool(get("device_mock_enabled", legacy_is_mock))
    bcs_enabled = bool(get("device_bcs_enabled", legacy_is_bcs))
    happi_enabled = bool(get("device_happi_enabled", False))

    no_preference = (
        legacy_backend is _UNSET
        and get("device_mock_enabled", _UNSET) is _UNSET
        and get("device_bcs_enabled", _UNSET) is _UNSET
        and get("device_happi_enabled", _UNSET) is _UNSET
    )
    if no_preference and not any([mock_enabled, bcs_enabled, happi_enabled]):
        mock_enabled = True
    return mock_enabled, bcs_enabled, happi_enabled


def _setup_devices() -> None:
    """Setup the device catalog based on user preferences.

    Initializes the DeviceCatalog with all enabled backends.
    Multiple backends can be active simultaneously — their devices
    are merged into a single unified catalog.
    """
    catalog = DeviceCatalog.get_instance()
    prefs = PreferencesManager.get_instance()

    mock_enabled, bcs_enabled, happi_enabled = _resolve_enabled_backends(prefs.get)

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


def _setup_bluesky(app: LFApplication) -> None:
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

    # Load user-defined plans from ~/lightfall/plans/
    from lightfall.acquire.plans import UserPlanService

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


def _setup_tiled(app: LFApplication, config: ConfigManager) -> None:
    """Setup Tiled service if configured.

    Initializes the TiledService singleton and connects to the
    Tiled server if enabled in preferences.

    Args:
        app: The NCS application instance.
        config: The configuration manager.
    """
    from lightfall.services.tiled_service import TiledAuthMode, TiledService

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


def _setup_plugins(app: LFApplication) -> None:
    """Setup the plugin system and load preload plugins.

    This must be called BEFORE creating the main window to ensure
    preload plugins (like appearance/theme) can apply settings before
    any UI is shown.

    Args:
        app: The Lightfall application instance.
    """
    from lightfall.plugins import AgentPlugin, PluginLoader, PluginRegistry
    from lightfall.plugins.builtin_manifest import builtin_manifest
    from lightfall.plugins.controller_plugin import ControllerPlugin
    from lightfall.plugins.device_backend_plugin import DeviceBackendPlugin
    from lightfall.plugins.engine_plugin import EnginePlugin
    from lightfall.plugins.panel_plugin import PanelPlugin
    from lightfall.plugins.settings_plugin import SettingsPlugin
    from lightfall.plugins.statusbar_plugin import StatusBarPlugin

    services = app.services

    # Create registry and loader
    registry = PluginRegistry()
    loader = PluginLoader(registry)

    # Register plugin types (theme must be first to load before appearance settings)
    from lightfall.plugins.theme_plugin import ThemePlugin

    loader.register_plugin_type("theme", ThemePlugin)
    loader.register_plugin_type("settings", SettingsPlugin)
    loader.register_plugin_type("engine", EnginePlugin)
    loader.register_plugin_type("agent", AgentPlugin)
    loader.register_plugin_type("statusbar", StatusBarPlugin)
    loader.register_plugin_type("controller", ControllerPlugin)
    loader.register_plugin_type("panel", PanelPlugin)
    loader.register_plugin_type("device_backend", DeviceBackendPlugin)
    from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin
    loader.register_plugin_type("auth_provider", AuthProviderPlugin)

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

    # NOTE: the background plugin wave (loader.start_loading) is intentionally
    # NOT started here. Only login-window plugins (auth providers, theme) load
    # before login via load_preload_plugins() above; everything else loads after
    # authentication. main() arms the wave via _arm_post_login_plugin_load().
    # See docs/superpowers/specs/2026-06-20-post-login-plugin-loading-design.md.

    logger.debug("Plugin system initialized")


def _arm_post_login_plugin_load(
    loader: PluginLoader, session_manager: SessionManager
) -> Callable[[], None]:
    """Arm a one-shot that starts the background plugin wave after login.

    ``loader.start_loading`` is deferred from startup to the first
    ``AUTHENTICATED`` transition, so non-login plugins (and any I/O they do on
    load/first render) never run while the modal login screen is up.

    Returns a ``fire()`` callable for the caller to invoke once the startup
    login dialog has closed: guest / cancelled outcomes never reach
    ``AUTHENTICATED`` but the app still runs (anonymously), so it must still
    load the post-login wave. ``fire()`` is idempotent — whichever of (the
    ``AUTHENTICATED`` transition, the already-authenticated guard, the caller's
    ``fire()``) happens first starts the wave; the rest are no-ops.

    See docs/superpowers/specs/2026-06-20-post-login-plugin-loading-design.md.
    """
    state = {"fired": False}

    def fire() -> None:
        if state["fired"]:
            return
        state["fired"] = True
        logger.info("Starting post-login plugin wave")
        loader.start_loading()

    def _on_state_changed(new_state: AuthState, _old_state: AuthState) -> None:
        if new_state == AuthState.AUTHENTICATED:
            fire()

    session_manager.state_changed.connect(_on_state_changed)

    # Defensive: a cached token / auto-login / NCS_AUTH dev override may already
    # be authenticated before we arm — fire now so we don't wait for a
    # transition that won't come.
    if session_manager.is_authenticated:
        fire()

    return fire


def _setup_user_plugins(app: LFApplication) -> None:
    """Load user-defined plugins from ~/lightfall/plugins/.

    User plugins are Python files in ~/lightfall/plugins/. Plugin classes
    auto-register via PluginType.__init_subclass__ when defined, so user
    files do not need explicit Registry.register() calls.

    Args:
        app: The Lightfall application instance.
    """
    from lightfall.plugins.user_plugins import UserPluginService

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
        logger.info("Loaded {} user plugin(s) from ~/lightfall/plugins/", user_plugin_count)
    else:
        logger.debug("No user plugins loaded")


def _show_startup_login(window: LFMainWindow) -> None:
    """Show login dialog on application startup.

    Creates an independent dialog (parent=None) so it gets its own
    taskbar entry on Windows, rather than being owned by the hidden
    main window.

    Args:
        window: The main window instance (unused, kept for API compatibility).
    """
    from lightfall.resources import get_app_icon
    from lightfall.ui.dialogs import LoginDialog
    from lightfall.ui.dialogs.login_dialog import LoginResult

    # Create dialog without parent - gets its own taskbar entry
    dialog = LoginDialog(
        parent=None,
        title="Welcome to Lightfall",
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


def _setup_session_expiry_handler(window: LFMainWindow) -> None:
    """Setup handler to show login dialog when session expires.

    Args:
        window: The main window instance.
    """
    from lightfall.ui.dialogs import LoginDialog

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
        from lightfall.auth.session import AuthState

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


def _check_editor_protocol(main_window: LFMainWindow) -> None:
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


def main() -> int:
    """Run the Lightfall application.

    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    # One-time rebrand migration of the user data directory.
    migrate_legacy_data_dir()

    # Get/create the application singleton
    app = LFApplication.get_instance()

    # Initialize with default settings
    app.initialize(log_level="DEBUG")

    # Bridge Qt's internal warnings (thread-affinity violations, queued
    # connection failures, paint-event errors) into loguru. Must run after
    # PySide6.QtCore is importable, which app.initialize() guarantees.
    crash_diagnostics.install_qt_bridge()

    # Get config manager
    config: ConfigManager = app.services.get(ConfigManager)

    # Setup services (must be before Sentry so PreferencesManager has ConfigManager)
    _setup_services(app, config)

    # Initialize Sentry error reporting (opt-in: requires SENTRY_DSN env var
    # or 'telemetry_dsn' preference; after services so proxy/preference
    # settings are available). init_sentry() logs why when inactive.
    if init_sentry():
        logger.info("Sentry error reporting initialized")

    # Install error collector to capture recent errors for bug reporting
    from lightfall.utils.error_collector import ErrorCollector

    ErrorCollector.get_instance().install()

    # Install full-tail log buffer so the embedded agent can look back
    # at recent activity when something unexpected happens.
    from lightfall.utils.log_buffer import LogBuffer

    LogBuffer.get_instance().install()

    # Setup authentication
    _setup_auth(config)

    # Start CA tunnel if configured (must be before device setup)
    _setup_ca_tunnel()

    # Setup device catalog with mock backend
    _setup_devices()

    # Start CA tunnel retries if tunnel is active
    app_module = __import__(__name__)
    _retry_fn = getattr(app_module, "_ca_tunnel_schedule_retries", None)
    if _retry_fn:
        _retry_fn()
        del app_module._ca_tunnel_schedule_retries

    # Setup Bluesky RunEngine and plans
    _setup_bluesky(app)

    # Setup Tiled data catalog service
    _setup_tiled(app, config)

    # Setup plugin system and load preload plugins (before main window)
    _setup_plugins(app)

    # Load user plugins from ~/lightfall/plugins/
    _setup_user_plugins(app)

    # Create and set main window (after preload plugins applied theme)
    window = LFMainWindow()
    window.set_config_manager(config)
    app.set_main_window(window)

    # Connect engine to menubar control
    engine = get_engine()
    window.set_engine(engine)

    # NOTE: the default panel layout is built from the PanelRegistry, which is
    # populated by the post-login plugin wave. It is therefore driven from the
    # main window's loading_complete handler (see _on_plugin_loading_complete),
    # not here pre-login.

    # Register built-in tutorials
    from lightfall.ui.tutorial import register_builtin_tutorials
    register_builtin_tutorials()

    # Check editor protocol handler (shows warning if PyCharm selected but Toolbox missing)
    _check_editor_protocol(window)

    # Setup session expiry handler
    _setup_session_expiry_handler(window)

    # Wire the post-login plugin wave. start_loading() is deferred (see
    # _setup_plugins) so only login-window plugins load before login. The main
    # window builds its default layout and restores saved state off
    # loading_complete; connect that BEFORE arming so an already-authenticated
    # fast start can't emit loading_complete before the slot is connected.
    from lightfall.plugins import PluginLoader

    session_manager = SessionManager.get_instance()
    loader = app.services.get(PluginLoader, None)
    if loader is not None:
        loader.loading_complete.connect(window._on_plugin_loading_complete)
        fire_plugin_wave = _arm_post_login_plugin_load(loader, session_manager)
    else:
        logger.warning(
            "PluginLoader service missing; post-login plugin wave not armed"
        )

        # No loader means no wave and no loading_complete to drive the layout.
        # Build the (empty) default layout directly so the window is still
        # usable rather than blank. Mirrors the pre-change unconditional
        # setup_default_layout()/proactive-init path.
        window._on_plugin_loading_complete(0, 0)

        def fire_plugin_wave() -> None:
            return None

    # Show login dialog on startup
    _show_startup_login(window)

    # Guest / cancelled login never reaches AUTHENTICATED, but the app still
    # runs anonymously — start the wave now that the login screen is dismissed
    # (no-op if the AUTHENTICATED transition already started it).
    fire_plugin_wave()

    # Register cleanup on exit — order matters:
    # 1. Stop CA tunnel (kills relay sockets, stops new data arriving)
    # 2. Disconnect devices (cancels ophyd connections)
    # 3. Disconnect caproto Context (tears down circuits + threads)
    # The watchdog ensures we exit within 5s even if caproto blocks.
    import threading as _threading

    from PySide6.QtCore import QCoreApplication

    def _cleanup_on_exit():
        # Schedule a hard exit as a safety net. If cleanup takes more
        # than 5 seconds (e.g. caproto ThreadPoolExecutor.shutdown()
        # blocks waiting for callbacks), force-terminate the process.
        import logging as _logging
        import os
        import time as _time

        # Mute caproto loggers during shutdown — stale commands arriving
        # on closed channels/circuits are expected and harmless.
        for _name in ("caproto", "caproto.ch", "caproto.circ", "caproto.ctx", "caproto.bcast"):
            _logging.getLogger(_name).setLevel(_logging.CRITICAL)

        def _log_active_threads(label: str = ""):
            threads = _threading.enumerate()
            non_daemon = [t for t in threads if not t.daemon and t.is_alive()]
            daemon = [t for t in threads if t.daemon and t.is_alive()]
            logger.info(
                "Active threads ({}): {} total, {} non-daemon, {} daemon",
                label, len(threads), len(non_daemon), len(daemon),
            )
            for t in non_daemon:
                logger.info(
                    "  [non-daemon] {!r} (ident={}, native_id={})",
                    t.name, t.ident, getattr(t, 'native_id', '?'),
                )
            for t in daemon:
                logger.debug(
                    "  [daemon]     {!r} (ident={}, native_id={})",
                    t.name, t.ident, getattr(t, 'native_id', '?'),
                )

        def _force_exit():
            _time.sleep(5)
            _log_active_threads("watchdog-5s")
            logger.warning("Shutdown taking too long, forcing exit")
            sys.stderr.flush()
            sys.stdout.flush()
            os._exit(0)

        _threading.Thread(
            target=_force_exit, daemon=True, name="shutdown-watchdog"
        ).start()

        # 0. Revoke this session's unleased service keys while the network
        #    stack is still fully alive. Keys embedded in dispatched pipeline
        #    jobs are exempt — detached executors may still be using them.
        try:
            from lightfall.auth.session import SessionManager

            SessionManager.get_instance().revoke_unleased_service_keys()
        except Exception:
            pass

        # 1. Halt any running Bluesky plan immediately so it doesn't
        #    try to read PVs while we're tearing down connections.
        try:
            import lightfall.acquire.engine as _eng_mod

            engine = _eng_mod._engine
            if engine is not None and hasattr(engine, '_RE') and engine._RE is not None:
                if engine._RE.state in ("running", "paused"):
                    logger.info("Halting Bluesky plan for shutdown")
                    engine._RE.halt()
        except Exception:
            pass

        # 2. Stop CA tunnel — kill the relay sockets so no more
        #    data arrives on caproto circuits. This prevents the
        #    "cannot schedule new futures after shutdown" errors that
        #    happen when data arrives after executor.shutdown().
        try:
            from lightfall.services.ca_tunnel import CATunnelService

            tunnel = CATunnelService.get_instance()
            if tunnel.is_running:
                tunnel.stop()
                logger.debug("CA tunnel stopped during shutdown")
        except Exception:
            pass

        # 3. Disconnect device catalog (ophyd devices, connection manager)
        try:
            catalog = DeviceCatalog.get_instance()
            catalog.disconnect()
            logger.debug("Device catalog disconnected during shutdown")
        except Exception:
            pass

        # 4. Shut down managed thread pools (dev-values, etc.)
        #    ThreadManager.shutdown() also does this via aboutToQuit,
        #    but belt-and-suspenders in case ordering varies.
        from lightfall.utils.threads import ManagedThreadPool

        ManagedThreadPool.shutdown_all(wait=False)
        logger.debug("Managed thread pools shut down")

        # 5. Skip caproto Context.disconnect().
        #    Previously we called ctx.disconnect(wait=False) here, but it
        #    triggers an access violation (0xC0000005) on Windows — caproto's
        #    C-level socket code touches memory freed by earlier teardown.
        #    With ManagedThreadPool (daemon threads) and CA tunnel stopped,
        #    all caproto threads will die automatically on process exit.
        logger.debug("Skipping caproto context disconnect (daemon threads exit with process)")

        # Log remaining threads so we can see what's blocking exit
        _log_active_threads("post-cleanup")
        sys.stderr.flush()
        sys.stdout.flush()

    QCoreApplication.instance().aboutToQuit.connect(_cleanup_on_exit)

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
