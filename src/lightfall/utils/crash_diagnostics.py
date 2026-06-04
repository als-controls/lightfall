"""Crash diagnostics for Lightfall.

Hardens the application against silent native crashes by capturing as much
context as possible at fault time:

- ``faulthandler.enable(all_threads=True)`` writes Python tracebacks for every
  thread when the process receives a fatal signal (SIGSEGV, SIGFPE, etc).
- ``sys.excepthook`` and ``threading.excepthook`` log unhandled exceptions
  through loguru before delegating to the previously-installed hooks
  (Sentry's hooks remain in the chain — install order matters).
- A user signal handler dumps every thread's stack on demand
  (``SIGUSR1`` on POSIX, ``SIGBREAK`` on Windows / Ctrl-Break in a console).
- Qt's internal warnings are bridged to loguru via
  ``qInstallMessageHandler`` so thread-affinity violations and queued
  connection failures show up in the logs (and the Sentry pipeline) instead
  of vanishing into stderr.

The thread-affinity helpers (``assert_gui_thread``, ``@gui_thread_only``)
and shiboken6 wrapper-validity helpers (``safe_call``, ``valid_or_skip``)
live alongside the install routine because they are part of the same crash
prevention story: the assertions catch the offending call before it can
trigger an unrelated fault later, and the validity helpers prevent
use-after-delete on a deleted C++ object.

``install()`` must run BEFORE any PySide6 import. ``install_qt_bridge()``
runs after PySide6.QtCore is importable.
"""

from __future__ import annotations

import faulthandler
import os
import signal
import sys
import threading
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import IO, Any, TypeVar

__all__ = [
    "install",
    "install_qt_bridge",
    "dump_all_threads",
    "assert_gui_thread",
    "assert_object_thread",
    "gui_thread_only",
    "safe_call",
    "valid_or_skip",
]

F = TypeVar("F", bound=Callable[..., Any])


# Module state ---------------------------------------------------------------

_installed = False
_qt_bridge_installed = False
_fault_log_file: IO[str] | None = None
_diag_dir: Path | None = None

# Hooks captured at install() time so we can chain to whatever was previously
# installed (Sentry's excepthook gets installed AFTER us, which is fine — its
# hook will call ours as its "previous", and ours will call sys.__excepthook__).
_orig_excepthook: Callable[..., Any] | None = None
_orig_threading_excepthook: Callable[..., Any] | None = None


# Public API -----------------------------------------------------------------


def install(
    diagnostics_dir: Path | str | None = None,
    *,
    qt_logging_rules: str | None = None,
) -> Path:
    """Install crash diagnostics. Must be called before any PySide6 import.

    Idempotent — repeat calls return the original diagnostics directory
    without reinstalling hooks.

    Args:
        diagnostics_dir: Where to write segfault traces and on-demand
            thread dumps. Defaults to ``./logs/diagnostics/`` relative to
            the current working directory.
        qt_logging_rules: Value for ``QT_LOGGING_RULES``. Only applied if
            the environment variable is not already set. ``None`` leaves
            Qt's defaults in place.

    Returns:
        The resolved diagnostics directory path.
    """
    global _installed, _fault_log_file, _diag_dir
    global _orig_excepthook, _orig_threading_excepthook

    if _installed:
        assert _diag_dir is not None
        return _diag_dir

    diag_dir = (
        Path(diagnostics_dir)
        if diagnostics_dir is not None
        else Path.cwd() / "logs" / "diagnostics"
    )
    diag_dir.mkdir(parents=True, exist_ok=True)
    _diag_dir = diag_dir

    # 1. faulthandler — segfault tracebacks for every thread.
    #    Open the file unbuffered so a fatal signal doesn't lose the trailing
    #    bytes. Keep the handle on the module so it isn't garbage-collected.
    fault_path = diag_dir / "fault.log"
    _fault_log_file = open(fault_path, "a", buffering=1, encoding="utf-8")
    _fault_log_file.write(
        f"\n--- crash_diagnostics installed at {datetime.now().isoformat()} ---\n"
    )
    _fault_log_file.flush()
    faulthandler.enable(file=_fault_log_file, all_threads=True)

    # 2. On-demand all-thread dump.
    _register_on_demand_dump(_fault_log_file)

    # 3. Unhandled exceptions in the main thread.
    _orig_excepthook = sys.excepthook
    sys.excepthook = _excepthook

    # 4. Unhandled exceptions in worker threads (Python 3.8+).
    _orig_threading_excepthook = threading.excepthook
    threading.excepthook = _threading_excepthook  # type: ignore[assignment]

    # 5. Qt environment knobs. We deliberately do NOT set QT_FATAL_WARNINGS:
    #    on a beamline a stray Qt warning should not abort acquisition.
    #    Qt warnings are routed to loguru via install_qt_bridge() instead.
    if qt_logging_rules is not None:
        os.environ.setdefault("QT_LOGGING_RULES", qt_logging_rules)

    _installed = True
    return diag_dir


