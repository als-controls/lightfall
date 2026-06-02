"""NCS utility modules."""

from lucid.utils.error_collector import ErrorCollector, get_error_collector
from lucid.utils.logging import configure_logging, log_time
from lucid.utils.sentry import (
    add_breadcrumb,
    capture_exception,
    capture_message,
    init_sentry,
    sentry_slot,
    set_context,
    set_tag,
    submit_bug_report,
)
from lucid.utils.threads import (
    QThreadFuture,
    QThreadFutureIterator,
    ThreadManager,
    get_thread_manager,
    invoke_in_main_thread,
    is_main_thread,
    iterator,
    method,
    thread_manager,
)

__all__ = [
    # Error collector
    "ErrorCollector",
    "get_error_collector",
    # Logging
    "configure_logging",
    "log_time",
    # Sentry
    "init_sentry",
    "capture_exception",
    "capture_message",
    "add_breadcrumb",
    "set_tag",
    "set_context",
    "sentry_slot",
    "submit_bug_report",
    # Threading
    "ThreadManager",
    "get_thread_manager",
    "thread_manager",
    "QThreadFuture",
    "QThreadFutureIterator",
    "method",
    "iterator",
    "invoke_in_main_thread",
    "is_main_thread",
]
