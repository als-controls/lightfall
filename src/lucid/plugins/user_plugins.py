"""Service for loading and managing user-defined plugins with hot-reload.

User plugins are Python files in ~/lucid/plugins/. Plugin classes auto-register
via PluginType.__init_subclass__ when defined; UserPluginService tracks
registrations so that unload + hot-reload work correctly.

Hot-reload warning: Reloading a plugin may cause instability if the
old version's objects are still in use. A stability warning is shown
on first hot-reload.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

if TYPE_CHECKING:
    pass


@dataclass
class PluginRegistration:
    """Record of a single registration made by a plugin."""

    registry_type: str  # e.g., "panel", "skill", "mcp_tool"
    key: str  # e.g., panel_id, skill_name, tool_name


@dataclass
class PluginInfo:
    """Information about a loaded user plugin."""

    file_path: Path
    module_name: str
    registrations: list[PluginRegistration] = field(default_factory=list)
    is_temp: bool = False
    load_error: str | None = None



class UserPluginService(QObject):
    """Service for loading user-defined plugins with hot-reload.

    User plugins are Python files in ~/lucid/plugins/ that self-register
    with type-specific registries on execution.

    Hot-reload warning: Reloading a plugin may cause instability if the
    old version's objects are still in use. A stability warning is shown
    on first hot-reload.

    Signals:
        plugin_loaded: Emitted when a plugin is loaded (file path).
        plugin_unloaded: Emitted when a plugin is unloaded (file path).
        plugin_error: Emitted on load error (file path, error message).
        plugins_refreshed: Emitted after all plugins are refreshed.
        hot_reload_warning: Emitted on first hot-reload (file path).

    Example:
        >>> service = UserPluginService.get_instance()
        >>> service.load_all_plugins()
        >>> service.enable_hot_reload(True)
    """

    _instance: ClassVar[UserPluginService | None] = None
    _lock = threading.RLock()

    # Signals
    plugin_loaded = Signal(str)  # file path
    plugin_unloaded = Signal(str)  # file path
    plugin_error = Signal(str, str)  # file path, error message
    plugins_refreshed = Signal()
    hot_reload_warning = Signal(str)  # file path (first reload triggers warning)

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the user plugin service."""
        super().__init__(parent)
        self._plugins_dir = Path.home() / "lucid" / "plugins"
        self._loaded_plugins: dict[str, PluginInfo] = {}  # file_path_str -> PluginInfo
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._hot_reload_enabled = False
        self._has_shown_hot_reload_warning = False
        self._temp_dir: Path | None = None
        self._temp_plugins: set[Path] = set()
        self._current_load: PluginInfo | None = None  # set during load_plugin_from_file

        # Ensure plugins directory exists
        self._ensure_directory()

        logger.debug("UserPluginService initialized, plugins dir: {}", self._plugins_dir)

    @classmethod
    def get_instance(cls) -> UserPluginService:
        """Get the singleton UserPluginService instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance._cleanup_temp_dir()
                cls._instance.deleteLater()
            cls._instance = None

    def _ensure_directory(self) -> bool:
        """Ensure the plugins directory exists.

        Returns:
            True if directory exists or was created.
        """
        try:
            self._plugins_dir.mkdir(parents=True, exist_ok=True)
            # Watch the directory for new/deleted files
            if str(self._plugins_dir) not in self._watcher.directories():
                self._watcher.addPath(str(self._plugins_dir))
            return True
        except Exception as e:
            logger.error("Failed to create plugins directory: {}", e)
            return False

    def get_plugins_directory(self) -> Path:
        """Get the user plugins directory path.

        Returns:
            Path to ~/lucid/plugins/
        """
        return self._plugins_dir

    def open_plugins_folder(self) -> bool:
        """Open the plugins folder in the system file explorer.

        Returns:
            True if successful.
        """
        import os
        import subprocess
        import sys as sys_module

        self._ensure_directory()

        try:
            if sys_module.platform == "win32":
                os.startfile(str(self._plugins_dir))
            elif sys_module.platform == "darwin":
                subprocess.run(["open", str(self._plugins_dir)], check=True)
            else:
                subprocess.run(["xdg-open", str(self._plugins_dir)], check=True)
            return True
        except Exception as e:
            logger.error("Failed to open plugins folder: {}", e)
            return False

    def load_all_plugins(self) -> list[tuple[Path, Exception | None]]:
        """Load all plugins from the plugins directory.

        Returns:
            List of (path, Exception or None) tuples.
        """
        results: list[tuple[Path, Exception | None]] = []

        if not self._plugins_dir.exists():
            logger.debug("Plugins directory does not exist: {}", self._plugins_dir)
            return results

        for py_file in self._plugins_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue  # Skip private files

            success = self.load_plugin_from_file(py_file)
            error = None
            if not success:
                info = self._loaded_plugins.get(str(py_file))
                if info and info.load_error:
                    error = Exception(info.load_error)
                else:
                    error = Exception("Unknown error loading plugin")
            results.append((py_file, error if not success else None))

        self.plugins_refreshed.emit()
        loaded_count = sum(1 for _, err in results if err is None)
        logger.info(
            "Loaded {} user plugin(s) from {}", loaded_count, self._plugins_dir
        )

        return results

    def load_plugin_from_file(self, path: Path) -> bool:
        """Load a single plugin from a file.

        Args:
            path: Path to the Python file.

        Returns:
            True if successful.
        """
        path_str = str(path)

        # If already loaded, this is a reload
        if path_str in self._loaded_plugins:
            return self.reload_plugin(path)

        try:
            # Read the code
            code = path.read_text(encoding="utf-8")

            # Syntax check
            try:
                compile(code, str(path), "exec")
            except SyntaxError as e:
                error_msg = f"Syntax error at line {e.lineno}: {e.msg}"
                logger.warning("Syntax error in {}: {}", path.name, error_msg)
                self._loaded_plugins[path_str] = PluginInfo(
                    file_path=path,
                    module_name=f"lucid_user_plugins.{path.stem}",
                    load_error=error_msg,
                )
                self.plugin_error.emit(path_str, error_msg)
                return False

            # Create namespace with standard imports available
            namespace: dict[str, Any] = {
                "__name__": f"lucid_user_plugins.{path.stem}",
                "__file__": str(path),
            }

            # Pre-construct PluginInfo so enqueue() can append registrations
            # into it while exec() runs class definitions.
            module_name = f"lucid_user_plugins.{path.stem}"
            info = PluginInfo(
                file_path=path,
                module_name=module_name,
                is_temp=path in self._temp_plugins,
            )

            # Pre-register a stub module in sys.modules BEFORE exec() so that
            # inspect.getfile(cls) can resolve the source file.  Without this,
            # PluginType.__init_subclass__ falls back to inspect.getfile() which
            # raises TypeError for classes whose module isn't in sys.modules yet,
            # causing the auto-enqueue to silently bail out.
            stub_module = type(sys)("lucid_user_plugins")  # ModuleType
            stub_module.__name__ = module_name
            stub_module.__file__ = str(path)
            stub_module.__loader__ = None
            stub_module.__spec__ = None
            sys.modules[module_name] = stub_module

            self._current_load = info
            try:
                try:
                    exec(code, namespace)
                except Exception as e:
                    error_msg = f"Execution error: {type(e).__name__}: {e}"
                    logger.warning("Error loading {}: {}", path.name, error_msg)
                    info.load_error = error_msg
                    self._loaded_plugins[path_str] = info
                    self.plugin_error.emit(path_str, error_msg)
                    # Clean up the stub module on failure
                    sys.modules.pop(module_name, None)
                    return False
            finally:
                self._current_load = None

            # Store plugin info (registrations list was populated by enqueue())
            self._loaded_plugins[path_str] = info

            # Update the module entry with the fully populated namespace
            sys.modules[module_name] = type(
                "module", (), {"__dict__": namespace, "__name__": module_name}
            )()

            # Watch file for changes
            if str(path) not in self._watcher.files():
                self._watcher.addPath(str(path))

            self.plugin_loaded.emit(path_str)
            logger.debug(
                "Loaded user plugin: {} ({} registrations)",
                path.name,
                len(info.registrations),
            )

            return True

        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to load plugin from {}: {}", path, e)
            self._loaded_plugins[path_str] = PluginInfo(
                file_path=path,
                module_name=f"lucid_user_plugins.{path.stem}",
                load_error=error_msg,
            )
            self.plugin_error.emit(path_str, error_msg)
            return False

    def unload_plugin(self, path: Path) -> bool:
        """Unload a plugin by path.

        Args:
            path: Path to the plugin file.

        Returns:
            True if unloaded.
        """
        path_str = str(path)
        info = self._loaded_plugins.get(path_str)

        if info is None:
            return False

        # Unregister from all registries
        for reg in info.registrations:
            self._unregister(reg)

        # Remove from sys.modules
        if info.module_name in sys.modules:
            del sys.modules[info.module_name]

        # Remove from watcher
        if path_str in self._watcher.files():
            self._watcher.removePath(path_str)

        # Remove from tracking
        del self._loaded_plugins[path_str]

        self.plugin_unloaded.emit(path_str)
        logger.debug("Unloaded user plugin: {}", path.name)

        return True

    def reload_plugin(self, path: Path) -> bool:
        """Reload a plugin (unload + load).

        Args:
            path: Path to the plugin file.

        Returns:
            True if successful.
        """
        path_str = str(path)

        # Unload first
        was_loaded = path_str in self._loaded_plugins
        if was_loaded:
            self.unload_plugin(path)

        # Load again
        success = False
        if path.exists():
            # Need to re-add to watcher since we removed it
            success = self.load_plugin_from_file(path)

        # Show hot-reload warning on first reload
        if was_loaded and not self._has_shown_hot_reload_warning:
            self._has_shown_hot_reload_warning = True
            self.hot_reload_warning.emit(path_str)

        return success

    def _unregister(self, reg: PluginRegistration) -> None:
        """Unregister a single registration from its registry.

        Args:
            reg: The registration to remove.
        """
        try:
            if reg.registry_type == "panel":
                from lucid.ui.panels.registry import PanelRegistry

                registry = PanelRegistry.get_instance()
                # Destroy singleton if it exists
                registry.destroy_singleton(reg.key)
                registry.unregister(reg.key)
                logger.debug("Unregistered panel: {}", reg.key)

            elif reg.registry_type in ("agent", "skill", "mcp_tool"):
                from lucid.ui.panels.claude.agent_registry import AgentRegistry

                AgentRegistry.get_instance().unregister(reg.key)
                logger.debug("Unregistered agent plugin: {}", reg.key)

        except Exception as e:
            logger.warning(
                "Failed to unregister {} '{}': {}",
                reg.registry_type,
                reg.key,
                e,
            )

    def enqueue(self, cls: type, file_path: Path) -> None:
        """Register a PluginType subclass auto-discovered via __init_subclass__.

        Called from PluginType.__init_subclass__ during exec() of a user
        plugin file. Instantiates the class, registers it with the
        appropriate registry, and tracks the registration on the in-flight
        PluginInfo so unload can clean up later.
        """
        if self._current_load is None:
            # Class defined outside a load_plugin_from_file call (e.g., test
            # framework introspection). Just register it; we can't track unload.
            logger.debug(
                "auto-enqueue for {} outside a load operation; registering only",
                cls.__name__,
            )

        try:
            instance = cls()
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Could not instantiate user plugin {} from {}: {}",
                cls.__name__, file_path, e,
            )
            return

        registry_type = cls.type_name
        plugin_name = getattr(instance, "name", cls.__name__)

        try:
            if registry_type == "agent":
                from lucid.ui.panels.claude.agent_registry import AgentRegistry
                AgentRegistry.get_instance().register(instance)
                registration_key = plugin_name
            elif registry_type == "panel":
                from lucid.ui.panels.registry import PanelRegistry
                # PanelRegistry.register expects the BasePanel subclass (it reads
                # panel_metadata off it); the auto-discovered cls is the PanelPlugin
                # subclass, so route through get_panel_class().
                panel_class = instance.get_panel_class()
                PanelRegistry.get_instance().register(panel_class)
                registration_key = panel_class.panel_metadata.id
            else:
                logger.warning(
                    "auto-enqueue for type '{}' not supported (plugin {})",
                    registry_type, plugin_name,
                )
                return
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Failed to register user plugin {} (type={}): {}",
                plugin_name, registry_type, e,
            )
            return

        if self._current_load is not None:
            self._current_load.registrations.append(
                PluginRegistration(registry_type=registry_type, key=registration_key)
            )

        logger.debug("auto-enqueued user plugin: {} (type={})", plugin_name, registry_type)

    # Temporary plugins

    def create_temp_plugin(self, name: str, code: str) -> Path:
        """Create a temporary plugin file.

        Temporary plugins are written to a temp directory, loaded immediately,
        and cleaned up on application exit.

        Args:
            name: Plugin name (becomes filename).
            code: Python source code.

        Returns:
            Path to the temporary file.
        """
        if self._temp_dir is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="lucid_plugins_"))
            logger.debug("Created temp plugins directory: {}", self._temp_dir)

        file_path = self._temp_dir / f"{name}.py"
        file_path.write_text(code, encoding="utf-8")

        # Track as temp plugin
        self._temp_plugins.add(file_path)

        # Load immediately
        self.load_plugin_from_file(file_path)

        return file_path

    def list_temp_plugins(self) -> list[Path]:
        """List all temporary plugins.

        Returns:
            List of temporary plugin file paths.
        """
        return [p for p in self._temp_plugins if p.exists()]

    def _cleanup_temp_dir(self) -> None:
        """Clean up the temporary plugins directory."""
        if self._temp_dir and self._temp_dir.exists():
            try:
                # Unload all temp plugins first
                for path in list(self._temp_plugins):
                    self.unload_plugin(path)
                self._temp_plugins.clear()

                # Remove the directory
                shutil.rmtree(self._temp_dir)
                logger.debug("Cleaned up temp plugins directory: {}", self._temp_dir)
            except Exception as e:
                logger.warning("Failed to cleanup temp plugins directory: {}", e)

    # Hot-reload

    def enable_hot_reload(self, enabled: bool = True) -> None:
        """Enable or disable hot-reload on file changes.

        Args:
            enabled: Whether to enable hot-reload.
        """
        self._hot_reload_enabled = enabled
        logger.debug("Hot-reload {}", "enabled" if enabled else "disabled")

    def is_hot_reload_enabled(self) -> bool:
        """Check if hot-reload is enabled.

        Returns:
            True if hot-reload is enabled.
        """
        return self._hot_reload_enabled

    def _on_file_changed(self, path: str) -> None:
        """Handle file change notification.

        Args:
            path: Path to the changed file.
        """
        file_path = Path(path)

        if not self._hot_reload_enabled:
            return

        if not file_path.exists():
            # File was deleted
            if str(file_path) in self._loaded_plugins:
                self.unload_plugin(file_path)
                self.plugins_refreshed.emit()
        else:
            # File was modified - reload it
            self.reload_plugin(file_path)
            self.plugins_refreshed.emit()

        # Re-add the path to the watcher (Qt removes it after change)
        if file_path.exists() and str(path) not in self._watcher.files():
            self._watcher.addPath(path)

    def _on_directory_changed(self, path: str) -> None:
        """Handle directory change notification.

        Args:
            path: Path to the changed directory.
        """
        if not self._hot_reload_enabled:
            return

        # Rescan for new/deleted files
        current_files = {
            p for p in self._plugins_dir.glob("*.py") if not p.name.startswith("_")
        }
        loaded_paths = {
            info.file_path
            for info in self._loaded_plugins.values()
            if not info.is_temp
        }

        # Load new files
        for file_path in current_files - loaded_paths:
            self.load_plugin_from_file(file_path)

        # Unload deleted files
        for file_path in loaded_paths - current_files:
            self.unload_plugin(file_path)

        self.plugins_refreshed.emit()

    # Introspection

    def get_loaded_plugins(self) -> list[PluginInfo]:
        """Get all loaded plugin info.

        Returns:
            List of PluginInfo objects.
        """
        return list(self._loaded_plugins.values())

    def get_plugin_info(self, path: Path) -> PluginInfo | None:
        """Get info for a specific plugin.

        Args:
            path: Path to the plugin file.

        Returns:
            PluginInfo or None if not loaded.
        """
        return self._loaded_plugins.get(str(path))

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for debugging/MCP tools.

        Returns:
            Dictionary with service state and plugin information.
        """
        return {
            "plugins_dir": str(self._plugins_dir),
            "hot_reload_enabled": self._hot_reload_enabled,
            "loaded_plugin_count": len(self._loaded_plugins),
            "temp_plugin_count": len(self._temp_plugins),
            "plugins": [
                {
                    "file": info.file_path.name,
                    "path": str(info.file_path),
                    "module": info.module_name,
                    "is_temp": info.is_temp,
                    "has_error": info.load_error is not None,
                    "error": info.load_error,
                    "registrations": [
                        {"type": r.registry_type, "key": r.key}
                        for r in info.registrations
                    ],
                }
                for info in self._loaded_plugins.values()
            ],
        }