def install_qt_bridge() -> None:
    """Bridge Qt's message handler to loguru.

    Must be called after PySide6.QtCore is importable. Idempotent.
    Routes Qt debug/info/warning/critical/fatal messages through the
    existing loguru pipeline (and therefore through ErrorCollector and
    Sentry).
    """
    global _qt_bridge_installed
    if _qt_bridge_installed:
        return

    from PySide6.QtCore import qInstallMessageHandler

    qInstallMessageHandler(_qt_message_handler)
    _qt_bridge_installed = True


def dump_all_threads(file: IO[str] | None = None) -> None:
    """Dump every Python thread's current stack to ``file`` (default: stderr).

    Useful from a debugger or a panic button. The on-demand signal handler
    calls this implicitly.
    """
    out = file if file is not None else (sys.stderr or sys.stdout)
    if out is None:
        return
    print(
        f"\n=== thread dump at {datetime.now().isoformat()} "
        f"({len(threading.enumerate())} threads) ===",
        file=out,
    )
    frames = sys._current_frames()
    for thread in threading.enumerate():
        frame = frames.get(thread.ident)
        print(
            f"\n--- {thread.name!r} (ident={thread.ident}, "
            f"daemon={thread.daemon}, alive={thread.is_alive()}) ---",
            file=out,
        )
        if frame is None:
            print("  (no frame — thread has no Python state)", file=out)
            continue
        traceback.print_stack(frame, file=out)
    out.flush()


# Thread-affinity helpers ----------------------------------------------------


def assert_gui_thread(obj: Any | None = None) -> None:
    """Raise ``RuntimeError`` if not running on the QApplication's thread.

    Args:
        obj: Optional QObject. If supplied, its ``thread()`` is included
            in the error message — useful when a widget appears to belong
            to the wrong thread.
    """
    from PySide6.QtCore import QCoreApplication, QThread

    app = QCoreApplication.instance()
    if app is None:
        # No QApplication yet — affinity isn't meaningful. Be permissive.
        return

    current = QThread.currentThread()
    gui_thread = app.thread()
    if current is gui_thread:
        return

    py_thread = threading.current_thread()
    parts = [
        f"GUI thread assertion failed: called from "
        f"{py_thread.name!r} (QThread={current!r}), "
        f"expected GUI thread (QThread={gui_thread!r})",
    ]
    if obj is not None:
        try:
            obj_thread = obj.thread()
            parts.append(f"; object {obj!r}.thread()={obj_thread!r}")
        except Exception as e:
            parts.append(f"; object thread() raised: {e!r}")

    raise RuntimeError("".join(parts))


def assert_object_thread(obj: Any) -> None:
    """Raise ``RuntimeError`` if the current thread is not ``obj.thread()``.

    The mirror of ``assert_gui_thread`` for QObjects that legitimately
    live on a non-GUI thread (a worker, a background QObject moved to
    a QThread, etc).
    """
    from PySide6.QtCore import QThread

    obj_thread = obj.thread()
    current = QThread.currentThread()
    if current is obj_thread:
        return

    py_thread = threading.current_thread()
    raise RuntimeError(
        f"Object-thread assertion failed: called from "
        f"{py_thread.name!r} (QThread={current!r}), "
        f"expected {obj!r}.thread()={obj_thread!r}"
    )


