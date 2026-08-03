"""AgentSpecRegistry — loads agent definition files from core/beamline/user
scope directories with name shadowing (user > beamline > core).
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

from lightfall.agents.spec import AgentSpec, AgentSpecError, parse_agent_file
from lightfall.utils.logging import logger

DISABLED_AGENTS_PREF: str = "disabled_agents"
FORCED_ENABLED_AGENTS_PREF: str = "forced_enabled_agents"

_SCOPE_ORDER: tuple[str, ...] = ("core", "beamline", "user")


def user_agents_dir() -> Path:
    """Default user-scope agent definitions directory, created if missing."""
    path = Path.home() / "lightfall" / "agents"
    path.mkdir(parents=True, exist_ok=True)
    return path


class AgentSpecRegistry(QObject):
    """Singleton registry of AgentSpec definitions across scopes.

    Use AgentSpecRegistry.get_instance() to access. reset_instance() is for tests.
    """

    changed = Signal()

    _instance: AgentSpecRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self._scope_dirs: dict[str, list[Path]] = {"core": [], "beamline": [], "user": []}
        self._specs: dict[str, AgentSpec] = {}
        self._errors: list[tuple[Path, str]] = []
        self._watcher: QFileSystemWatcher | None = None

    @classmethod
    def get_instance(cls) -> AgentSpecRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register_scope_dir(self, scope: str, path: Path) -> None:
        if scope not in self._scope_dirs:
            raise ValueError(f"unknown scope '{scope}', must be one of {_SCOPE_ORDER}")
        self._scope_dirs[scope].append(path)
        self.reload()

    def reload(self) -> None:
        specs: dict[str, AgentSpec] = {}
        errors: list[tuple[Path, str]] = []
        for scope in _SCOPE_ORDER:
            for directory in self._scope_dirs[scope]:
                if not directory.is_dir():
                    continue
                for file_path in sorted(directory.glob("*.md")):
                    try:
                        spec = parse_agent_file(file_path, scope)
                    except AgentSpecError as e:
                        errors.append((file_path, str(e)))
                        logger.warning("agent spec rejected: {}: {}", file_path, e)
                        continue
                    if spec.name in specs:
                        logger.debug(
                            "agent '{}' from scope '{}' shadows previous definition from scope '{}'",
                            spec.name, scope, specs[spec.name].scope,
                        )
                    specs[spec.name] = spec
        self._specs = specs
        self._errors = errors
        self.changed.emit()

    def specs(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> AgentSpec | None:
        return self._specs.get(name)

    def errors(self) -> list[tuple[Path, str]]:
        return list(self._errors)

    def all_scope_files(self) -> dict[str, list[tuple[str, Path]]]:
        """Map spec name -> [(scope, path), ...] across every registered scope dir.

        Includes files that failed to parse (their `parse_agent_file` errors
        are tolerated here and the file's stem is used as a name proxy), so
        this reflects everything on disk, not just the winning specs -- used
        by the editor panel to compute shadow relationships and to attribute
        a scope to error rows.
        """
        by_name: dict[str, list[tuple[str, Path]]] = {}
        for scope in _SCOPE_ORDER:
            for directory in self._scope_dirs[scope]:
                if not directory.is_dir():
                    continue
                for file_path in sorted(directory.glob("*.md")):
                    try:
                        spec = parse_agent_file(file_path, scope)
                        name = spec.name
                    except AgentSpecError:
                        name = file_path.stem
                    by_name.setdefault(name, []).append((scope, file_path))
        return by_name

    def _read_list_pref(self, key: str) -> list[str] | None:
        """Read a list-valued preference. Returns None if unset/unreadable."""
        try:
            from lightfall.ui.preferences.manager import PreferencesManager
            prefs = PreferencesManager.get_instance()
            value = prefs.get(key)
            if value is None or isinstance(value, list):
                return value
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load {}: {}", key, e)
        return None

    def enabled_specs(self) -> list[AgentSpec]:
        """Specs enabled by current preferences, sorted by name."""
        disabled = set(self._read_list_pref(DISABLED_AGENTS_PREF) or [])
        forced_enabled = set(self._read_list_pref(FORCED_ENABLED_AGENTS_PREF) or [])
        _ = forced_enabled  # reserved for future default_enabled: false support
        result = [s for s in self.specs() if s.name not in disabled]
        result.sort(key=lambda s: s.name)
        return result

    def watch_user_dir(self) -> None:
        dirs = [str(d) for d in self._scope_dirs["user"] if d.is_dir()]
        if not dirs:
            return
        self._watcher = QFileSystemWatcher(dirs)
        self._watcher.fileChanged.connect(lambda _path: self.reload())
        self._watcher.directoryChanged.connect(lambda _path: self.reload())
