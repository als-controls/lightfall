"""Registry of device backend types available to settings rows."""

from __future__ import annotations

from lightfall.devices.backend_types.base import BackendType


def _builtin_types() -> list[BackendType]:
    """The backend types Lightfall ships with, in Add-menu order."""
    from lightfall.devices.backend_types.bcs import BCSBackendType
    from lightfall.devices.backend_types.csm import CSMBackendType
    from lightfall.devices.backend_types.happi import HappiBackendType
    from lightfall.devices.backend_types.mock import MockBackendType

    return [MockBackendType(), BCSBackendType(), HappiBackendType(), CSMBackendType()]


class BackendTypeRegistry:
    """Singleton map of ``type_id`` -> :class:`BackendType`.

    Built-in types register on first use. Plugins add their own with
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