def gui_thread_only(func: F) -> F:
    """Wrap a method/slot so it raises if called off the GUI thread.

    The wrapped callable is otherwise unchanged — ``@Slot`` decorators
    can be stacked above or below.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Pass the receiver (first positional, when wrapping a bound method)
        # to assert_gui_thread so its thread() is included in the error.
        receiver = args[0] if args else None
        assert_gui_thread(receiver)
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


# Wrapper-validity helpers ---------------------------------------------------


def _is_valid(obj: Any) -> bool:
    """Return True if shiboken6 considers the C++ wrapper alive.

    If shiboken6 is not importable (e.g., in a unit test that doesn't load
    PySide6), assume valid.
    """
    try:
        from shiboken6 import isValid

        return bool(isValid(obj))
    except ImportError:
        return True
    except Exception:
        return False


def safe_call(obj: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    """Invoke ``obj.method_name(*args, **kwargs)`` only if the wrapper is alive.

    Logs a warning with stack context if the underlying C++ object has been
    destroyed; returns ``None`` in that case rather than crashing.
    """
    if not _is_valid(obj):
        from lightfall.utils.logging import logger

        stack = "".join(traceback.format_stack()[:-1])
        logger.warning(
            "safe_call: wrapper for {!r}.{} is dead, skipping invocation\n{}",
            type(obj).__name__,
            method_name,
            stack,
        )
        return None

    method = getattr(obj, method_name)
    return method(*args, **kwargs)


@contextmanager
def valid_or_skip(obj: Any):
    """Context manager that yields the object only if its wrapper is alive.

    Yields ``None`` if the wrapper has been destroyed; the body sees
    ``None`` and is responsible for short-circuiting:

        with valid_or_skip(self.label) as label:
            if label is None:
                return
            label.setText("...")
    """
    if not _is_valid(obj):
        from lightfall.utils.logging import logger

        stack = "".join(traceback.format_stack()[:-2])
        logger.warning(
            "valid_or_skip: wrapper for {!r} is dead, skipping block\n{}",
            type(obj).__name__,
            stack,
        )
        yield None
        return
    yield obj


# Internals ------------------------------------------------------------------


def _register_on_demand_dump(file: IO[str]) -> None:
    """Register a signal that triggers an all-thread dump.

    POSIX uses SIGUSR1 (``kill -USR1 <pid>`` from another shell) via
    ``faulthandler.register`` for a C-level dump that works even when the
    GIL is held by C code. Windows lacks ``faulthandler.register`` (POSIX
    sigaction only), so we install a Python-level ``signal.signal`` handler
    on SIGBREAK (Ctrl-Break in the console) that calls ``dump_all_threads``.
    """
    if sys.platform == "win32":
        sig = getattr(signal, "SIGBREAK", None)
        if sig is None:
            return
        try:
            signal.signal(sig, lambda *_: dump_all_threads(file))
        except (ValueError, OSError):
            # signal.signal() can only run on the main thread; tests
            # importing from a worker would land here.
            pass
        return

    sig = getattr(signal, "SIGUSR1", None)
    if sig is None:
        return
    try:
        faulthandler.register(sig, file=file, all_threads=True, chain=True)
    except (ValueError, OSError, AttributeError):
        # AttributeError covers exotic builds where faulthandler.register
        # is missing despite running on POSIX.
        pass


def _excepthook(exc_type, exc_value, exc_traceback) -> None:
    """Log unhandled main-thread exceptions, then chain to the previous hook."""
    # KeyboardInterrupt should not be converted into noise.
    if issubclass(exc_type, KeyboardInterrupt):
        if _orig_excepthook is not None:
            _orig_excepthook(exc_type, exc_value, exc_traceback)
        return

    try:
        from lightfall.utils.logging import logger

        logger.opt(exception=(exc_type, exc_value, exc_traceback)).critical(
            "Unhandled exception in main thread: {}", exc_value
        )
    except Exception:
        # Logger isn't available yet — fall back to stderr so we lose nothing.
        traceback.print_exception(exc_type, exc_value, exc_traceback)

    if _orig_excepthook is not None:
        _orig_excepthook(exc_type, exc_value, exc_traceback)


def _threading_excepthook(args) -> None:
    """Log unhandled worker-thread exceptions, then chain to the previous hook."""
    if issubclass(args.exc_type, SystemExit):
        if _orig_threading_excepthook is not None:
            _orig_threading_excepthook(args)
        return

    try:
        from lightfall.utils.logging import logger

        thread_name = args.thread.name if args.thread is not None else "<unknown>"
        logger.opt(
            exception=(args.exc_type, args.exc_value, args.exc_traceback)
        ).critical(
            "Unhandled exception in thread {!r}: {}", thread_name, args.exc_value
        )
    except Exception:
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)

    if _orig_threading_excepthook is not None:
        _orig_threading_excepthook(args)


def _qt_message_handler(mode: Any, context: Any, message: str) -> None:
    """Route Qt messages through loguru.

    The handler must not raise — Qt invokes it from C++ and a Python
    exception escaping back into Qt is itself a fault source.
    """
    try:
        from PySide6.QtCore import QtMsgType

        from lightfall.utils.logging import logger

        # Map QtMsgType -> loguru level.
        level_map = {
            QtMsgType.QtDebugMsg: "DEBUG",
            QtMsgType.QtInfoMsg: "INFO",
            QtMsgType.QtWarningMsg: "WARNING",
            QtMsgType.QtCriticalMsg: "ERROR",
            QtMsgType.QtFatalMsg: "CRITICAL",
        }
        level = level_map.get(mode, "WARNING")

        # Prefix with file:line when Qt provides it (debug builds typically do;
        # release builds usually don't).
        location = ""
        try:
            file = getattr(context, "file", None)
            line = getattr(context, "line", 0)
            if file:
                location = f" [{file}:{line}]"
        except Exception:
            pass

        logger.log(level, "Qt: {}{}", message, location)
    except Exception:
        # Last resort: do not let a broken bridge crash Qt.
        try:
            sys.stderr.write(f"Qt (bridge failed): {message}\n")
        except Exception:
            pass
