"""LUCIDApplication - Central application singleton.

This module provides the main application class that coordinates
initialization, manages services, and controls the application lifecycle.

LUCID: Lightsource Unified Control Interface Dashboard
"""

from __future__ import annotations

import sys
import threading
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication

from lucid.core.services import ServiceRegistry
from lucid.utils.logging import configure_logging, logger

if TYPE_CHECKING:
    from pathlib import Path

    from PySide6.QtWidgets import QMainWindow


class ApplicationState(Enum):
    """Application lifecycle states."""

    UNINITIALIZED = auto()
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    SHUTTING_DOWN = auto()
    TERMINATED = auto()


class NCSEvent(QEvent):
    """Base class for NCS custom events.

    Use QCoreApplication.postEvent() to dispatch these events
    and install event filters to handle them.
    """

    _event_type_registry: dict[str, QEvent.Type] = {}

    @classmethod
    def register_type(cls, name: str) -> QEvent.Type:
        """Register a custom event type by name.

        Args:
            name: Unique name for the event type.

        Returns:
            The registered event type.
        """
        if name not in cls._event_type_registry:
            cls._event_type_registry[name] = QEvent.Type(QEvent.registerEventType())
        return cls._event_type_registry[name]

    def __init__(self, event_type: QEvent.Type, data: dict[str, Any] | None = None) -> None:
        super().__init__(event_type)
        self.data = data or {}


# Pre-registered event types
class NCSEventTypes:
    """Standard NCS event types."""

    CONFIG_CHANGED = NCSEvent.register_type("lucid.config.changed")
    SERVICE_REGISTERED = NCSEvent.register_type("lucid.service.registered")
    STATE_CHANGED = NCSEvent.register_type("lucid.state.changed")


class NCSApplication(QObject):
    """
    Central application singleton managing NCS lifecycle.

    NCSApplication coordinates:
    - Service registration and initialization
    - Configuration loading
    - Main window management
    - Application state transitions
    - Graceful shutdown

    The application follows an async initialization pattern:
    Config -> Services -> Plugins -> UI

    Example:
        >>> app = NCSApplication.get_instance()
        >>> app.initialize()
        >>> return app.run()
    """

    _instance: NCSApplication | None = None
    _lock = threading.RLock()

    def __init__(self, argv: list[str] | None = None) -> None:
        """
        Initialize NCSApplication.

        Args:
            argv: Command line arguments. If None, uses sys.argv.
        """
        super().__init__()
        self._state = ApplicationState.UNINITIALIZED
        self._qt_app: QApplication | None = None
        self._main_window: QMainWindow | None = None
        self._services = ServiceRegistry.get_instance()
        self._argv = argv if argv is not None else sys.argv

    @classmethod
    def get_instance(cls, argv: list[str] | None = None) -> NCSApplication:
        """
        Get the singleton NCSApplication instance.

        Args:
            argv: Command line arguments (only used on first call).

        Returns:
            The shared NCSApplication instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(argv)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """
        Reset the singleton instance.

        Primarily used for testing.
        """
        with cls._lock:
            if cls._instance is not None:
                cls._instance._shutdown()
            cls._instance = None
            ServiceRegistry.reset()

    @property
    def state(self) -> ApplicationState:
        """Current application state."""
        return self._state

    @property
    def services(self) -> ServiceRegistry:
        """Access to the service registry."""
        return self._services

    @property
    def qt_app(self) -> QApplication:
        """The Qt application instance."""
        if self._qt_app is None:
            raise RuntimeError("Qt application not initialized. Call initialize() first.")
        return self._qt_app

    @property
    def main_window(self) -> QMainWindow | None:
        """The main application window."""
        return self._main_window

    def _set_state(self, new_state: ApplicationState) -> None:
        """Change application state and post event."""
        old_state = self._state
        self._state = new_state
        logger.info("Application state: {} -> {}", old_state.name, new_state.name)

        # Post state change event
        if self._qt_app:
            event = NCSEvent(
                NCSEventTypes.STATE_CHANGED,
                {"old_state": old_state, "new_state": new_state},
            )
            QCoreApplication.postEvent(self, event)

    def initialize(
        self,
        *,
        log_level: str = "INFO",
        log_file: Path | str | None = None,
        config_paths: list[Path | str] | None = None,
    ) -> None:
        """
        Initialize the application.

        This sets up logging, creates the Qt application, loads configuration,
        and registers core services.

        Args:
            log_level: Minimum log level.
            log_file: Optional path to log file.
            config_paths: Additional configuration file paths.
        """
        if self._state != ApplicationState.UNINITIALIZED:
            logger.warning("Application already initialized, skipping")
            return

        self._set_state(ApplicationState.INITIALIZING)

        # Configure logging first
        configure_logging(level=log_level, log_file=log_file)
        logger.info("Initializing LUCID application")

        # Create Qt application
        self._qt_app = QApplication.instance()  # type: ignore[assignment]
        if self._qt_app is None:
            self._qt_app = QApplication(self._argv)

        self._qt_app.setApplicationName("LUCID")
        self._qt_app.setOrganizationName("ALS")
        self._qt_app.setOrganizationDomain("lbl.gov")

        # Register core services
        self._register_core_services(config_paths)

        self._set_state(ApplicationState.READY)
        logger.info("LUCID application initialized")

    def _register_core_services(
        self, config_paths: list[Path | str] | None = None
    ) -> None:
        """Register core application services."""
        # Import here to avoid circular imports
        from lucid.config.manager import ConfigManager

        # Register ConfigManager
        self._services.register(
            ConfigManager,
            lambda: ConfigManager(extra_paths=config_paths),
        )

        logger.debug("Core services registered")

    def set_main_window(self, window: QMainWindow) -> None:
        """
        Set the main application window.

        Args:
            window: The main window widget.
        """
        self._main_window = window
        logger.debug("Main window set: {}", window.__class__.__name__)

    def run(self) -> int:
        """
        Run the application event loop.

        Returns:
            The application exit code.
        """
        if self._state not in (ApplicationState.READY, ApplicationState.RUNNING):
            raise RuntimeError(
                f"Cannot run application in state {self._state.name}. "
                "Call initialize() first."
            )

        self._set_state(ApplicationState.RUNNING)

        # Show main window if set
        if self._main_window:
            self._main_window.show()

        logger.info("Starting application event loop")
        exit_code = self._qt_app.exec()

        self._shutdown()
        return exit_code

    def _shutdown(self) -> None:
        """Perform graceful shutdown."""
        if self._state == ApplicationState.TERMINATED:
            return

        self._set_state(ApplicationState.SHUTTING_DOWN)
        logger.info("Shutting down LUCID application")

        # Clear services
        self._services.clear()

        self._set_state(ApplicationState.TERMINATED)
        logger.info("LUCID application terminated")

    def quit(self, exit_code: int = 0) -> None:
        """
        Request application quit.

        Args:
            exit_code: Exit code to return from run().
        """
        if self._qt_app:
            self._qt_app.exit(exit_code)

    def post_event(self, receiver: QObject, event: NCSEvent) -> None:
        """
        Post an NCS event to a receiver.

        Args:
            receiver: The object to receive the event.
            event: The event to post.
        """
        QCoreApplication.postEvent(receiver, event)

    def broadcast_event(self, event: NCSEvent) -> None:
        """
        Broadcast an event to the application.

        Args:
            event: The event to broadcast.
        """
        if self._qt_app:
            QCoreApplication.postEvent(self._qt_app, event)
