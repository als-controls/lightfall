"""Service registry providing dependency injection for NCS.

This module implements a simple service locator pattern for managing
application-wide services like configuration, authentication, and device catalogs.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, TypeVar, overload

from ncs.utils.logging import logger

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")


class ServiceNotFoundError(Exception):
    """Raised when a requested service is not registered."""


class ServiceAlreadyRegisteredError(Exception):
    """Raised when attempting to register a service that already exists."""


class ServiceRegistry:
    """
    Central registry for application services.

    The ServiceRegistry provides a dependency injection container that allows
    services to be registered and retrieved by type. It supports lazy initialization
    through factory functions.

    Thread-Safety:
        All operations are thread-safe via a reentrant lock.

    Example:
        >>> registry = ServiceRegistry()
        >>> registry.register(ConfigManager, lambda: ConfigManager())
        >>> config = registry.get(ConfigManager)
    """

    _instance: ServiceRegistry | None = None
    _lock = threading.RLock()

    def __init__(self) -> None:
        self._services: dict[type, Any] = {}
        self._factories: dict[type, Callable[[], Any]] = {}
        self._initializing: set[type] = set()

    @classmethod
    def get_instance(cls) -> ServiceRegistry:
        """
        Get the singleton ServiceRegistry instance.

        Returns:
            The shared ServiceRegistry instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """
        Reset the singleton instance.

        Primarily used for testing to ensure a clean state.
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance.clear()
            cls._instance = None

    def register(
        self,
        service_type: type[T],
        factory: Callable[[], T],
        *,
        replace: bool = False,
    ) -> None:
        """
        Register a service factory.

        The factory will be called lazily when the service is first requested.

        Args:
            service_type: The type used to identify the service.
            factory: A callable that creates the service instance.
            replace: If True, allow replacing an existing registration.

        Raises:
            ServiceAlreadyRegisteredError: If service already registered and
                replace is False.
        """
        with self._lock:
            if service_type in self._factories and not replace:
                raise ServiceAlreadyRegisteredError(
                    f"Service {service_type.__name__} is already registered"
                )
            self._factories[service_type] = factory
            # Clear any existing instance so the new factory is used
            self._services.pop(service_type, None)
            logger.debug("Registered service factory for {}", service_type.__name__)

    def register_instance(
        self,
        service_type: type[T],
        instance: T,
        *,
        replace: bool = False,
    ) -> None:
        """
        Register an already-instantiated service.

        Args:
            service_type: The type used to identify the service.
            instance: The service instance.
            replace: If True, allow replacing an existing registration.

        Raises:
            ServiceAlreadyRegisteredError: If service already registered and
                replace is False.
        """
        with self._lock:
            if service_type in self._services and not replace:
                raise ServiceAlreadyRegisteredError(
                    f"Service {service_type.__name__} is already registered"
                )
            self._services[service_type] = instance
            self._factories.pop(service_type, None)
            logger.debug("Registered service instance for {}", service_type.__name__)

    @overload
    def get(self, service_type: type[T]) -> T: ...

    @overload
    def get(self, service_type: type[T], default: T) -> T: ...

    @overload
    def get(self, service_type: type[T], default: None) -> T | None: ...

    def get(self, service_type: type[T], default: T | None = ...) -> T | None:  # type: ignore[assignment]
        """
        Get a service instance by type.

        If the service hasn't been instantiated yet, its factory will be called.

        Args:
            service_type: The type of service to retrieve.
            default: Default value if service not found. If not provided,
                raises ServiceNotFoundError.

        Returns:
            The service instance.

        Raises:
            ServiceNotFoundError: If service not found and no default provided.
            RuntimeError: If circular dependency detected.
        """
        with self._lock:
            # Return cached instance if available
            if service_type in self._services:
                return self._services[service_type]

            # Check for factory
            if service_type not in self._factories:
                if default is not ...:
                    return default
                raise ServiceNotFoundError(
                    f"Service {service_type.__name__} is not registered"
                )

            # Detect circular dependencies
            if service_type in self._initializing:
                raise RuntimeError(
                    f"Circular dependency detected while initializing {service_type.__name__}"
                )

            # Create instance
            self._initializing.add(service_type)
            try:
                logger.debug("Initializing service {}", service_type.__name__)
                instance = self._factories[service_type]()
                self._services[service_type] = instance
                return instance
            finally:
                self._initializing.discard(service_type)

    def has(self, service_type: type) -> bool:
        """
        Check if a service is registered.

        Args:
            service_type: The type to check.

        Returns:
            True if registered (factory or instance), False otherwise.
        """
        with self._lock:
            return service_type in self._services or service_type in self._factories

    def clear(self) -> None:
        """Clear all registered services and factories."""
        with self._lock:
            self._services.clear()
            self._factories.clear()
            self._initializing.clear()
            logger.debug("Cleared all services")

    def unregister(self, service_type: type) -> bool:
        """
        Unregister a service.

        Args:
            service_type: The type to unregister.

        Returns:
            True if the service was registered, False otherwise.
        """
        with self._lock:
            removed = False
            if service_type in self._services:
                del self._services[service_type]
                removed = True
            if service_type in self._factories:
                del self._factories[service_type]
                removed = True
            if removed:
                logger.debug("Unregistered service {}", service_type.__name__)
            return removed
