"""Registry of authentication-provider plugins (built-in and contributed)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from lightfall.plugins.auth_provider_plugin import AuthProviderPlugin


class AuthProviderRegistry:
    """Singleton registry of AuthProviderPlugin instances."""

    _instance: AuthProviderRegistry | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        self._providers: dict[str, AuthProviderPlugin] = {}

    @classmethod
    def get_instance(cls) -> AuthProviderRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def register(self, plugin: AuthProviderPlugin) -> None:
        if plugin.name in self._providers:
            logger.warning("Auth provider '{}' already registered, replacing", plugin.name)
        self._providers[plugin.name] = plugin
        logger.debug("Registered auth provider plugin: {}", plugin.name)

    def get(self, name: str) -> AuthProviderPlugin | None:
        return self._providers.get(name)

    def get_all(self) -> list[AuthProviderPlugin]:
        return sorted(self._providers.values(), key=lambda p: (p.priority, p.display_name))

    def get_names(self) -> list[str]:
        return list(self._providers.keys())

    def has(self, name: str) -> bool:
        return name in self._providers
