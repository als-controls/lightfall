"""LFApplication - Central application singleton.

This module provides the main application class that coordinates
initialization, manages services, and controls the application lifecycle.
"""

from __future__ import annotations

import sys
import threading
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication

from lightfall.core.services import ServiceRegistry
from lightfall.ipc.service import IPCService
from lightfall.ipc.trust import TrustDialog, TrustManager, TrustState
from lightfall.utils.logging import configure_logging, logger

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


class LFEvent(QEvent):
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
class LFEventTypes:
    """Standard NCS event types."""

    CONFIG_CHANGED = LFEvent.register_type("lightfall.config.changed")
    SERVICE_REGISTERED = LFEvent.register_type("lightfall.service.registered")
    STATE_CHANGED = LFEvent.register_type("lightfall.state.changed")


class LFApplication(QObject):
    """
    Central application singleton managing NCS lifecycle.

    LFApplication coordinates:
    - Service registration and initialization
    - Configuration loading
    - Main window management
    - Application state transitions
    - Graceful shutdown

    The application follows an async initialization pattern:
    Config -> Services -> Plugins -> UI

    Example:
        >>> app = LFApplication.get_instance()
        >>> app.initialize()
        >>> return app.run()
    """

    _instance: LFApplication | None = None
    _lock = threading.RLock()

    def __init__(self, argv: list[str] | None = None) -> None:
        """
        Initialize LFApplication.

        Args:
            argv: Command line arguments. If None, uses sys.argv.
        """
        super().__init__()
        self._state = ApplicationState.UNINITIALIZED
        self._qt_app: QApplication | None = None
        self._main_window: QMainWindow | None = None
        self._services = ServiceRegistry.get_instance()
        self._argv = argv if argv is not None else sys.argv
        self._auth_dialog_active: bool = False

    @classmethod
    def get_instance(cls, argv: list[str] | None = None) -> LFApplication:
        """
        Get the singleton LFApplication instance.

        Args:
            argv: Command line arguments (only used on first call).

        Returns:
            The shared LFApplication instance.
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
            event = LFEvent(
                LFEventTypes.STATE_CHANGED,
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
        logger.info("Initializing Lightfall application")

        # NOTE: Windows AppUserModelID is set in main.py BEFORE any Qt imports.
        # It must be called before COM/Qt initialization for taskbar icon to work.

        # Create Qt application with Sentry exception capture
        self._qt_app = QApplication.instance()  # type: ignore[assignment]
        if self._qt_app is None:
            from lightfall.utils.sentry import create_sentry_application

            self._qt_app = create_sentry_application(self._argv)
        else:
            logger.warning(
                "QApplication already exists ({}), Sentry exception capture disabled",
                type(self._qt_app).__name__,
            )

        self._qt_app.setApplicationName("Lightfall")
        self._qt_app.setOrganizationName("ALS")
        self._qt_app.setOrganizationDomain("lbl.gov")
        self._qt_app.setDesktopFileName("gov.lbl.als.lightfall")

        # Set application icon
        from lightfall.resources import get_app_icon

        app_icon = get_app_icon()
        if not app_icon.isNull():
            self._qt_app.setWindowIcon(app_icon)
            sizes = app_icon.availableSizes()
            logger.info("App icon set on QApplication ({} sizes: {})", len(sizes), sizes)
        else:
            logger.warning("App icon is null, cannot set on QApplication")

        # Register core services
        self._register_core_services(config_paths)

        self._set_state(ApplicationState.READY)
        logger.info("Lightfall application initialized")

    def _register_core_services(
        self, config_paths: list[Path | str] | None = None
    ) -> None:
        """Register core application services."""
        # Import here to avoid circular imports
        from lightfall.config.manager import ConfigManager

        # Register ConfigManager
        self._services.register(
            ConfigManager,
            lambda: ConfigManager(extra_paths=config_paths),
        )

        # Register IPC trust manager and service
        trust_manager = TrustManager()
        self._services.register(TrustManager, lambda: trust_manager)
        self._services.register(
            IPCService, lambda: self._create_ipc_service(trust_manager)
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

    # ------------------------------------------------------------------
    # IPC helpers
    # ------------------------------------------------------------------

    def _create_ipc_service(self, trust_manager: TrustManager) -> IPCService:
        """Factory that builds a configured :class:`IPCService`."""
        from lightfall.ui.preferences.manager import PreferencesManager

        prefs = PreferencesManager.get_instance()
        nats_url = prefs.get("ipc_nats_url", "nats://bcgnats.als.private.lbl.gov:4222")
        topic_prefix = prefs.get("ipc_topic_prefix", "als.7011")
        svc = IPCService(nats_url=nats_url, topic_prefix=topic_prefix)
        svc.set_trust_manager(trust_manager)
        svc.register_meta_endpoints()
        return svc

    def _start_ipc(self) -> None:
        """Start the IPC service and register the auth handler."""
        ipc = self._services.get(IPCService)
        ipc.start()
        ipc.register_action(
            "auth.request",
            self._handle_ipc_auth_request,
            description="Trust handshake + token sharing",
        )
        logger.info("IPC service started")

        # Wire engine signals → IPC events & plan commands
        try:
            self._wire_engine_ipc()
            self._wire_plan_commands()
        except Exception:
            logger.exception("Failed to wire engine IPC (engine may not be initialized yet)")

        # Wire logbook + agent IPC commands
        try:
            self._wire_logbook_ipc()
        except Exception:
            logger.exception("Failed to wire logbook IPC")

        try:
            self._wire_agent_ipc()
        except Exception:
            logger.exception("Failed to wire agent IPC")

    def _wire_engine_ipc(self) -> None:
        """Connect engine signals to IPC events.

        Publishes ``runs.new``, ``runs.complete``, and ``state.engine``
        events so that external IPC clients can track run lifecycle and
        engine state changes.
        """
        from lightfall.acquire.engine import get_engine

        engine = get_engine()
        ipc = self._services.get(IPCService)

        current_run: dict[str, str] = {}

        def on_output(name: str, doc: dict) -> None:
            if name == "start":
                run_id = doc.get("uid", "")
                plan_name = doc.get("plan_name", "unknown")
                current_run["uid"] = run_id
                current_run["plan_name"] = plan_name
                ipc.publish(
                    ipc.topic("runs.new"),
                    {"run_id": run_id, "plan_name": plan_name},
                )

        def on_finish() -> None:
            run_id = current_run.get("uid", "")
            ipc.publish(
                ipc.topic("runs.complete"),
                {"run_id": run_id, "exit_status": "success"},
            )

        def on_abort() -> None:
            run_id = current_run.get("uid", "")
            ipc.publish(
                ipc.topic("runs.complete"),
                {"run_id": run_id, "exit_status": "abort"},
            )

        def on_exception(exc: Exception) -> None:
            run_id = current_run.get("uid", "")
            ipc.publish(
                ipc.topic("runs.complete"),
                {"run_id": run_id, "exit_status": "error"},
            )

        def on_state_changed(state: str) -> None:
            ipc.publish(ipc.topic("state.engine"), {"state": state})

        engine.sigOutput.connect(on_output)
        engine.sigFinish.connect(on_finish)
        engine.sigAbort.connect(on_abort)
        engine.sigException.connect(on_exception)
        engine.sigStateChanged.connect(on_state_changed)

        # Register outbound events in catalog
        ipc.register_event(
            "runs.new",
            description="Fired when a new run starts",
            schema={"run_id": "str", "plan_name": "str"},
        )
        ipc.register_event(
            "runs.complete",
            description="Fired when a run finishes",
            schema={"run_id": "str", "exit_status": "str"},
        )
        ipc.register_event(
            "state.engine",
            description="Engine state change",
            schema={"state": "str"},
        )

        logger.debug("Engine → IPC event wiring complete")

    def _wire_plan_commands(self) -> None:
        """Register IPC commands for plan execution.

        Registers ``commands.plan.run`` and ``commands.plan.abort`` so that
        external IPC clients can submit plans and abort the active run.
        """
        from lightfall.acquire.engine import get_engine

        engine = get_engine()
        ipc = self._services.get(IPCService)

        def handle_plan_run(subject: str, data: dict, reply: str | None) -> None:
            from lightfall.acquire.plans.registry import get_registry

            plan_name = data.get("plan_name")
            params = data.get("params", {})
            if not plan_name:
                if reply:
                    ipc.reply(reply, {"error": True, "message": "plan_name is required"})
                return

            registry = get_registry()
            plan_info = registry.get_plan(plan_name)
            if plan_info is None:
                if reply:
                    ipc.reply(
                        reply,
                        {"error": True, "message": f"Plan '{plan_name}' not found"},
                    )
                return

            try:
                plan_generator = plan_info.func(**params)
                proc_id = engine.submit(plan_generator, name=plan_name)
                if reply:
                    ipc.reply(
                        reply,
                        {"status": "submitted", "plan_name": plan_name, "procedure_id": proc_id},
                    )
            except Exception as exc:
                if reply:
                    ipc.reply(reply, {"error": True, "message": str(exc)})

        def handle_plan_abort(subject: str, data: dict, reply: str | None) -> None:
            reason = data.get("reason", "")
            try:
                engine.abort(reason=reason)
                if reply:
                    ipc.reply(reply, {"status": "abort_requested"})
            except Exception as exc:
                if reply:
                    ipc.reply(reply, {"error": True, "message": str(exc)})

        ipc.register_action(
            "commands.plan.run",
            handle_plan_run,
            description="Submit a plan to the BlueskyEngine",
            schema={"plan_name": "str", "params": "dict"},
        )
        ipc.register_action(
            "commands.plan.abort",
            handle_plan_abort,
            description="Abort the active run",
        )

        logger.debug("Plan IPC commands registered")

    def _wire_logbook_ipc(self) -> None:
        """Register IPC commands for logbook entry creation.

        Registers ``commands.logbook.add`` so that external IPC clients
        can create logbook entries and fragments via the local-first
        :class:`LogbookClient`.
        """
        from lightfall.logbook.client import LogbookClient

        ipc = self._services.get(IPCService)

        def handle_logbook_add(subject: str, data: dict, reply: str | None) -> None:
            title = data.get("title")
            content = data.get("content")
            tags = data.get("tags", [])

            client = LogbookClient.get_instance()

            # Determine the active logbook ID from the current user
            user_id: str | None = None
            try:
                from lightfall.auth.session import SessionManager

                sm = SessionManager.get_instance()
                user = sm.current_user
                if user and user.username:
                    user_id = user.username
            except Exception:
                pass

            if not user_id:
                if reply:
                    ipc.reply(reply, {"error": True, "message": "No active logbook (no user)"})
                return

            try:
                logbook_id = client.get_or_create_logbook(user_id)
            except Exception as exc:
                if reply:
                    ipc.reply(reply, {"error": True, "message": str(exc)})
                return

            entry_id = client.create_entry(logbook_id, title=title, tags=tags)
            if content:
                client.add_fragment(entry_id, content=content)

            if reply:
                ipc.reply(reply, {"status": "created", "entry_id": entry_id})

        ipc.register_action(
            "commands.logbook.add",
            handle_logbook_add,
            description="Create a logbook entry with optional content fragment",
            schema={"title": "str", "content": "str (optional)", "tags": "list[str]"},
        )

        logger.debug("Logbook IPC commands registered")

    def _wire_agent_ipc(self) -> None:
        """Register IPC commands for the Claude agent.

        Registers ``commands.agent.message`` so that external IPC clients
        can send messages to the :class:`QtClaudeAgent`.
        """
        ipc = self._services.get(IPCService)

        def handle_agent_message(subject: str, data: dict, reply: str | None) -> None:
            message = data.get("message", "")
            if not message:
                if reply:
                    ipc.reply(reply, {"error": True, "message": "message is required"})
                return

            # Find the agent via the main window widget tree
            agent = None
            if self._main_window:
                from lightfall.claude.widget import ClaudeAssistantWidget

                widget = self._main_window.findChild(ClaudeAssistantWidget)
                if widget and hasattr(widget, "agent"):
                    agent = widget.agent

            if agent is None:
                if reply:
                    ipc.reply(reply, {"error": True, "message": "Claude agent not available"})
                return

            agent.query_sync(message)
            if reply:
                ipc.reply(reply, {"status": "sent"})

        ipc.register_action(
            "commands.agent.message",
            handle_agent_message,
            description="Send a message to the Claude agent",
            schema={"message": "str"},
        )

        logger.debug("Agent IPC commands registered")

    def _handle_ipc_auth_request(
        self, subject: str, data: dict, reply: str | None
    ) -> None:
        """Handle an incoming IPC auth handshake request."""
        if not reply:
            return

        ipc = self._services.get(IPCService)
        app_name = data.get("app_name", "unknown")
        app_version = data.get("app_version", "")

        state = ipc.evaluate_trust(app_name)

        if state == TrustState.APPROVED:
            session = self._get_current_session()
            tiled_url = self._get_tiled_url()
            ipc.reply(
                reply,
                ipc.build_auth_response(
                    approved=True, session=session, tiled_url=tiled_url
                ),
            )
            return

        if state == TrustState.DENIED:
            ipc.reply(reply, ipc.build_auth_response(approved=False))
            return

        # Unknown app — show dialog with 60 s timeout
        if self._auth_dialog_active:
            ipc.reply(reply, ipc.build_auth_response(approved=False, reason="busy"))
            return

        trust = self._services.get(TrustManager)
        self._auth_dialog_active = True
        try:
            dialog = TrustDialog(app_name, app_version, parent=self._main_window)
            from PySide6.QtCore import QTimer

            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(dialog.reject)
            timer.start(60_000)

            if dialog.exec() == TrustDialog.DialogCode.Accepted:
                trust.approve(app_name)
                session = self._get_current_session()
                tiled_url = self._get_tiled_url()
                ipc.reply(
                    reply,
                    ipc.build_auth_response(
                        approved=True, session=session, tiled_url=tiled_url
                    ),
                )
            else:
                trust.deny(app_name)
                reason = "denied" if timer.isActive() else "timeout"
                ipc.reply(
                    reply,
                    ipc.build_auth_response(approved=False, reason=reason),
                )
        finally:
            self._auth_dialog_active = False

    def _get_current_session(self):
        """Return the current :class:`Session` or ``None``."""
        try:
            from lightfall.auth.session import SessionManager

            sm = SessionManager.get_instance()
            return sm.session
        except Exception:
            return None

    def _get_tiled_url(self) -> str:
        """Return the configured Tiled server URL."""
        try:
            from lightfall.ui.preferences.manager import PreferencesManager

            return PreferencesManager.get_instance().get("tiled_url", "")
        except Exception:
            return ""

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

        # Start IPC service (after main window is visible)
        try:
            self._start_ipc()
        except Exception:
            logger.exception("Failed to start IPC service")

        logger.info("Starting application event loop")
        exit_code = self._qt_app.exec()

        self._shutdown()
        return exit_code

    def _shutdown(self) -> None:
        """Perform graceful shutdown."""
        if self._state == ApplicationState.TERMINATED:
            return

        self._set_state(ApplicationState.SHUTTING_DOWN)
        logger.info("Shutting down Lightfall application")

        # Stop IPC service before clearing the registry
        try:
            ipc = self._services.get(IPCService, None)
            if ipc:
                ipc.stop()
        except Exception:
            logger.exception("Error stopping IPC service")

        # Clear services
        self._services.clear()

        self._set_state(ApplicationState.TERMINATED)
        logger.info("Lightfall application terminated")

    def quit(self, exit_code: int = 0) -> None:
        """
        Request application quit.

        Args:
            exit_code: Exit code to return from run().
        """
        if self._qt_app:
            self._qt_app.exit(exit_code)

    def post_event(self, receiver: QObject, event: LFEvent) -> None:
        """
        Post an NCS event to a receiver.

        Args:
            receiver: The object to receive the event.
            event: The event to post.
        """
        QCoreApplication.postEvent(receiver, event)

    def broadcast_event(self, event: LFEvent) -> None:
        """
        Broadcast an event to the application.

        Args:
            event: The event to broadcast.
        """
        if self._qt_app:
            QCoreApplication.postEvent(self._qt_app, event)
