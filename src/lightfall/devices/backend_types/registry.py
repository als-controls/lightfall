"""Registry of device backend types available to settings rows."""

from __future__ import annotations

from importlib.metadata import entry_points

from loguru import logger

from lightfall.devices.backend_types.base import BackendType

#: Entry-point group for plugin backend types. Each entry point names a
#: zero-argument callable (usually the class) returning a BackendType. It is
#: loaded before the device rows are built, so it must be cheap: no widgets,
#: no I/O.
ENTRY_POINT_GROUP = "lightfall.backend_types"


def _builtin_types() -> list[BackendType]:
    """The backend types Lightfall ships with, in Add-menu order."""
    from lightfall.devices.backend_types.bcs import BCSBackendType
    from lightfall.devices.backend_types.csm import CSMBackendType
    from lightfall.devices.backend_types.happi import HappiBackendType
    from lightfall.devices.backend_types.mock import MockBackendType

    return [MockBackendType(), BCSBackendType(), HappiBackendType(), CSMBackendType()]


def _entry_point_types() -> list[tuple[str, BackendType]]:
    """(entry-point name, type) for each plugin backend type, by name; bad ones skipped."""
    types: list[tuple[str, BackendType]] = []
    for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        try:
            backend_type = ep.load()()
        except Exception as exc:
            logger.warning("Backend type entry point '{}' failed to load: {}", ep.name, exc)
            continue
        if not isinstance(backend_type, BackendType):
            logger.warning(
                "Backend type entry point '{}' did not return a BackendType; skipped", ep.name
            )
            continue
        types.append((ep.name, backend_type))
    return types


class BackendTypeRegistry:
    """Singleton map of ``type_id`` -> :class:`BackendType`.

    Built-in types register on first use, then the types plugins publish in the
    ``lightfall.backend_types`` entry-point group. Code may also add types with
    ``BackendTypeRegistry.get_instance().register(MyType())``.
    """

    _instance: BackendTypeRegistry | None = None

    def __init__(self) -> None:
        self._types: dict[str, BackendType] = {}

    @classmethod
    def get_instance(cls) -> BackendTypeRegistry:
        if cls._instance is None:
            registry = cls()
            for backend_type in _builtin_types():
                registry.register(backend_type)
            for name, backend_type in _entry_point_types():
                try:
                    registry.register(backend_type)
                except ValueError as exc:
                    logger.warning("Backend type entry point '{}' skipped: {}", name, exc)
            cls._instance = registry
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton (tests)."""
        cls._instance = None

    def register(self, backend_type: BackendType) -> None:
        if backend_type.type_id in self._types:
            raise ValueError(f"Backend type '{backend_type.type_id}' is already registered")
        self._types[backend_type.type_id] = backend_type

    def get(self, type_id: str) -> BackendType | None:
        return self._types.get(type_id)

    def all(self) -> list[BackendType]:
        return list(self._types.values())
