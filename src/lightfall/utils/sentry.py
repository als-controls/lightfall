"""Sentry error reporting integration for Lightfall.

Provides centralized error reporting to Sentry with automatic exception capture,
loguru integration, and context enrichment for Qt applications.

Telemetry is opt-in: it activates only when a DSN is explicitly configured via
the SENTRY_DSN environment variable or the 'telemetry_dsn' preference (env
wins). With no DSN configured, all reporting functions no-op.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

_initialized = False


def init_sentry(
    *,
    dsn: str | None = None,
    environment: str | None = None,
    release: str | None = None,
    debug: bool = False,
    sample_rate: float = 1.0,
    traces_sample_rate: float = 0.0,
    profiles_sample_rate: float = 0.0,
    enable_loguru: bool = True,
    proxy_url: str | None = None,
) -> bool:
    """Initialize Sentry error reporting.

    This should be called once at application startup, ideally immediately
    after configuring logging. It integrates with loguru to automatically
    capture errors logged at ERROR level or above.

    Args:
        dsn: Sentry DSN. If None, uses the SENTRY_DSN env var, falling back
            to the 'telemetry_dsn' preference. If no DSN is configured
            anywhere, telemetry stays disabled. Set to empty string to
            disable Sentry explicitly.
        environment: Environment name (e.g., "development", "production").
            If None, auto-detects from NCS_AUTH env var.
        release: Release/version string. If None, attempts to get from package version.
        debug: Enable Sentry SDK debug mode for troubleshooting.
        sample_rate: Error sample rate (0.0 to 1.0). Default 1.0 captures all errors.
        traces_sample_rate: Performance tracing sample rate. Default 0.0 (disabled).
        profiles_sample_rate: Profiling sample rate. Default 0.0 (disabled).
        enable_loguru: Enable loguru integration to capture logged errors.
        proxy_url: HTTP/SOCKS proxy URL (e.g., "socks5://localhost:1080").
            If None, auto-detects from ProxySettingsProvider.

    Returns:
        True if Sentry was initialized successfully, False otherwise.
    """
    global _initialized

    if _initialized:
        return True

    from lightfall.utils.logging import logger

    # Resolve DSN — telemetry is opt-in. An explicit dsn argument wins
    # (empty string = explicitly disabled), then the SENTRY_DSN env var,
    # then the 'telemetry_dsn' preference.
    if dsn is not None:
        resolved_dsn = dsn
    else:
        resolved_dsn = os.environ.get("SENTRY_DSN") or _get_preference_dsn()
    if not resolved_dsn:
        logger.info("telemetry disabled — no DSN configured")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.loguru import LoguruIntegration
    except ImportError:
        logger.debug("sentry-sdk not installed; telemetry disabled")
        return False

    # Auto-detect environment
    if environment is None:
        ncs_auth = os.environ.get("NCS_AUTH", "").lower()
        if ncs_auth == "local":
            environment = "development"
        else:
            environment = "production"

    # Get release version
    if release is None:
        release = _get_release_version()

    # Configure integrations
    integrations = []
    if enable_loguru:
        # Capture ERROR and above from loguru as Sentry events
        integrations.append(LoguruIntegration(level="ERROR"))

    # Resolve proxy - check ProxySettingsProvider if not explicitly provided
    resolved_proxy = proxy_url
    if resolved_proxy is None:
        resolved_proxy = _get_proxy_for_sentry(resolved_dsn)

    # Build init kwargs
    init_kwargs: dict[str, Any] = {
        "dsn": resolved_dsn,
        "environment": environment,
        "release": release,
        "debug": debug,
        "sample_rate": sample_rate,
        "traces_sample_rate": traces_sample_rate,
        "profiles_sample_rate": profiles_sample_rate,
        "integrations": integrations,
        # Attach all threads for full context
        "attach_stacktrace": True,
        # Include local variables in stack traces (helpful for debugging)
        "include_local_variables": True,
        # Filter sensitive data
        "before_send": _before_send,
    }

    # Add proxy if configured (Sentry uses http_proxy for HTTP DSNs)
    if resolved_proxy:
        init_kwargs["http_proxy"] = resolved_proxy
        init_kwargs["https_proxy"] = resolved_proxy

    # Initialize Sentry
    sentry_sdk.init(**init_kwargs)

    # Add default context
    _set_default_context()

    _initialized = True
    return True


def _get_preference_dsn() -> str | None:
    """Read the 'telemetry_dsn' preference, if the preferences system is up.

    Returns:
        The configured DSN, or None when unset or preferences are unavailable
        (e.g., early init or headless use).
    """
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        return PreferencesManager.get_instance().get("telemetry_dsn") or None
    except Exception:
        return None


def _get_release_version() -> str | None:
    """Get the application release version."""
    try:
        from lightfall._version import __version__

        return f"lightfall@{__version__}"
    except ImportError:
        pass

    try:
        from importlib.metadata import version

        return f"lightfall@{version('lightfall')}"
    except Exception:
        pass

    return None


def _get_proxy_for_sentry(dsn: str) -> str | None:
    """Get proxy URL for Sentry from application settings.

    Checks ProxySettingsProvider to see if a proxy should be used
    for the Sentry DSN URL.

    Args:
        dsn: The Sentry DSN URL.

    Returns:
        Proxy URL if configured, None otherwise.
    """
    from lightfall.utils.logging import logger

    try:
        from lightfall.ui.preferences.proxy_settings import ProxySettingsProvider

        proxy_url = ProxySettingsProvider.should_use_proxy_for_url(dsn)
        if proxy_url:
            logger.info("Sentry will use proxy: {}", proxy_url)
        return proxy_url
    except ImportError:
        # ProxySettingsProvider not available (e.g., during early init)
        return None
    except Exception:
        # Proxy detection failed, proceed without proxy
        return None


def _set_default_context() -> None:
    """Set default Sentry context with system/Qt information."""
    import sentry_sdk

    # Qt version info
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        sentry_sdk.set_context(
            "qt",
            {
                "pyside_version": pyside_version,
                "qt_version": qVersion(),
            },
        )
    except ImportError:
        pass

    # Python version
    import sys

    sentry_sdk.set_context(
        "runtime",
        {
            "name": "python",
            "version": sys.version,
        },
    )


def _before_send(event: Event, hint: Hint) -> Event | None:
    """Filter/modify events before sending to Sentry.

    Used to scrub sensitive data and filter out noise.
    """
    # Strip loguru formatting from messages
    # Format: "2024-01-15 10:30:45.123 | ERROR | module:func:line - actual message"
    _strip_loguru_formatting(event)

    # Filter out certain exception types that aren't actionable
    if "exc_info" in hint:
        exc_type, exc_value, tb = hint["exc_info"]
        # Filter keyboard interrupts
        if exc_type is KeyboardInterrupt:
            return None

        # Filter connection errors (expected when services are unavailable)
        # Check by name to avoid importing optional dependencies
        exc_name = exc_type.__name__
        if exc_name in (
            "ConnectError",  # httpx
            "ConnectTimeout",  # httpx
            "ConnectionRefusedError",  # stdlib
            "ConnectionResetError",  # stdlib
            "ConnectionError",  # stdlib base class
        ):
            return None

        # Filter errors from within Bluesky plans (user code, not system errors)
        if _is_from_bluesky_plan(tb):
            return None

    # Filter log-based events for expected service connection failures
    # (LoguruIntegration captures ERROR logs which may not have exc_info)
    if _is_expected_connection_failure(event):
        return None

    return event


def _strip_loguru_formatting(event: Event) -> None:
    """Strip loguru timestamp and level formatting from event messages.

    Modifies the event in place, searching all common message locations.

    Args:
        event: The Sentry event dict.
    """
    import re

    # Pattern matches loguru format with flexible whitespace:
    # "2026-01-31 00:13:29.405 | ERROR    | thread | module:func:line - message"
    # Made flexible to handle varying whitespace around pipes and dashes
    loguru_pattern = re.compile(
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*\|\s*\w+\s*\|"
        r"(?:\s*\S+\s*\|)?"  # optional thread name field
        r"\s*\S+\s+-\s+"
    )

    def strip_from_string(s: str) -> str:
        """Strip loguru formatting from a string."""
        if not s:
            return s
        return loguru_pattern.sub("", s)

    # Strip from main message
    if "message" in event and isinstance(event["message"], str):
        event["message"] = strip_from_string(event["message"])

    # Strip from logentry (can have both 'message' and 'formatted')
    if "logentry" in event and isinstance(event["logentry"], dict):
        if "message" in event["logentry"]:
            event["logentry"]["message"] = strip_from_string(event["logentry"]["message"])
        if "formatted" in event["logentry"]:
            event["logentry"]["formatted"] = strip_from_string(event["logentry"]["formatted"])

    # Strip from exception values (exception messages can contain loguru format)
    if "exception" in event and isinstance(event["exception"], dict):
        values = event["exception"].get("values", [])
        for exc_value in values:
            if isinstance(exc_value, dict) and "value" in exc_value:
                exc_value["value"] = strip_from_string(exc_value["value"])

    # Strip from extra fields (loguru may add extra context)
    if "extra" in event and isinstance(event["extra"], dict):
        for key in list(event["extra"].keys()):
            if isinstance(event["extra"][key], str):
                event["extra"][key] = strip_from_string(event["extra"][key])

    # Strip from breadcrumbs messages
    if "breadcrumbs" in event and isinstance(event["breadcrumbs"], dict):
        values = event["breadcrumbs"].get("values", [])
        for crumb in values:
            if isinstance(crumb, dict) and "message" in crumb:
                crumb["message"] = strip_from_string(crumb["message"])


def _is_from_bluesky_plan(tb: Any) -> bool:
    """Check if exception originated from within a Bluesky plan.

    Args:
        tb: The traceback object from the exception.

    Returns:
        True if the error came from within a plan execution.
    """
    import traceback

    if tb is None:
        return False

    # Walk the traceback looking for bluesky plan execution frames
    for frame_info in traceback.extract_tb(tb):
        filename = frame_info.filename
        # Check for bluesky package frames (plan execution internals)
        if "/bluesky/" in filename or "\\bluesky\\" in filename:
            return True
        # Check for user plans in lightfall
        if "/lightfall/acquire/plans/" in filename or "\\lightfall\\acquire\\plans\\" in filename:
            return True

    return False


def _is_expected_connection_failure(event: Event) -> bool:
    """Check if event is an expected service connection failure.

    These are expected during normal operation when optional services
    (Tiled, EPICS IOCs, etc.) are unavailable.

    Args:
        event: The Sentry event dict.

    Returns:
        True if this is an expected connection failure that should be filtered.
    """
    # Get the message from various possible locations
    message = ""
    if "message" in event:
        message = event["message"]
    elif "logentry" in event and "message" in event["logentry"]:
        message = event["logentry"]["message"]

    if not message:
        return False

    message_lower = message.lower()

    # Known expected connection failure patterns
    connection_error_patterns = (
        "connection could be made because the target machine actively refused",
        "no connection could be made",
        "connection refused",
        "connecterror",
        "connecttimeout",
        "[winerror 10061]",  # Windows connection refused
        "[errno 111]",  # Linux connection refused
        "[errno 61]",  # macOS connection refused
    )

    # Known service connection thread names (from QThreadFuture)
    service_thread_patterns = (
        "tiled_connect",
        "tiled_service",
        "epics_connect",
        "ioc_connect",
    )

    # Check for connection error messages
    for pattern in connection_error_patterns:
        if pattern in message_lower:
            return True

    # Check for service thread connection failures
    for pattern in service_thread_patterns:
        if pattern in message_lower:
            return True

    return False


def set_user(
    user_id: str | None = None,
    username: str | None = None,
    email: str | None = None,
    roles: Any = None,
) -> None:
    """Set the current user context for Sentry events.

    Call this when a user logs in to associate errors with user info.

    Args:
        user_id: Unique user identifier.
        username: Display username.
        email: User email address.
        roles: User roles (strings or enum values).
    """
    if not _initialized:
        return

    import sentry_sdk

    user_data: dict[str, Any] = {}
    if user_id:
        user_data["id"] = user_id
    if username:
        user_data["username"] = username
    if email:
        user_data["email"] = email

    if user_data:
        sentry_sdk.set_user(user_data)

    if roles:
        # Convert to strings (handles both str and enum values)
        role_strs = [r.name if hasattr(r, "name") else str(r) for r in roles]
        sentry_sdk.set_tag("user.roles", ",".join(role_strs))


def clear_user() -> None:
    """Clear user context (e.g., on logout)."""
    if not _initialized:
        return

    import sentry_sdk

    sentry_sdk.set_user(None)
    sentry_sdk.set_tag("user.roles", None)


def capture_exception(exception: BaseException | None = None) -> str | None:
    """Manually capture an exception and send to Sentry.

    Args:
        exception: The exception to capture. If None, captures the current exception.

    Returns:
        The Sentry event ID if captured, None otherwise.
    """
    if not _initialized:
        return None

    import sentry_sdk

    return sentry_sdk.capture_exception(exception)


def capture_message(message: str, level: str = "info") -> str | None:
    """Capture a message and send to Sentry.

    Args:
        message: The message to capture.
        level: Severity level ("debug", "info", "warning", "error", "fatal").

    Returns:
        The Sentry event ID if captured, None otherwise.
    """
    if not _initialized:
        return None

    import sentry_sdk

    return sentry_sdk.capture_message(message, level=level)


def add_breadcrumb(
    message: str,
    category: str = "log",
    level: str = "info",
    data: dict[str, Any] | None = None,
) -> None:
    """Add a breadcrumb to the current scope.

    Breadcrumbs are trail of events leading up to an error.

    Args:
        message: Breadcrumb message.
        category: Category (e.g., "ui", "http", "log").
        level: Severity level.
        data: Additional data to attach.
    """
    if not _initialized:
        return

    import sentry_sdk

    sentry_sdk.add_breadcrumb(
        message=message,
        category=category,
        level=level,
        data=data or {},
    )


def set_tag(key: str, value: str | None) -> None:
    """Set a tag on all future events.

    Tags are indexed and searchable in Sentry.

    Args:
        key: Tag name.
        value: Tag value (None to clear).
    """
    if not _initialized:
        return

    import sentry_sdk

    sentry_sdk.set_tag(key, value)


def set_context(name: str, data: dict[str, Any] | None) -> None:
    """Set a context on all future events.

    Contexts provide additional structured data.

    Args:
        name: Context name (e.g., "device", "acquisition").
        data: Context data (None to clear).
    """
    if not _initialized:
        return

    import sentry_sdk

    sentry_sdk.set_context(name, data)


class SentryQApplication:
    """Mixin or wrapper that adds Sentry exception capture to QApplication.

    Qt's event loop catches exceptions in slots/event handlers at the C++/Python
    boundary, preventing them from reaching sys.excepthook. By overriding notify(),
    we can catch these exceptions and report them to Sentry.

    Usage:
        # Option 1: As a mixin (preferred)
        class MyApp(SentryQApplication, QApplication):
            pass

        # Option 2: Use the create_application() helper
        app = create_sentry_application(sys.argv)
    """

    def notify(self, receiver: Any, event: Any) -> bool:
        """Override notify to catch exceptions in event handling.

        Exceptions raised in slots/event handlers are reported to Sentry and the
        logs, but are deliberately **not** re-raised. ``super().notify()`` is a
        C++ call; propagating a Python exception back out through Qt's C++
        event-dispatch frames is undefined behavior and can corrupt the
        interpreter's per-thread state, turning a recoverable handler error into
        a hard abort (``_PyThreadState_Attach: non-NULL old thread state``).
        Swallowing keeps the GUI alive and the event is reported as unhandled.

        ``SystemExit`` and ``KeyboardInterrupt`` are ``BaseException`` (not
        ``Exception``) so they are not caught here and still propagate normally.

        Args:
            receiver: The object receiving the event.
            event: The event being delivered.

        Returns:
            True if the event was handled, False otherwise (including when a
            handler raised and the exception was suppressed).
        """
        try:
            return super().notify(receiver, event)  # type: ignore[misc]
        except Exception:
            # Report to Sentry + logs, but do NOT re-raise (see docstring).
            capture_exception()
            try:
                from lightfall.utils.logging import logger

                logger.opt(exception=True).error(
                    "Unhandled exception in Qt event handler "
                    "(receiver={}, event={}); event suppressed to keep the GUI "
                    "alive.",
                    type(receiver).__name__,
                    type(event).__name__,
                )
            except Exception:
                pass
            return False


def create_sentry_application(argv: list[str] | None = None) -> Any:
    """Create a QApplication with Sentry exception capture.

    This dynamically creates a QApplication subclass with the SentryQApplication
    mixin, ensuring exceptions in Qt event handling are captured.

    Args:
        argv: Command line arguments. If None, uses sys.argv.

    Returns:
        A QApplication instance with Sentry integration.
    """
    import sys

    from PySide6.QtWidgets import QApplication

    class SentryApp(SentryQApplication, QApplication):
        """QApplication with Sentry exception capture."""

        pass

    return SentryApp(argv if argv is not None else sys.argv)


def sentry_slot(*args, **kwargs):
    """Decorator that wraps a Qt slot with Sentry exception capture.

    Use this instead of @Slot() to automatically capture exceptions to Sentry.
    Supports all the same arguments as PySide6's @Slot decorator.

    Example::

        from lightfall.utils.sentry import sentry_slot

        class MyWidget(QWidget):
            @sentry_slot()
            def on_button_clicked(self):
                # If this raises, it will be captured by Sentry
                do_something_risky()

            @sentry_slot(str, int)
            def on_data_received(self, name: str, value: int):
                process_data(name, value)

    The decorator:
    1. Wraps the slot in try/except
    2. Logs the exception via loguru (so ErrorCollector can capture it)
    3. Captures any exception to Sentry
    4. Re-raises the exception (so Qt still logs it to stderr)
    5. Applies PySide6's @Slot decorator with the same arguments
    """
    from functools import wraps

    from PySide6.QtCore import Slot

    from lightfall.utils.logging import logger

    def decorator(func):
        @wraps(func)
        def wrapper(*func_args, **func_kwargs):
            try:
                return func(*func_args, **func_kwargs)
            except Exception as e:
                # Log to loguru so ErrorCollector captures it for bug reporting
                logger.opt(exception=True).error(
                    "Exception in slot {}: {}", func.__name__, e
                )
                # Sentry will also capture via LoguruIntegration
                raise

        # Apply PySide6's Slot decorator
        return Slot(*args, **kwargs)(wrapper)

    return decorator


def submit_bug_report(
    description: str,
    error_record: Any = None,
    priority: str = "normal",
) -> str | None:
    """Submit a user bug report to Sentry/GlitchTip as a tagged issue.

    Since GlitchTip does not support Sentry's User Feedback API, bug reports
    are submitted as regular Sentry issues with special tags for filtering:
    - `bug_report=true` - Identifies user-submitted bug reports
    - `priority=<level>` - The user-selected priority level

    Filter in GlitchTip dashboard: `bug_report:true`

    Args:
        description: User-provided description of the bug.
        error_record: Optional ErrorRecord from ErrorCollector with details
            about a recent error to include in the report.
        priority: Priority level for the report. One of:
            - "low" -> Sentry "info" level
            - "normal" -> Sentry "warning" level
            - "high" -> Sentry "error" level
            - "critical" -> Sentry "fatal" level

    Returns:
        The Sentry event ID if captured, None otherwise.
    """
    if not _initialized:
        return None

    import sentry_sdk

    # Map priority strings to Sentry levels
    level_map = {
        "low": "info",
        "normal": "warning",
        "high": "error",
        "critical": "fatal",
    }
    level = level_map.get(priority.lower(), "warning")

    # Submit as a tagged issue using new_scope for isolation
    with sentry_sdk.new_scope() as scope:
        # Tags for filtering bug reports in GlitchTip
        scope.set_tag("bug_report", "true")
        scope.set_tag("priority", priority)

        # Add error context if an error record was provided
        if error_record is not None:
            scope.set_context(
                "reported_error",
                {
                    "timestamp": (
                        error_record.timestamp.isoformat()
                        if hasattr(error_record, "timestamp")
                        else None
                    ),
                    "level": getattr(error_record, "level", None),
                    "module": getattr(error_record, "module", None),
                    "function": getattr(error_record, "function", None),
                    "line": getattr(error_record, "line", None),
                    "message": getattr(error_record, "message", None),
                    "location": getattr(error_record, "location", None),
                },
            )

            # Add traceback as extra data if available
            exception_info = getattr(error_record, "exception_info", None)
            if exception_info:
                scope.set_extra("traceback", exception_info)

        # Capture the bug report as a message with appropriate level
        return sentry_sdk.capture_message(
            f"[Bug Report] {description}",
            level=level,
        )
