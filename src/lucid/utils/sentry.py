"""Sentry error reporting integration for LUCID.

Provides centralized error reporting to Sentry with automatic exception capture,
loguru integration, and context enrichment for Qt applications.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

# Sentry DSN - can be overridden via SENTRY_DSN environment variable
_DEFAULT_DSN = "https://3b73343435d03a70c396c72544e3b08f@o4510803909083136.ingest.us.sentry.io/4510803913998336"

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
) -> bool:
    """Initialize Sentry error reporting.

    This should be called once at application startup, ideally immediately
    after configuring logging. It integrates with loguru to automatically
    capture errors logged at ERROR level or above.

    Args:
        dsn: Sentry DSN. If None, uses SENTRY_DSN env var or built-in default.
            Set to empty string to disable Sentry.
        environment: Environment name (e.g., "development", "production").
            If None, auto-detects from NCS_AUTH env var.
        release: Release/version string. If None, attempts to get from package version.
        debug: Enable Sentry SDK debug mode for troubleshooting.
        sample_rate: Error sample rate (0.0 to 1.0). Default 1.0 captures all errors.
        traces_sample_rate: Performance tracing sample rate. Default 0.0 (disabled).
        profiles_sample_rate: Profiling sample rate. Default 0.0 (disabled).
        enable_loguru: Enable loguru integration to capture logged errors.

    Returns:
        True if Sentry was initialized successfully, False otherwise.
    """
    global _initialized

    if _initialized:
        return True

    # Resolve DSN
    resolved_dsn = dsn if dsn is not None else os.environ.get("SENTRY_DSN", _DEFAULT_DSN)
    if not resolved_dsn:
        # Empty string = explicitly disabled
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.loguru import LoguruIntegration
    except ImportError:
        # sentry-sdk not installed
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

    # Initialize Sentry
    sentry_sdk.init(
        dsn=resolved_dsn,
        environment=environment,
        release=release,
        debug=debug,
        sample_rate=sample_rate,
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        integrations=integrations,
        # Attach all threads for full context
        attach_stacktrace=True,
        # Include local variables in stack traces (helpful for debugging)
        include_local_variables=True,
        # Filter sensitive data
        before_send=_before_send,
    )

    # Add default context
    _set_default_context()

    _initialized = True
    return True


def _get_release_version() -> str | None:
    """Get the application release version."""
    try:
        from lucid._version import __version__

        return f"lucid@{__version__}"
    except ImportError:
        pass

    try:
        from importlib.metadata import version

        return f"lucid@{version('lucid')}"
    except Exception:
        pass

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
    # "2026-01-31 00:13:29.405 | ERROR    | module:func:line - message"
    # Made flexible to handle varying whitespace around pipes and dashes
    loguru_pattern = re.compile(
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*\|\s*\w+\s*\|\s*\S+\s+-\s+"
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
        # Check for user plans in lucid
        if "/lucid/acquire/plans/" in filename or "\\lucid\\acquire\\plans\\" in filename:
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
