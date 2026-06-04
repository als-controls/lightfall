"""Plugin loader with background threading.

The PluginLoader discovers plugin manifests via entry points, loads plugin
classes in a background thread, and instantiates them. It uses Qt threading
integration for non-blocking operation.
"""

from __future__ import annotations

import importlib
import time
from collections import deque
from collections.abc import Generator
from datetime import datetime
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from loguru import logger
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from lightfall.plugins.errors import (
    PluginInitError,
    PluginLoadError,
    PluginNotFoundError,
    PluginStatus,
)
from lightfall.plugins.info import PluginInfo
from lightfall.plugins.manifest import PluginManifest
from lightfall.plugins.registry import PluginRegistry
from lightfall.plugins.types import PluginType
from lightfall.utils.threads import (
    QThreadFutureIterator,
    initialize_main_thread_invoker,
    invoke_in_main_thread,
    is_main_thread,
)

if TYPE_CHECKING:
    pass


class PluginLoader(QObject):
    """Loads plugins from manifests with background threading.

    The loader discovers plugin manifests via entry points, loads plugin
    classes in a background thread, and instantiates them. It addresses
    common issues from Xi-CAM:

    1. Duplicate detection: Uses unique IDs (type:name) tracked in registry
    2. Graceful failure: Catches exceptions, marks plugins as failed
    3. Background loading: Non-blocking loading via QThreadFutureIterator
    4. Priority loading: get_plugin_by_name() loads immediately if needed

    Signals:
        plugin_loaded: Emitted when a plugin is ready (PluginInfo).
        plugin_failed: Emitted when a plugin fails to load (PluginInfo).
        loading_started: Emitted when background loading begins.
        loading_complete: Emitted when loading finishes (successful, failed).

    Example::

        loader = PluginLoader(registry)
        loader.register_plugin_type("plan", PlanPlugin)
        loader.discover_manifests()
        loader.start_loading()
        loader.loading_complete.connect(on_plugins_ready)
    """

    ENTRY_POINT_GROUP = "lightfall.plugins"

    # Signals
    plugin_loaded = Signal(object)  # PluginInfo
    plugin_failed = Signal(object)  # PluginInfo
    loading_started = Signal()
    loading_complete = Signal(int, int)  # successful, failed

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Initialize the plugin loader.

        Args:
            registry: Plugin registry. Creates a new one if not provided.
            parent: Qt parent object.
        """
        super().__init__(parent)
        self._registry = registry or PluginRegistry()
        self._plugin_types: dict[str, type[PluginType]] = {}

        # Queues for two-phase loading
        self._load_queue: deque[PluginInfo] = deque()
        self._init_queue: deque[PluginInfo] = deque()

        # Track what's being processed to detect cycles
        self._loading: set[str] = set()
        self._initializing: set[str] = set()

        # Background loading state
        self._worker: QThreadFutureIterator | None = None
        self._is_loading = False

        # Counters
        self._successful = 0
        self._failed = 0

    @property
    def registry(self) -> PluginRegistry:
        """Get the plugin registry."""
        return self._registry

    @property
    def is_loading(self) -> bool:
        """Check if background loading is in progress."""
        return self._is_loading

    def register_plugin_type(
        self,
        type_name: str,
        plugin_type_class: type[PluginType],
    ) -> None:
        """Register a plugin type.

        Args:
            type_name: Unique type identifier (e.g., "plan").
            plugin_type_class: The PluginType subclass.
        """
        self._plugin_types[type_name] = plugin_type_class
        logger.debug("Registered plugin type: {}", type_name)

    def get_plugin_type(self, type_name: str) -> type[PluginType] | None:
        """Get a registered plugin type class.

        Args:
            type_name: The type identifier.

        Returns:
            The PluginType subclass or None.
        """
        return self._plugin_types.get(type_name)

    def discover_manifests(self) -> int:
        """Discover plugin manifests via entry points.

        Entry point format in pyproject.toml::

            [project.entry-points."lightfall.plugins"]
            my_beamline = "my_plugin.manifest:manifest"

        Returns:
            Number of manifests discovered.
        """
        count = 0

        try:
            eps = entry_points(group=self.ENTRY_POINT_GROUP)
        except TypeError:
            # Fallback for older Python
            all_eps = entry_points()
            eps = all_eps.get(self.ENTRY_POINT_GROUP, [])

        for ep in eps:
            try:
                manifest = ep.load()
                if isinstance(manifest, PluginManifest):
                    self._process_manifest(manifest)
                    count += 1
                    logger.info(
                        "Discovered manifest '{}' with {} plugins from {}",
                        manifest.name,
                        len(manifest.plugins),
                        ep.value,
                    )
                else:
                    logger.warning(
                        "Entry point {} does not provide a PluginManifest, got {}",
                        ep.name,
                        type(manifest).__name__,
                    )
            except Exception as e:
                logger.error(
                    "Failed to load manifest from entry point {}: {}",
                    ep.name,
                    e,
                )

        logger.info("Discovered {} plugin manifest(s)", count)
        return count

    def load_manifest(self, manifest: PluginManifest) -> None:
        """Load plugins from a manifest directly (not via entry point).

        Args:
            manifest: The manifest to load.
        """
        self._process_manifest(manifest)

    def _get_disabled_plugin_ids(self) -> set[str]:
        """Get the set of disabled plugin IDs from preferences.

        Returns:
            Set of unique_id strings for disabled plugins.
        """
        try:
            from lightfall.ui.preferences.manager import PreferencesManager

            prefs = PreferencesManager.get_instance()
            disabled_list = prefs.get("disabled_plugins", [])
            if isinstance(disabled_list, list):
                return set(disabled_list)
        except Exception as e:
            logger.debug("Could not load disabled plugins preference: {}", e)
        return set()

    def _process_manifest(self, manifest: PluginManifest) -> None:
        """Process a manifest and queue its plugins.

        Args:
            manifest: The manifest to process.
        """
        disabled_ids = self._get_disabled_plugin_ids()

        for entry in manifest.plugins:
            # Check if this type is known
            if entry.type_name not in self._plugin_types:
                logger.warning(
                    "Unknown plugin type '{}' for plugin '{}', skipping",
                    entry.type_name,
                    entry.name,
                )
                continue

            # Create PluginInfo
            plugin_info = PluginInfo(
                type_name=entry.type_name,
                name=entry.name,
                import_path=entry.import_path,
                manifest_name=manifest.name,
                status=PluginStatus.DISCOVERED,
                preload=entry.preload,
            )

            # Try to register (checks for duplicates)
            if self._registry.register(plugin_info):
                # Check if this plugin is disabled (but never disable settings:plugins)
                if (
                    plugin_info.unique_id in disabled_ids
                    and plugin_info.unique_id != "settings:plugins"
                ):
                    plugin_info.status = PluginStatus.DISABLED
                    logger.info(
                        "Plugin '{}' is disabled, not loading",
                        plugin_info.unique_id,
                    )
                else:
                    self._load_queue.append(plugin_info)
                    plugin_info.status = PluginStatus.QUEUED_LOAD
                    logger.debug("Queued plugin for loading: {}", plugin_info.unique_id)

    def load_preload_plugins(self) -> tuple[int, int]:
        """Load all plugins marked with preload=True synchronously.

        Call this BEFORE creating the main window to ensure preload plugins
        (like appearance/theme) are ready before any UI is shown.

        Returns:
            Tuple of (successful_count, failed_count).

        Example::

            loader = PluginLoader(registry)
            loader.register_plugin_type("settings", SettingsPlugin)
            loader.load_manifest(builtin_manifest)
            loader.discover_manifests()

            # Load preload plugins before creating window
            ok, failed = loader.load_preload_plugins()
            logger.info("Preload plugins: {} ok, {} failed", ok, failed)

            # NOW create main window (after theme is applied)
            window = MainWindow()

            # Start background loading for remaining plugins
            loader.start_loading()
        """
        # Separate preload plugins from regular queue
        preload_infos: list[PluginInfo] = []
        remaining: deque[PluginInfo] = deque()

        for info in self._load_queue:
            if info.preload:
                preload_infos.append(info)
            else:
                remaining.append(info)

        self._load_queue = remaining

        if not preload_infos:
            logger.debug("No preload plugins to load")
            return 0, 0

        logger.info("Loading {} preload plugin(s) synchronously", len(preload_infos))

        # Load preload plugins synchronously
        successful = 0
        failed = 0

        for info in preload_infos:
            if self._load_plugin_class(info):
                if self._instantiate_plugin(info):
                    successful += 1
                    self._register_with_type_registry(info)
                    self.plugin_loaded.emit(info)

                    # Call on_loaded for immediate application (e.g., theme)
                    if hasattr(info.instance, "on_loaded"):
                        try:
                            info.instance.on_loaded()
                            logger.debug(
                                "Called on_loaded() for preload plugin: {}",
                                info.unique_id,
                            )
                        except Exception as e:
                            logger.error(
                                "Error in on_loaded() for {}: {}",
                                info.unique_id,
                                e,
                            )
                else:
                    failed += 1
                    self.plugin_failed.emit(info)
            else:
                failed += 1
                self.plugin_failed.emit(info)

        logger.info(
            "Preload plugin loading complete: {} successful, {} failed",
            successful,
            failed,
        )
        return successful, failed

    def start_loading(self) -> None:
        """Start background loading of queued plugins.

        Emits loading_started and loading_complete signals.
        """
        if self._is_loading:
            logger.warning("Plugin loading already in progress")
            return

        if not self._load_queue:
            logger.info("No plugins to load")
            self.loading_complete.emit(0, 0)
            return

        self._is_loading = True
        self._successful = 0
        self._failed = 0

        self.loading_started.emit()
        logger.info("Starting background plugin loading ({} plugins)", len(self._load_queue))

        # Ensure threading invoker is initialized on main thread before
        # background thread tries to use invoke_in_main_thread.
        initialize_main_thread_invoker()

        # Start background thread
        self._worker = QThreadFutureIterator(
            self._load_plugins_generator,
            yield_slot=self._on_plugin_processed,
            finished_slot=self._on_loading_finished,
            except_slot=self._on_loading_error,
            key="plugin_loader",
            name="PluginLoader",
        )
        self._worker.start()

    def _load_plugins_generator(self) -> Generator[PluginInfo, None, None]:
        """Generator for background plugin loading.

        Yields PluginInfo after each plugin is processed for progress updates.
        """
        # Phase 1: Load classes (import)
        while self._load_queue:
            plugin_info = self._load_queue.popleft()

            success = self._load_plugin_class(plugin_info)
            if success:
                self._init_queue.append(plugin_info)
                plugin_info.status = PluginStatus.QUEUED_INIT
            else:
                self._failed += 1

            yield plugin_info

        # Phase 2: Instantiate
        while self._init_queue:
            plugin_info = self._init_queue.popleft()

            success = self._instantiate_plugin(plugin_info)
            if success:
                self._successful += 1
            else:
                self._failed += 1

            yield plugin_info

    def _on_plugin_processed(self, plugin_info: PluginInfo) -> None:
        """Handle plugin processing completion (called in main thread).

        Args:
            plugin_info: The processed plugin.
        """
        if plugin_info.is_ready:
            self.plugin_loaded.emit(plugin_info)
            self._register_with_type_registry(plugin_info)
        elif plugin_info.is_failed:
            self.plugin_failed.emit(plugin_info)

    def _on_loading_finished(self) -> None:
        """Handle loading completion (called in main thread)."""
        self._is_loading = False
        self._worker = None
        logger.info(
            "Plugin loading complete: {} successful, {} failed",
            self._successful,
            self._failed,
        )
        self.loading_complete.emit(self._successful, self._failed)

    def _on_loading_error(self, error: Exception) -> None:
        """Handle loading error (called in main thread).

        Args:
            error: The exception that occurred.
        """
        self._is_loading = False
        self._worker = None
        logger.error("Plugin loading failed with error: {}", error)
        self.loading_complete.emit(self._successful, self._failed)

    def _load_plugin_class(self, plugin_info: PluginInfo) -> bool:
        """Load a plugin class (import).

        Args:
            plugin_info: Plugin to load.

        Returns:
            True if successful.
        """
        unique_id = plugin_info.unique_id

        # Check for circular dependency
        if unique_id in self._loading:
            logger.error(
                "Circular dependency detected while loading {}",
                unique_id,
            )
            plugin_info.status = PluginStatus.FAILED_LOAD
            plugin_info.error = "Circular dependency"
            return False

        self._loading.add(unique_id)
        plugin_info.status = PluginStatus.LOADING

        try:
            # Parse import path: "module.path:ClassName"
            module_path, class_name = plugin_info.import_path.rsplit(":", 1)

            logger.debug("Loading plugin class: {} from {}", class_name, module_path)

            module = importlib.import_module(module_path)
            plugin_class = getattr(module, class_name)

            # Validate against plugin type
            plugin_type_class = self._plugin_types[plugin_info.type_name]
            if not plugin_type_class.validate_class(plugin_class):
                raise TypeError(
                    f"{class_name} is not a valid {plugin_info.type_name} plugin"
                )

            plugin_info.plugin_class = plugin_class
            plugin_info.load_time = datetime.now()

            logger.debug("Loaded plugin class: {}", unique_id)
            return True

        except Exception as e:
            logger.error("Failed to load plugin {}: {}", unique_id, e)
            plugin_info.status = PluginStatus.FAILED_LOAD
            plugin_info.error = str(e)
            return False

        finally:
            self._loading.discard(unique_id)

    def _instantiate_plugin(self, plugin_info: PluginInfo) -> bool:
        """Instantiate a loaded plugin class.

        Args:
            plugin_info: Plugin to instantiate.

        Returns:
            True if successful.
        """
        unique_id = plugin_info.unique_id

        # Check for circular instantiation
        if unique_id in self._initializing:
            logger.error(
                "Circular dependency detected while instantiating {}",
                unique_id,
            )
            plugin_info.status = PluginStatus.FAILED_INIT
            plugin_info.error = "Circular instantiation"
            return False

        self._initializing.add(unique_id)
        plugin_info.status = PluginStatus.INITIALIZING

        try:
            plugin_class = plugin_info.plugin_class
            plugin_type_class = self._plugin_types[plugin_info.type_name]

            # Instantiate if singleton, otherwise store class
            if plugin_type_class.is_singleton:
                logger.debug("Instantiating singleton plugin: {}", unique_id)
                plugin_info.instance = plugin_class()
            else:
                # For non-singletons, store the class itself
                plugin_info.instance = plugin_class

            plugin_info.status = PluginStatus.READY

            logger.info("Plugin ready: {}", unique_id)
            return True

        except Exception as e:
            logger.error("Failed to instantiate plugin {}: {}", unique_id, e)
            plugin_info.status = PluginStatus.FAILED_INIT
            plugin_info.error = str(e)
            return False

        finally:
            self._initializing.discard(unique_id)

    def _register_with_type_registry(self, plugin_info: PluginInfo) -> None:
        """Register plugin with type-specific registry.

        For example, PlanPlugins get registered with PlanRegistry,
        EnginePlugins get registered with EngineRegistry.

        Args:
            plugin_info: The ready plugin.
        """
        if plugin_info.type_name == "plan":
            try:
                from lightfall.acquire.plans.registry import PlanRegistry

                plan_registry = PlanRegistry.get_instance()
                plan_plugin = plugin_info.instance

                if hasattr(plan_plugin, "get_plan_info"):
                    plan_info = plan_plugin.get_plan_info()
                    try:
                        plan_registry.register(
                            plan_info.name,
                            plan_info.func,
                            plan_info.category,
                        )
                        logger.debug(
                            "Registered plan '{}' with PlanRegistry",
                            plan_info.name,
                        )
                    except ValueError:
                        # Already registered
                        logger.warning(
                            "Plan '{}' already in PlanRegistry",
                            plan_info.name,
                        )
            except ImportError:
                logger.debug("PlanRegistry not available, skipping plan registration")

        elif plugin_info.type_name == "engine":
            try:
                from lightfall.acquire.engine.registry import EngineRegistry

                engine_registry = EngineRegistry.get_instance()
                engine_plugin = plugin_info.instance

                if hasattr(engine_plugin, "name"):
                    engine_registry.register(engine_plugin)
                    logger.debug(
                        "Registered engine '{}' with EngineRegistry",
                        engine_plugin.name,
                    )
            except ImportError:
                logger.debug("EngineRegistry not available, skipping engine registration")

        elif plugin_info.type_name == "statusbar":
            # Statusbar plugins don't need type-specific registration here.
            # They are loaded by StatusBarManager when the main window is created.
            logger.debug(
                "Statusbar plugin '{}' registered, will be loaded by StatusBarManager",
                plugin_info.name,
            )

        elif plugin_info.type_name == "controller":
            try:
                from lightfall.ui.widgets.controller_registry import ControllerPluginRegistry

                controller_registry = ControllerPluginRegistry.get_instance()
                controller_plugin = plugin_info.instance

                controller_registry.register(controller_plugin)
                logger.debug(
                    "Registered controller plugin '{}' with ControllerPluginRegistry",
                    controller_plugin.name,
                )
            except ImportError:
                logger.debug(
                    "ControllerPluginRegistry not available, "
                    "skipping controller registration"
                )

        elif plugin_info.type_name == "agent":
            try:
                from lightfall.plugins.agent_plugin import AgentPlugin
                from lightfall.ui.panels.claude.agent_registry import AgentRegistry

                instance = plugin_info.instance
                if not isinstance(instance, AgentPlugin):
                    logger.error(
                        "Agent plugin '{}' class {} is not an AgentPlugin subclass; skipping",
                        plugin_info.name, type(instance).__name__,
                    )
                else:
                    AgentRegistry.get_instance().register(instance)
                    logger.debug("Registered agent plugin '{}' with AgentRegistry", instance.name)
            except ImportError:
                logger.debug("AgentRegistry not available, skipping agent registration")

        elif plugin_info.type_name == "panel":
            try:
                from lightfall.ui.panels.registry import PanelRegistry

                panel_registry = PanelRegistry.get_instance()
                panel_plugin = plugin_info.instance
                panel_class = panel_plugin.get_panel_class()

                panel_registry.register(panel_class, replace=True)
                logger.debug(
                    "Registered panel '{}' with PanelRegistry",
                    panel_class.panel_metadata.id,
                )
            except ImportError:
                logger.debug(
                    "PanelRegistry not available, skipping panel registration"
                )

        elif plugin_info.type_name == "theme":
            try:
                from lightfall.ui.theme.registry import ThemeRegistry

                theme_registry = ThemeRegistry.get_instance()
                theme_plugin = plugin_info.instance

                theme_registry.register(theme_plugin)
                logger.debug(
                    "Registered theme '{}' with ThemeRegistry",
                    theme_plugin.name,
                )
            except ImportError:
                logger.debug("ThemeRegistry not available, skipping theme registration")

        elif plugin_info.type_name == "visualization":
            try:
                from lightfall.visualization.registry import VisualizationRegistry

                viz_registry = VisualizationRegistry.get_instance()
                viz_plugin = plugin_info.instance

                viz_registry.register_visualization(viz_plugin, replace=True)
                logger.debug(
                    "Registered visualization '{}' with VisualizationRegistry",
                    viz_plugin.name,
                )
            except ImportError:
                logger.debug(
                    "VisualizationRegistry not available, skipping visualization registration"
                )

    def get_plugin_by_name(
        self,
        name: str,
        type_name: str | None = None,
        timeout: float = 10.0,
    ) -> Any | None:
        """Get a plugin by name, loading it immediately if necessary.

        This implements the Xi-CAM pattern of priority loading - if a plugin
        is not yet ready, it will be loaded synchronously.

        Args:
            name: Plugin name.
            type_name: Plugin type (required if name is ambiguous).
            timeout: Maximum seconds to wait for loading.

        Returns:
            Plugin instance or None if not found.

        Raises:
            PluginNotFoundError: If plugin is not registered.
            PluginLoadError: If plugin failed to load.
            PluginInitError: If plugin failed to initialize.
            TimeoutError: If loading times out.
        """
        # Find the plugin info
        plugin_info = None
        if type_name:
            plugin_info = self._registry.get(type_name, name)
        else:
            # Search all types
            for t in self._registry.get_types():
                info = self._registry.get(t, name)
                if info:
                    if plugin_info is not None:
                        raise ValueError(
                            f"Multiple plugins named '{name}' exist. "
                            "Specify type_name to disambiguate."
                        )
                    plugin_info = info

        if plugin_info is None:
            raise PluginNotFoundError(type_name or "any", name)

        if plugin_info.is_ready:
            return plugin_info.instance

        if plugin_info.is_failed:
            if plugin_info.status == PluginStatus.FAILED_LOAD:
                raise PluginLoadError(
                    plugin_info.unique_id,
                    plugin_info.error or "Unknown error",
                )
            else:
                raise PluginInitError(
                    plugin_info.unique_id,
                    plugin_info.error or "Unknown error",
                )

        # Try to load it now (priority loading)
        if plugin_info.status in (PluginStatus.QUEUED_LOAD, PluginStatus.DISCOVERED):
            # Remove from queue if present
            try:
                self._load_queue.remove(plugin_info)
            except ValueError:
                pass

            if not self._load_plugin_class(plugin_info):
                raise PluginLoadError(
                    plugin_info.unique_id,
                    plugin_info.error or "Unknown error",
                )

        if plugin_info.status == PluginStatus.QUEUED_INIT:
            # Remove from queue if present
            try:
                self._init_queue.remove(plugin_info)
            except ValueError:
                pass

            if not self._instantiate_plugin(plugin_info):
                raise PluginInitError(
                    plugin_info.unique_id,
                    plugin_info.error or "Unknown error",
                )

            # Register with type-specific registry
            invoke_in_main_thread(self._register_with_type_registry, plugin_info)

        # Wait for loading/initializing to complete
        if plugin_info.is_loading:
            start_time = time.monotonic()
            while plugin_info.is_loading:
                if is_main_thread():
                    app = QApplication.instance()
                    if app:
                        app.processEvents()
                else:
                    time.sleep(0.01)

                if time.monotonic() - start_time > timeout:
                    raise TimeoutError(
                        f"Plugin '{plugin_info.unique_id}' loading timed out"
                    )

        if plugin_info.is_ready:
            return plugin_info.instance

        # Must have failed
        if plugin_info.status == PluginStatus.FAILED_LOAD:
            raise PluginLoadError(
                plugin_info.unique_id,
                plugin_info.error or "Unknown error",
            )
        else:
            raise PluginInitError(
                plugin_info.unique_id,
                plugin_info.error or "Unknown error",
            )

    def load_all_sync(self) -> tuple[int, int]:
        """Load all queued plugins synchronously.

        Blocks until all plugins are loaded. Use for testing or when
        background loading is not desired.

        Returns:
            Tuple of (successful_count, failed_count).
        """
        successful = 0
        failed = 0

        # Phase 1: Load classes
        while self._load_queue:
            plugin_info = self._load_queue.popleft()

            if self._load_plugin_class(plugin_info):
                self._init_queue.append(plugin_info)
                plugin_info.status = PluginStatus.QUEUED_INIT
            else:
                failed += 1

        # Phase 2: Instantiate
        while self._init_queue:
            plugin_info = self._init_queue.popleft()

            if self._instantiate_plugin(plugin_info):
                successful += 1
                self._register_with_type_registry(plugin_info)
                self.plugin_loaded.emit(plugin_info)
            else:
                failed += 1
                self.plugin_failed.emit(plugin_info)

        logger.info(
            "Synchronous plugin loading complete: {} successful, {} failed",
            successful,
            failed,
        )
        return successful, failed

    def cancel_loading(self) -> None:
        """Cancel background loading if in progress."""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._is_loading = False
            logger.info("Plugin loading cancelled")

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools.

        Returns:
            Dictionary with loader state and statistics.
        """
        return {
            "is_loading": self._is_loading,
            "registered_types": list(self._plugin_types.keys()),
            "queued_for_load": len(self._load_queue),
            "queued_for_init": len(self._init_queue),
            "last_successful": self._successful,
            "last_failed": self._failed,
            "registry": self._registry.get_introspection_data(),
        }
