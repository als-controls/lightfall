"""Service for loading and managing user-defined plugins with hot-reload.

User plugins are Python files in ~/lightfall/plugins/. Plugin classes auto-register
via PluginType.__init_subclass__ when defined; UserPluginService tracks
registrations so that unload + hot-reload work correctly.

Hot-reload warning: Reloading a plugin may cause instability if the
old version's objects are still in use. A stability warning is shown
on first hot-reload.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from lightfall.utils.git_tracker import GitTracker

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
    is_package: bool = False  # folder plugin loaded as a real package



class UserPluginService(QObject):
    """Service for loading user-defined plugins with hot-reload.

    User plugins are Python files in ~/lightfall/plugins/ that self-register
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
        self._plugins_dir = Path.home() / "lightfall" / "plugins"
        self._loaded_plugins: dict[str, PluginInfo] = {}  # file_path_str -> PluginInfo
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._hot_reload_enabled = False
        self._has_shown_hot_reload_warning = False
        self._temp_dir: Path | None = None
        self._temp_plugins: set[Path] = set()
        self._current_load: PluginInfo | None = None  # set during load_plugin_from_file
        # folder plugin path string -> single-shot debounce timer for hot reload
        self._reload_timers: dict[str, QTimer] = {}

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
            Path to ~/lightfall/plugins/
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

        entries, errors = self._discover(self._plugins_dir)
        for bad_path, message in errors:
            logger.warning("Plugin conflict, not loading {}: {}", bad_path, message)
            self.plugin_error.emit(str(bad_path), message)
            results.append((bad_path, Exception(message)))

        for py_file in entries:
            # Skip per-file commits on bulk startup load: each commit costs
            # 2 subprocess calls (git add + git diff --cached --quiet) even
            # when the file is unchanged. With 50 staff plugins that's 100
            # subprocesses fired on every Lightfall startup. The hot-reload
            # watcher and explicit edits still commit normally.
            success = self.load_plugin_from_file(py_file, _skip_commit=True)
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

    @staticmethod
    def _discover(root: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
        """Find plugin entry points directly under ``root``.

        Entry points are ``x.py`` files and ``x/`` folders containing an
        ``__init__.py``; names starting with ``_`` are skipped. When both
        ``x.py`` and ``x/`` exist, neither loads and both paths are returned
        as errors.

        Returns:
            ``(entries, errors)`` where errors are ``(path, message)`` pairs.
        """
        files: dict[str, Path] = {}
        folders: dict[str, Path] = {}
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if child.name.startswith("_"):
                    continue
                if child.is_file() and child.suffix == ".py":
                    files[child.stem] = child
                elif child.is_dir() and (child / "__init__.py").is_file():
                    folders[child.name] = child
        entries: list[Path] = []
        errors: list[tuple[Path, str]] = []
        for name in sorted(set(files) | set(folders)):
            if name in files and name in folders:
                msg = (
                    f"Both {files[name].name} and {folders[name].name}/ exist; "
                    "rename or remove one"
                )
                errors.append((files[name], msg))
                errors.append((folders[name], msg))
            else:
                entries.append(files.get(name) or folders[name])
        return entries, errors

    def _commit_change(self, path: Path, commit_msg: str | None) -> None:
        """Commit a change to ``path`` via the GitTracker singleton.

        Called on every terminal path of load_plugin_from_file where the file
        has been written to disk (success, syntax error, exec error, outer
        except). Failed loads still commit -- the file's presence is forensic
        evidence even when the load itself didn't take.

        No-op if the path doesn't exist (delete paths handle their own
        commit_removal in the watcher slot). The tracker swallows all errors,
        so this never raises.
        """
        if not path.exists():
            return  # delete path: handled elsewhere
        msg = commit_msg or f"auto: updated {path.name}"
        GitTracker.get_instance().commit([path], msg)

    def _commit_removal(self, path: Path, commit_msg: str) -> None:
        """Commit a removal of ``path`` via the GitTracker singleton.

        Mirrors :meth:`_commit_change` for the deletion path. The tracker
        swallows all errors, so this never raises.
        """
        GitTracker.get_instance().commit_removal([path], commit_msg)

    def load_plugin_from_file(
        self,
        path: Path,
        commit_msg: str | None = None,
        *,
        _skip_commit: bool = False,
        module_name: str | None = None,
    ) -> bool:
        """Load a single plugin from a file or a package folder.

        A folder (``path.is_dir()``) is loaded as a real package
        ``lightfall_user_plugins.<name>`` so relative imports and ``__file__``
        work. ``module_name`` (keyword-only) overrides ``<name>`` (default:
        the folder name); used for synced folders such as ``xas_tools@4``
        loaded as ``xas_tools``. Ignored for single files.

        Args:
            path: Path to the Python file.
            commit_msg: Optional subject for the auto-commit. Defaults to
                ``"auto: updated <name>"`` when omitted. Commits happen
                on every terminal branch (success, syntax error, exec error,
                outer except) -- even failed loads land in history as
                forensic evidence.
            _skip_commit: If True, do not invoke the GitTracker on any
                terminal path. Used by :meth:`load_all_plugins` so the
                bulk startup scan doesn't fire 2 subprocesses per file.
                Underscore-prefixed because it is not part of the public
                contract -- callers writing the file should always commit.

        Returns:
            True if successful.
        """
        path_str = str(path)

        # If already loaded, this is a reload
        if path_str in self._loaded_plugins:
            return self.reload_plugin(path, commit_msg=commit_msg)

        if path.is_dir():
            return self._load_folder_plugin(
                path, commit_msg, _skip_commit=_skip_commit, module_name=module_name
            )

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
                    module_name=f"lightfall_user_plugins.{path.stem}",
                    load_error=error_msg,
                )
                self.plugin_error.emit(path_str, error_msg)
                if not _skip_commit:
                    self._commit_change(path, commit_msg)
                return False

            # Create namespace with standard imports available
            namespace: dict[str, Any] = {
                "__name__": f"lightfall_user_plugins.{path.stem}",
                "__file__": str(path),
            }

            # Pre-construct PluginInfo so enqueue() can append registrations
            # into it while exec() runs class definitions.
            module_name = f"lightfall_user_plugins.{path.stem}"
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
            stub_module = type(sys)("lightfall_user_plugins")  # ModuleType
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
                    if not _skip_commit:
                        self._commit_change(path, commit_msg)
                    return False

                # Track BasePanel subclasses self-registered with PanelRegistry
                # at module scope (the canonical user-plugin pattern). These
                # don't go through PluginType.__init_subclass__ / enqueue, so
                # we discover them here and add them to registrations so unload
                # and hot-reload can remove them.
                self._track_self_registered_panels(namespace, info)
            finally:
                self._current_load = None

            # Store plugin info (registrations list was populated by enqueue()
            # and _track_self_registered_panels())
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

            if not _skip_commit:
                self._commit_change(path, commit_msg)
            return True

        except Exception as e:
            error_msg = str(e)
            logger.error("Failed to load plugin from {}: {}", path, e)
            self._loaded_plugins[path_str] = PluginInfo(
                file_path=path,
                module_name=f"lightfall_user_plugins.{path.stem}",
                load_error=error_msg,
            )
            self.plugin_error.emit(path_str, error_msg)
            if not _skip_commit:
                self._commit_change(path, commit_msg)
            return False

    def _load_folder_plugin(
        self,
        folder: Path,
        commit_msg: str | None,
        *,
        _skip_commit: bool,
        module_name: str | None,
    ) -> bool:
        """Load ``folder`` as the real package ``lightfall_user_plugins.<name>``."""
        path_str = str(folder)
        full_name = f"lightfall_user_plugins.{module_name or folder.name}"
        info = PluginInfo(file_path=folder, module_name=full_name, is_package=True)

        def fail(error_msg: str) -> bool:
            logger.warning("Error loading folder plugin {}: {}", folder.name, error_msg)
            info.load_error = error_msg
            self._purge_modules(full_name)
            self._loaded_plugins[path_str] = info
            self.plugin_error.emit(path_str, error_msg)
            if not _skip_commit:
                self._commit_change(folder, commit_msg)
            return False

        try:
            init_file = folder / "__init__.py"
            spec = importlib.util.spec_from_file_location(
                full_name, init_file, submodule_search_locations=[str(folder)]
            )
            if spec is None or spec.loader is None:
                return fail(f"Cannot import {init_file}")
            module = importlib.util.module_from_spec(spec)
            # Register before exec so relative imports and
            # inspect.getfile() (auto-enqueue) resolve.
            self._purge_modules(full_name)
            if "lightfall_user_plugins" not in sys.modules:
                # Namespace parent so the import system can resolve children.
                parent = type(sys)("lightfall_user_plugins")
                parent.__path__ = []
                sys.modules["lightfall_user_plugins"] = parent
            sys.modules[full_name] = module
            self._current_load = info
            try:
                try:
                    spec.loader.exec_module(module)
                except Exception as e:
                    return fail(f"Execution error: {type(e).__name__}: {e}")
                for mod_name, mod in list(sys.modules.items()):
                    if mod_name == full_name or mod_name.startswith(full_name + "."):
                        ns = getattr(mod, "__dict__", None)
                        if ns:
                            self._track_self_registered_panels(ns, info)
            finally:
                self._current_load = None
        except Exception as e:
            logger.error("Failed to load folder plugin {}: {}", folder, e)
            return fail(str(e))

        self._loaded_plugins[path_str] = info
        self._watch_folder(folder)
        self.plugin_loaded.emit(path_str)
        logger.debug(
            "Loaded folder plugin: {} ({} registrations)",
            folder.name, len(info.registrations),
        )
        if not _skip_commit:
            self._commit_change(folder, commit_msg)
        return True

    @staticmethod
    def _purge_modules(full_name: str) -> None:
        """Remove ``full_name`` and all of its submodules from sys.modules."""
        for key in list(sys.modules):
            if key == full_name or key.startswith(full_name + "."):
                del sys.modules[key]

    @staticmethod
    def _folder_watch_paths(folder: Path) -> list[str]:
        """The folder, every subfolder and every file below it."""
        paths = [str(folder)]
        for p in folder.rglob("*"):
            if "__pycache__" in p.parts:
                continue
            paths.append(str(p))
        return paths

    def _watch_folder(self, folder: Path) -> None:
        """Watch a folder plugin's folder, subfolders and files."""
        current = set(self._watcher.files()) | set(self._watcher.directories())
        new = [p for p in self._folder_watch_paths(folder) if p not in current]
        if new:
            self._watcher.addPaths(new)

    def _unwatch_folder(self, folder: Path) -> None:
        """Stop watching everything at or below ``folder``."""
        stale = [
            p
            for p in (*self._watcher.files(), *self._watcher.directories())
            if Path(p) == folder or Path(p).is_relative_to(folder)
        ]
        if stale:
            self._watcher.removePaths(stale)

    def _folder_plugin_containing(self, path: Path) -> Path | None:
        """Return the loaded folder plugin that contains ``path`` (or is it)."""
        for info in self._loaded_plugins.values():
            if info.is_package and (
                path == info.file_path or path.is_relative_to(info.file_path)
            ):
                return info.file_path
        return None

    def _schedule_folder_reload(self, folder: Path) -> None:
        """Debounce (300 ms) a reload of ``folder``; bursts of changes reload once."""
        key = str(folder)
        timer = self._reload_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(300)
            timer.timeout.connect(lambda f=folder: self._reload_folder_plugin(f))
            self._reload_timers[key] = timer
        timer.start()

    def _reload_folder_plugin(self, folder: Path) -> None:
        """Reload (or unload, if deleted) a folder plugin after a change."""
        if not self._hot_reload_enabled:
            return
        if not folder.exists():
            if str(folder) in self._loaded_plugins:
                self.unload_plugin(folder)
                self._commit_removal(folder, f"external delete: {folder.name}")
        else:
            self.reload_plugin(folder, commit_msg=f"external edit: {folder.name}")
        self.plugins_refreshed.emit()

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
        if info.is_package:
            self._purge_modules(info.module_name)
            self._unwatch_folder(path)
        elif info.module_name in sys.modules:
            del sys.modules[info.module_name]

        # Remove from watcher
        if path_str in self._watcher.files():
            self._watcher.removePath(path_str)

        # Remove from tracking
        del self._loaded_plugins[path_str]

        self.plugin_unloaded.emit(path_str)
        logger.debug("Unloaded user plugin: {}", path.name)

        return True

    def reload_plugin(
        self, path: Path, commit_msg: str | None = None,
    ) -> bool:
        """Reload a plugin (unload + load).

        Args:
            path: Path to the plugin file.
            commit_msg: Forwarded to ``load_plugin_from_file`` for the
                auto-commit subject.

        Returns:
            True if successful.
        """
        path_str = str(path)

        # Unload first
        old_info = self._loaded_plugins.get(path_str)
        was_loaded = old_info is not None
        if was_loaded:
            self.unload_plugin(path)

        # Load again
        success = False
        if path.exists():
            # Need to re-add to watcher since we removed it
            kwargs: dict[str, Any] = {}
            if old_info is not None and old_info.is_package:
                # keep the module name a caller (e.g. synced folder) chose
                kwargs["module_name"] = old_info.module_name.split(".", 1)[1]
            success = self.load_plugin_from_file(path, commit_msg=commit_msg, **kwargs)

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
                from lightfall.ui.panels.registry import PanelRegistry

                registry = PanelRegistry.get_instance()
                # Destroy singleton if it exists
                registry.destroy_singleton(reg.key)
                registry.unregister(reg.key)
                logger.debug("Unregistered panel: {}", reg.key)

            elif reg.registry_type in ("tool", "skill", "mcp_tool"):
                from lightfall.ui.panels.claude.tool_registry import ToolRegistry

                ToolRegistry.get_instance().unregister(reg.key)
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
            if registry_type == "tool":
                from lightfall.ui.panels.claude.tool_registry import ToolRegistry
                ToolRegistry.get_instance().register(instance)
                registration_key = plugin_name
            elif registry_type == "panel":
                from lightfall.ui.panels.registry import PanelRegistry
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

    def _track_self_registered_panels(
        self, namespace: dict[str, Any], info: PluginInfo,
    ) -> None:
        """Track BasePanel subclasses self-registered at module scope.

        The canonical user-panel pattern (per the ``panel_design`` skill) is::

            class MyPanel(BasePanel):
                panel_metadata = PanelMetadata(id=..., ...)
                ...
            PanelRegistry.get_instance().register(MyPanel, replace=True)

        ``BasePanel`` is not a ``PluginType`` so ``__init_subclass__`` does not
        fire the auto-enqueue path; the explicit ``register()`` call adds the
        class to ``PanelRegistry`` but bypasses our load tracking. Without
        tracking, ``unload_plugin`` and hot-reload leak the entry.

        After ``exec()`` we scan the populated namespace for ``BasePanel``
        subclasses whose ``panel_metadata.id`` is now in ``PanelRegistry`` and
        not already tracked from this load (e.g. via a PanelPlugin wrapper),
        and append a registration entry.
        """
        from lightfall.ui.panels.base import BasePanel
        from lightfall.ui.panels.registry import PanelRegistry

        registry = PanelRegistry.get_instance()
        already_tracked: set[str] = {
            reg.key for reg in info.registrations if reg.registry_type == "panel"
        }
        for v in namespace.values():
            if not isinstance(v, type):
                continue
            if not issubclass(v, BasePanel) or v is BasePanel:
                continue
            metadata = getattr(v, "panel_metadata", None)
            panel_id = getattr(metadata, "id", None)
            if not panel_id or panel_id in already_tracked:
                continue
            if registry.get(panel_id) is not None:
                info.registrations.append(
                    PluginRegistration(registry_type="panel", key=panel_id)
                )
                already_tracked.add(panel_id)
                logger.debug(
                    "tracked self-registered panel: {} (class {})",
                    panel_id, v.__name__,
                )

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
            self._temp_dir = Path(tempfile.mkdtemp(prefix="lightfall_plugins_"))
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

        folder = self._folder_plugin_containing(file_path)
        if folder is not None:
            self._schedule_folder_reload(folder)
            return

        if not file_path.exists():
            # File was deleted
            if str(file_path) in self._loaded_plugins:
                self.unload_plugin(file_path)
                self._commit_removal(
                    file_path, f"external delete: {file_path.name}"
                )
                self.plugins_refreshed.emit()
        else:
            # File was modified - reload it (commit happens in load_plugin_from_file)
            self.reload_plugin(
                file_path,
                commit_msg=f"external edit: {file_path.name}",
            )
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

        folder = self._folder_plugin_containing(Path(path))
        if folder is not None:
            self._schedule_folder_reload(folder)
            return

        # Rescan for new/deleted files and folders
        current_files = set(self._discover(self._plugins_dir)[0])
        loaded_paths = {
            info.file_path
            for info in self._loaded_plugins.values()
            if not info.is_temp
        }

        # Load new files
        for file_path in current_files - loaded_paths:
            self.load_plugin_from_file(
                file_path, commit_msg=f"external add: {file_path.name}"
            )

        # Unload deleted files
        for file_path in loaded_paths - current_files:
            self.unload_plugin(file_path)
            self._commit_removal(
                file_path, f"external delete: {file_path.name}"
            )

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
