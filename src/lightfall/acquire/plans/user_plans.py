"""Service for loading and managing user-defined Bluesky plans.

User plans are Python files in ~/lightfall/plans/ that define a `plan` variable
which must be a callable (generator function) that can be run by the RunEngine.

Each file corresponds to one plan. The filename (without .py) becomes the plan name.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

from lightfall.content.clashes import Clash, ClashLog
from lightfall.content.sources import SourceItem, default_synced_items, specificity
from lightfall.utils.git_tracker import GitTracker

if TYPE_CHECKING:
    from lightfall.acquire.plans.registry import PlanInfo

# Template for new user plans
PLAN_TEMPLATE = '''"""{{name}} - Custom Bluesky plan.

{{description}}
"""
from __future__ import annotations

from typing import Any, Generator

import bluesky.plans as bp


def plan(
    detectors: list,
    motor: Any,
    start: float = -10.0,
    stop: float = 10.0,
    num: int = 21,
) -> Generator[Any, Any, Any]:
    """{{description}}

    Args:
        detectors: List of detectors to read at each point.
        motor: Motor to scan.
        start: Starting position.
        stop: Ending position.
        num: Number of points.

    Yields:
        Bluesky plan messages.
    """
    yield from bp.scan(detectors, motor, start, stop, num)
'''


class UserPlanService(QObject):
    """Service for loading and managing user-defined plans.

    User plans are loaded from ~/lightfall/plans/ directory. Each .py file
    in this directory that contains a callable `plan` variable is
    registered with the plan registry under the "user" category.

    The service uses a QFileSystemWatcher to automatically reload
    plans when files are modified.

    Signals:
        plan_loaded: Emitted when a plan is successfully loaded.
        plan_unloaded: Emitted when a plan is unloaded (name).
        plan_error: Emitted when a plan fails to load (path, error).
        plans_refreshed: Emitted after refresh_plans() completes.

    Example:
        >>> service = UserPlanService.get_instance()
        >>> service.load_all_plans()
        >>> service.create_new_plan("my_scan", "My custom scan plan")
    """

    _instance: ClassVar[UserPlanService | None] = None
    _lock = threading.RLock()

    plan_loaded = Signal(object)  # PlanInfo
    plan_unloaded = Signal(str)  # plan name
    plan_error = Signal(str, str)  # path, error message
    plans_refreshed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize the user plan service."""
        super().__init__(parent)
        self._plans_dir = Path.home() / "lightfall" / "plans"
        # name -> (file path, source label)
        self._loaded_plans: dict[str, tuple[Path, str]] = {}
        # synced plan candidates seen at the last load_all_plans, by stem
        self._synced_by_stem: dict[str, list[SourceItem]] = {}
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_directory_changed)

        # Ensure plans directory exists
        self._ensure_directory()

        logger.debug("UserPlanService initialized, plans dir: {}", self._plans_dir)

    @classmethod
    def get_instance(cls) -> UserPlanService:
        """Get the singleton UserPlanService instance."""
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
                cls._instance.deleteLater()
            cls._instance = None

    def _ensure_directory(self) -> bool:
        """Ensure the plans directory exists.

        Returns:
            True if directory exists or was created.
        """
        try:
            self._plans_dir.mkdir(parents=True, exist_ok=True)
            # Watch the directory for new/deleted files
            if str(self._plans_dir) not in self._watcher.directories():
                self._watcher.addPath(str(self._plans_dir))
            return True
        except Exception as e:
            logger.error("Failed to create plans directory: {}", e)
            return False

    def get_plans_directory(self) -> Path:
        """Get the user plans directory path.

        Returns:
            Path to ~/lightfall/plans/
        """
        return self._plans_dir

    def open_plans_folder(self) -> bool:
        """Open the plans folder in the system file explorer.

        Returns:
            True if successful.
        """
        import os
        import subprocess
        import sys

        self._ensure_directory()

        try:
            if sys.platform == "win32":
                os.startfile(str(self._plans_dir))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(self._plans_dir)], check=True)
            else:
                subprocess.run(["xdg-open", str(self._plans_dir)], check=True)
            return True
        except Exception as e:
            logger.error("Failed to open plans folder: {}", e)
            return False

    def load_all_plans(
        self, synced: list[SourceItem] | None = None
    ) -> list[tuple[Path, PlanInfo | Exception]]:
        """Load plans from synced scopes and the local plans directory.

        For each plan name (file stem) the most specific candidate wins
        (local > branch > beamline > facility); it is imported and registered.
        Losing candidates are not imported and are reported to the ClashLog.
        A winner whose name a core or package plan already owns is refused
        (also reported).

        Args:
            synced: Synced plan items; None reads this machine's content store.

        Returns:
            List of (path, PlanInfo or Exception) tuples.
        """
        from lightfall.acquire.plans.registry import PlanRegistry

        results: list[tuple[Path, PlanInfo | Exception]] = []

        if synced is None:
            synced = default_synced_items("plan")

        by_stem: dict[str, list[SourceItem]] = {}
        for item in synced:
            if item.type == "plan":
                by_stem.setdefault(item.name, []).append(item)
        self._synced_by_stem = by_stem

        local_files: dict[str, Path] = {}
        if self._plans_dir.exists():
            for py_file in self._plans_dir.glob("*.py"):
                if not py_file.name.startswith("_"):  # skip private files
                    local_files[py_file.stem] = py_file
        else:
            logger.debug("Plans directory does not exist: {}", self._plans_dir)

        registry = PlanRegistry.get_instance()
        clashes: list[Clash] = []

        for stem in list(by_stem) + [n for n in local_files if n not in by_stem]:
            candidates = self._candidates(stem, local_files.get(stem))
            winner = candidates[0]
            clashes.extend(
                Clash("plan", stem, winner.source, loser.source, False)
                for loser in candidates[1:]
            )
            result = self._load_plan_from_file(winner.path, registry, winner.source)
            if result is not None:
                results.append((winner.path, result))

        if clashes:
            ClashLog.get_instance().report(clashes)

        self.plans_refreshed.emit()
        logger.info("Loaded {} user plans from {}", len(self._loaded_plans), self._plans_dir)

        return results

    def _candidates(self, stem: str, local_path: Path | None) -> list[SourceItem]:
        """Candidates for ``stem``, most specific first (local, then synced scopes)."""
        items = list(self._synced_by_stem.get(stem, []))
        if local_path is not None:
            items.append(SourceItem("plan", stem, "local", local_path))
        return sorted(items, key=lambda i: specificity(i.source), reverse=True)

    def _load_synced_fallback(self, stem: str) -> None:
        """Load the most specific synced candidate for ``stem``, if there is one."""
        from lightfall.acquire.plans.registry import PlanRegistry

        candidates = self._candidates(stem, None)
        if candidates:
            self._load_plan_from_file(
                candidates[0].path, PlanRegistry.get_instance(), candidates[0].source
            )

    def _commit_change(self, path: Path, commit_msg: str | None) -> None:
        """Commit a change to ``path`` via the GitTracker singleton.

        Mirrors :meth:`UserPluginService._commit_change`. Called on every
        terminal path of :meth:`load_plan_from_file` where the file has been
        written to disk. Failed loads still commit -- the file's presence is
        forensic evidence even when the load itself didn't take.

        No-op if the path doesn't exist (delete paths handle their own
        ``commit_removal`` in the watcher slot). The tracker swallows all
        errors, so this never raises.
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

    def load_plan_from_file(
        self, path: Path, commit_msg: str | None = None,
    ) -> PlanInfo | None:
        """Load a single plan from a file.

        Args:
            path: Path to the Python file.
            commit_msg: Optional subject for the auto-commit. Defaults to
                ``"auto: updated <name>"`` when omitted. Commits happen
                regardless of load outcome -- failed loads land in history
                as forensic evidence.

        Returns:
            PlanInfo if successful, None otherwise.
        """
        from lightfall.acquire.plans.registry import PlanInfo, PlanRegistry

        registry = PlanRegistry.get_instance()
        result = self._load_plan_from_file(path, registry)

        # Commit regardless of load outcome -- failed loads stay in history
        # (forensics). The _commit_change helper guards against missing path.
        self._commit_change(path, commit_msg)

        if isinstance(result, PlanInfo):
            return result
        return None

    def _load_plan_from_file(
        self, path: Path, registry: Any, source: str = "local"
    ) -> PlanInfo | Exception | None:
        """Internal: Load a plan from file and register it.

        Args:
            path: Path to the Python file.
            registry: PlanRegistry to register with.
            source: Source label of the file ("local" or a synced scope).

        Returns:
            PlanInfo on success, Exception on error, None if no plan variable.
        """

        plan_name = path.stem

        # A core or package plan owns the name: refuse before running any code.
        existing = registry.get_plan(plan_name)
        if existing is not None and existing.source in ("core", "package"):
            ClashLog.get_instance().report(
                [Clash("plan", plan_name, existing.source, source, False)]
            )
            logger.warning(
                "Plan {} from {} refused: name belongs to a {} plan",
                plan_name, source, existing.source,
            )
            return None

        try:
            # Load the module
            spec = importlib.util.spec_from_file_location(
                f"lightfall_user_plans.{plan_name}", path
            )
            if spec is None or spec.loader is None:
                logger.warning("Could not load module spec for: {}", path)
                return None

            module = importlib.util.module_from_spec(spec)

            # Add to sys.modules temporarily for imports to work
            module_name = f"lightfall_user_plans.{plan_name}"
            sys.modules[module_name] = module

            try:
                spec.loader.exec_module(module)
            except SyntaxError as e:
                error_msg = f"Syntax error at line {e.lineno}: {e.msg}"
                logger.warning("Syntax error in {}: {}", path.name, error_msg)
                self.plan_error.emit(str(path), error_msg)
                return e
            except Exception as e:
                error_msg = str(e)
                logger.warning("Error loading {}: {}", path.name, error_msg)
                self.plan_error.emit(str(path), error_msg)
                return e

            # Check for plan variable
            if not hasattr(module, "plan"):
                logger.debug("No 'plan' variable in {}", path.name)
                return None

            plan_func = module.plan

            if not callable(plan_func):
                logger.warning("'plan' in {} is not callable", path.name)
                self.plan_error.emit(str(path), "'plan' is not callable")
                return ValueError("'plan' is not callable")

            # Reloading our own earlier copy of this plan: drop it first so the
            # new function replaces it instead of tying with it.
            previous = self._loaded_plans.pop(plan_name, None)
            if previous is not None:
                registry.unregister(plan_name)
                if previous[1] == "local" and previous[0] != path:
                    self._unwatch(previous[0])

            clash = registry.register_ranked(plan_name, plan_func, "user", source)
            if clash is not None:
                ClashLog.get_instance().report([clash])
                if clash.winner != source:
                    return None
            plan_info = registry.get_plan(plan_name)

            # Track loaded plan; only local files are watched
            self._loaded_plans[plan_name] = (path, source)
            if source == "local" and str(path) not in self._watcher.files():
                self._watcher.addPath(str(path))

            self.plan_loaded.emit(plan_info)
            logger.debug("Loaded user plan: {} from {}", plan_name, path.name)

            return plan_info

        except Exception as e:
            logger.error("Failed to load plan from {}: {}", path, e)
            self.plan_error.emit(str(path), str(e))
            return e

    def _unwatch(self, path: Path) -> None:
        if str(path) in self._watcher.files():
            self._watcher.removePath(str(path))

    def _unload_plan(self, name: str) -> bool:
        """Unload a plan by name.

        Args:
            name: Plan name.

        Returns:
            True if unloaded.
        """
        from lightfall.acquire.plans.registry import PlanRegistry

        if name not in self._loaded_plans:
            return False

        registry = PlanRegistry.get_instance()
        registry.unregister(name)

        path, _source = self._loaded_plans.pop(name)
        self._unwatch(path)

        # Remove from sys.modules
        module_name = f"lightfall_user_plans.{name}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        self.plan_unloaded.emit(name)
        logger.debug("Unloaded user plan: {}", name)

        return True

    def create_new_plan(
        self,
        name: str,
        description: str = "",
        commit_msg: str | None = None,
    ) -> Path:
        """Create a new plan file from template.

        Args:
            name: Plan name (will be used as filename).
            description: Optional plan description.
            commit_msg: Optional subject for the auto-commit. Forwarded to
                :meth:`load_plan_from_file`; defaults to
                ``"auto: updated <name>"`` when omitted.

        Returns:
            Path to the created file.

        Raises:
            ValueError: If name is invalid or file already exists.
            OSError: If file creation fails.
        """
        # Validate name is a valid Python identifier
        if not name.isidentifier():
            raise ValueError(f"'{name}' is not a valid Python identifier")

        self._ensure_directory()

        file_path = self._plans_dir / f"{name}.py"

        if file_path.exists():
            raise ValueError(f"Plan file already exists: {file_path}")

        # Generate content from template
        content = PLAN_TEMPLATE.replace("{{name}}", name)
        content = content.replace(
            "{{description}}", description if description else f"Custom plan: {name}"
        )

        # Write the file
        file_path.write_text(content, encoding="utf-8")
        logger.info("Created new plan file: {}", file_path)

        # Load the new plan immediately. Forwarding commit_msg ensures a single
        # commit happens with the user-supplied message.
        self.load_plan_from_file(file_path, commit_msg=commit_msg)

        return file_path

    def refresh_plans(self) -> None:
        """Refresh all user plans from disk.

        Unloads all current plans and reloads them from the directory.
        """
        # Unload all current plans
        for name in list(self._loaded_plans.keys()):
            self._unload_plan(name)

        # Reload all plans
        self.load_all_plans()

    def _is_local(self, name: str) -> bool:
        """True when the loaded plan ``name`` came from the local plans directory."""
        entry = self._loaded_plans.get(name)
        return entry is not None and entry[1] == "local"

    def _on_file_changed(self, path: str) -> None:
        """Handle file change notification.

        Args:
            path: Path to the changed file.
        """
        file_path = Path(path)
        if not file_path.exists():
            # File was deleted
            plan_name = file_path.stem
            if self._is_local(plan_name):
                self._unload_plan(plan_name)
                self._commit_removal(
                    file_path, f"external delete: {file_path.name}"
                )
                self._load_synced_fallback(plan_name)
                self.plans_refreshed.emit()
        else:
            # File was modified - reload it (commit happens in load_plan_from_file)
            self.load_plan_from_file(
                file_path, commit_msg=f"external edit: {file_path.name}"
            )
            self.plans_refreshed.emit()

        # Re-add the path to the watcher (Qt removes it after change)
        if file_path.exists() and str(path) not in self._watcher.files():
            self._watcher.addPath(path)

    def _on_directory_changed(self, path: str) -> None:
        """Handle directory change notification.

        Args:
            path: Path to the changed directory.
        """
        # Rescan for new/deleted files
        current_files = {
            p.stem for p in self._plans_dir.glob("*.py")
            if not p.name.startswith("_")
        }
        loaded_names = {n for n in self._loaded_plans if self._is_local(n)}

        # Load new files
        for name in current_files - loaded_names:
            file_path = self._plans_dir / f"{name}.py"
            self.load_plan_from_file(
                file_path, commit_msg=f"external add: {file_path.name}"
            )

        # Unload deleted files
        for name in loaded_names - current_files:
            path = self._loaded_plans[name][0]
            self._unload_plan(name)
            self._commit_removal(path, f"external delete: {path.name}")
            self._load_synced_fallback(name)

        self.plans_refreshed.emit()
